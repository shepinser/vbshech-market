"""Каталог, покупки и управление магазином. Все данные хранятся в Access."""
import hmac
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
import pyodbc
from db import BASE_DIR, connection, records
from catalog_data import categories, category_ids, enrich

STATUSES = {'created': 'Оформлен', 'shipping': 'В пути', 'ready': 'Готов к выдаче', 'issued': 'Выдан', 'cancelled': 'Отменён'}
ROLES = {'customer': 'Покупатель', 'staff': 'Сотрудник ПВЗ', 'admin': 'Администратор'}

class StockError(Exception):
    pass

def back(default='index'):
    target = request.form.get('next', '')
    return redirect(target if target.startswith('/') and not target.startswith('//') and '\\' not in target else url_for(default))

def feedback(message, **values):
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify(message=message, **values)
    flash(message)
    return back()

def catalog():
    q = request.args.get('q', '').strip()[:120]
    category = request.args.get('category', type=int)
    sort = request.args.get('sort', '')
    color = request.args.get('color', '').strip()[:40]
    size = request.args.get('size', '').strip()[:30]
    def price_arg(name):
        try:
            val = Decimal(request.args.get(name, '') or '0')
            return val if val.is_finite() and 0 <= val <= 100000000 else Decimal(0)
        except InvalidOperation:
            return Decimal(0)
    low, high = price_arg('min_price'), price_arg('max_price')
    if low and high and low > high:
        low, high = high, low
    with connection() as db:
        cats = categories(db)
        # Внешний ключ Products.CategoryID соединяет товар с его подкатегорией.
        sql = '''SELECT p.*, c.Title AS CategoryTitle FROM Products AS p
                 INNER JOIN Categories AS c ON p.CategoryID=c.ID WHERE p.IsActive=True'''
        params = []
        if q:
            sql += ' AND (p.Title LIKE ? OR p.Brand LIKE ?)'
            params += ['%' + q + '%'] * 2
        if category:
            ids = category_ids(cats, category)
            sql += ' AND p.CategoryID IN (' + ','.join('?' for _ in ids) + ')'
            params += ids
        if low:
            sql += ' AND p.Price>=?'
            params.append(low)
        if high:
            sql += ' AND p.Price<=?'
            params.append(high)
        if color or size or request.args.get('in_stock'):
            sql += ' AND p.ID IN (SELECT ProductID FROM ProductVariants WHERE Stock>0'
            if color:
                sql += ' AND ColorLabel=?'
                params.append(color)
            if size:
                sql += ' AND SizeLabel=?'
                params.append(size)
            sql += ')'
        sql += {'cheap': ' ORDER BY p.Price ASC', 'expensive': ' ORDER BY p.Price DESC', 'new': ' ORDER BY p.ID DESC'}.get(sort, ' ORDER BY p.ID')
        products = enrich(db, records(db.cursor().execute(sql, params)))
        colors = [r[0] for r in db.cursor().execute('SELECT DISTINCT ColorLabel FROM ProductVariants ORDER BY ColorLabel')]
        sizes = [r[0] for r in db.cursor().execute("SELECT DISTINCT SizeLabel FROM ProductVariants WHERE SizeLabel<>'Без размера' ORDER BY SizeLabel")]
    if sort == 'rating':
        products.sort(key=lambda p: p['rating'] or 0, reverse=True)
    selected = next((c for c in cats if c['ID'] == category), None)
    filtered = any([q, category, low, high, color, size, request.args.get('in_stock')])
    return render_template('index.html', products=products, categories=cats, q=q, category=category,
                           sort=sort, color=color, size=size, low=low, high=high, colors=colors,
                           sizes=sizes, selected=selected, filtered=filtered)

def cart_rows(db, user_id):
    # Access требует скобки для цепочки JOIN: корзина → товар → выбранный размер и цвет.
    return records(db.cursor().execute('''SELECT ci.ID AS ItemID, ci.Quantity, ci.VariantID,
        v.SizeLabel, v.ColorLabel, v.Stock, p.* FROM
        (CartItems AS ci INNER JOIN Products AS p ON ci.ProductID=p.ID)
        INNER JOIN ProductVariants AS v ON ci.VariantID=v.ID
        WHERE ci.UserID=? ORDER BY ci.ID''', user_id))

def event(cur, order_id, status):
    cur.execute('INSERT INTO OrderEvents (OrderID, ActorID, EventStatus, CreatedAt) VALUES (?, ?, ?, ?)',
                order_id, g.user['ID'], status, datetime.now())

def load_orders(db, condition, params):
    result = records(db.cursor().execute('''SELECT o.*, p.City, p.Address, p.Hours, u.DisplayName
        FROM (OrderHeaders AS o INNER JOIN PickupPoints AS p ON o.PickupID=p.ID)
        INNER JOIN Users AS u ON o.UserID=u.ID WHERE ''' + condition + ' ORDER BY o.ID DESC', params))
    for order in result:
        order['items'] = records(db.cursor().execute('SELECT * FROM OrderItems WHERE OrderID=?', order['ID']))
        order['events'] = records(db.cursor().execute('''SELECT e.*, u.DisplayName FROM OrderEvents AS e
            INNER JOIN Users AS u ON e.ActorID=u.ID WHERE e.OrderID=? ORDER BY e.ID''', order['ID']))
    return result

def save_uploads(files):
    files = [file for file in files if file.filename]
    if len(files) > 8:
        raise ValueError('За один раз можно загрузить до 8 фотографий.')
    output = BASE_DIR / 'static' / 'uploads'
    output.mkdir(exist_ok=True)
    paths = []
    # Проверяем содержимое изображения, не доверяя расширению или имени файла.
    for file in files:
        data = file.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('Одна фотография должна быть не больше 8 МБ.')
        from io import BytesIO
        try:
            image = Image.open(BytesIO(data))
            if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 25000000:
                raise ValueError('Используйте JPG, PNG или WebP не больше 25 мегапикселей.')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1600, 1600))
            paths.append((secrets.token_hex(16) + '.jpg', image))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError('Не удалось прочитать фотографию. Используйте JPG, PNG или WebP.')
    saved = []
    for name, image in paths:
        image.save(output / name, 'JPEG', quality=88)
        saved.append('uploads/' + name)
    return saved

def register_routes(app, login_required, role_required):
    @app.context_processor
    def shared():
        with connection() as db:
            cats = categories(db)
            point_id = g.user.get('SelectedPickupID') if g.user else session.get('selected_pickup_id')
            point = records(db.cursor().execute('SELECT * FROM PickupPoints WHERE ID=?', point_id)) if point_id else []
            points = records(db.cursor().execute('SELECT * FROM PickupPoints ORDER BY City, Address'))
        return {'statuses': STATUSES, 'roles': ROLES, 'nav_categories': cats, 'all_points': points,
                'all_cities': sorted({p['City'] for p in points} | {'Ижевск', 'Москва', 'Санкт-Петербург', 'Казань', 'Самара', 'Екатеринбург'}),
                'selected_point': point[0] if point else None,
                'selected_city': point[0]['City'] if point else (g.user.get('SelectedCity') if g.user else None) or session.get('selected_city', 'Ижевск')}

    @app.get('/api/suggestions')
    def suggestions():
        q = request.args.get('q', '').strip()[:80]
        if len(q) < 2:
            return jsonify([])
        with connection() as db:
            rows = records(db.cursor().execute('SELECT TOP 6 ID, Title, Brand FROM Products WHERE IsActive=True AND (Title LIKE ? OR Brand LIKE ?) ORDER BY ID', '%' + q + '%', '%' + q + '%'))
        return jsonify([{'title': r['Brand'] + ' / ' + r['Title'], 'url': url_for('product', product_id=r['ID'])} for r in rows])

    @app.get('/product/<int:product_id>')
    def product(product_id):
        with connection() as db:
            rows = records(db.cursor().execute('SELECT * FROM Products WHERE ID=? AND IsActive=True', product_id))
            if not rows:
                abort(404)
            p = enrich(db, rows)[0]
            reviews = records(db.cursor().execute('''SELECT r.*, u.DisplayName FROM ProductReviews AS r
                INNER JOIN Users AS u ON r.UserID=u.ID WHERE r.ProductID=? ORDER BY r.ID DESC''', product_id))
            can_review = False
            if g.user:
                can_review = bool(db.cursor().execute('''SELECT TOP 1 i.ID FROM OrderItems AS i
                    INNER JOIN OrderHeaders AS o ON i.OrderID=o.ID
                    WHERE o.UserID=? AND i.ProductID=? AND o.OrderStatus='issued' ''', g.user['ID'], product_id).fetchone())
            related = enrich(db, records(db.cursor().execute('SELECT TOP 4 * FROM Products WHERE CategoryID=? AND ID<>? AND IsActive=True ORDER BY ID', p['CategoryID'], product_id)))
        variant_id = request.args.get('variant', type=int)
        variant = next((v for v in p['variants'] if v['ID'] == variant_id), None) or next((v for v in p['variants'] if v['Stock'] > 0), p['variants'][0] if p['variants'] else None)
        return render_template('product.html', p=p, variant=variant, reviews=reviews, can_review=can_review, related=related)

    @app.route('/favorites')
    @login_required
    def favorites():
        with connection() as db:
            # Избранное связано одновременно с пользователем и товаром.
            products = enrich(db, records(db.cursor().execute('''SELECT p.* FROM Products AS p
                INNER JOIN Favorites AS f ON p.ID=f.ProductID WHERE f.UserID=? AND p.IsActive=True ORDER BY f.ID DESC''', g.user['ID'])))
        return render_template('favorites.html', products=products)

    @app.post('/favorites/<int:product_id>')
    @login_required
    def toggle_favorite(product_id):
        with connection() as db:
            cur = db.cursor()
            if not cur.execute('SELECT ID FROM Products WHERE ID=? AND IsActive=True', product_id).fetchone():
                abort(404)
            item = cur.execute('SELECT ID FROM Favorites WHERE UserID=? AND ProductID=?', g.user['ID'], product_id).fetchone()
            if item:
                cur.execute('DELETE FROM Favorites WHERE ID=?', item.ID)
            else:
                cur.execute('INSERT INTO Favorites (UserID, ProductID) VALUES (?, ?)', g.user['ID'], product_id)
        return feedback('Удалено из избранного' if item else 'Добавлено в избранное', active=not bool(item))

    @app.post('/product/<int:product_id>/review')
    @login_required
    def add_review(product_id):
        score = request.form.get('score', type=int)
        text = request.form.get('text', '').strip()
        if score not in range(1, 6) or not 5 <= len(text) <= 2000:
            flash('Поставьте оценку и напишите отзыв от 5 до 2000 символов.')
            return redirect(url_for('product', product_id=product_id) + '#reviews')
        with connection() as db:
            cur = db.cursor()
            allowed = cur.execute('''SELECT TOP 1 i.ID FROM OrderItems AS i INNER JOIN OrderHeaders AS o ON i.OrderID=o.ID
                WHERE o.UserID=? AND i.ProductID=? AND o.OrderStatus='issued' ''', g.user['ID'], product_id).fetchone()
            if not allowed:
                abort(403)
            old = cur.execute('SELECT ID FROM ProductReviews WHERE UserID=? AND ProductID=?', g.user['ID'], product_id).fetchone()
            if old:
                cur.execute('UPDATE ProductReviews SET Score=?, ReviewText=?, CreatedAt=? WHERE ID=?', score, text, datetime.now(), old.ID)
            else:
                cur.execute('INSERT INTO ProductReviews (UserID, ProductID, Score, ReviewText, CreatedAt) VALUES (?, ?, ?, ?, ?)', g.user['ID'], product_id, score, text, datetime.now())
        flash('Спасибо! Ваш отзыв опубликован.')
        return redirect(url_for('product', product_id=product_id) + '#reviews')

    @app.post('/cart/add/<int:product_id>')
    @login_required
    def add_to_cart(product_id):
        variant_id = request.form.get('variant_id', type=int)
        quantity = request.form.get('quantity', 1, type=int)
        if quantity is None or not 1 <= quantity <= 99:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            if not cur.execute('SELECT ID FROM Products WHERE ID=? AND IsActive=True', product_id).fetchone():
                abort(404)
            variants = records(cur.execute('SELECT * FROM ProductVariants WHERE ProductID=? ORDER BY ID', product_id))
            variant = next((v for v in variants if v['ID'] == variant_id), None) if variant_id else next((v for v in variants if v['Stock'] > 0), None)
            if not variant or variant['Stock'] < quantity:
                return feedback('Выбранный вариант закончился. Выберите другой размер или цвет.', error=True)
            item = cur.execute('SELECT ID, Quantity FROM CartItems WHERE UserID=? AND VariantID=?', g.user['ID'], variant['ID']).fetchone()
            new_qty = quantity + (item.Quantity if item else 0)
            if new_qty > min(99, variant['Stock']):
                return feedback('В корзине уже максимально доступное количество.', error=True)
            if item:
                cur.execute('UPDATE CartItems SET Quantity=? WHERE ID=?', new_qty, item.ID)
            else:
                cur.execute('INSERT INTO CartItems (UserID, ProductID, VariantID, Quantity) VALUES (?, ?, ?, ?)', g.user['ID'], product_id, variant['ID'], new_qty)
            count = cur.execute('SELECT SUM(Quantity) FROM CartItems WHERE UserID=?', g.user['ID']).fetchone()[0]
        return feedback('Товар добавлен в корзину', count=count)

    @app.route('/cart')
    @login_required
    def cart():
        with connection() as db:
            items = cart_rows(db, g.user['ID'])
            enrich(db, items)
            points = records(db.cursor().execute('SELECT * FROM PickupPoints ORDER BY City, Address'))
        total = sum(item['Price'] * item['Quantity'] for item in items)
        session.setdefault('checkout_token', secrets.token_hex(24))
        can_checkout = all(i['IsActive'] and i['Stock'] >= i['Quantity'] for i in items)
        return render_template('cart.html', items=items, total=total, points=points, can_checkout=can_checkout)

    @app.post('/cart/change/<int:item_id>')
    @login_required
    def change_cart(item_id):
        quantity = 0 if request.form.get('action') == 'remove' else request.form.get('quantity', type=int)
        if quantity is None or not 0 <= quantity <= 99:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            row = cur.execute('''SELECT c.ID, v.Stock FROM CartItems AS c INNER JOIN ProductVariants AS v ON c.VariantID=v.ID
                WHERE c.ID=? AND c.UserID=?''', item_id, g.user['ID']).fetchone()
            if not row:
                abort(404)
            if quantity == 0:
                cur.execute('DELETE FROM CartItems WHERE ID=?', item_id)
            elif quantity <= row.Stock:
                cur.execute('UPDATE CartItems SET Quantity=? WHERE ID=?', quantity, item_id)
            else:
                flash('Такого количества сейчас нет в наличии.')
        return redirect(url_for('cart'))

    @app.route('/pickup')
    def pickup():
        with connection() as db:
            points = records(db.cursor().execute('SELECT * FROM PickupPoints ORDER BY City, Address'))
        cities = sorted({p['City'] for p in points} | {'Ижевск', 'Москва', 'Санкт-Петербург', 'Казань', 'Самара', 'Екатеринбург'})
        return render_template('pickup.html', points=points, cities=cities)

    @app.post('/location')
    def select_location():
        point_id = request.form.get('pickup_id', type=int)
        city = request.form.get('city', '').strip()
        with connection() as db:
            if point_id:
                found = db.cursor().execute('SELECT City FROM PickupPoints WHERE ID=?', point_id).fetchone()
                if not found:
                    abort(400)
                city = found.City
            if not 2 <= len(city) <= 80:
                abort(400)
            if g.user:
                db.cursor().execute('UPDATE Users SET SelectedPickupID=?, SelectedCity=? WHERE ID=?', point_id, city, g.user['ID'])
        session['selected_city'] = city
        session['selected_pickup_id'] = point_id
        flash('Адрес получения сохранён.' if point_id else 'Город изменён. Теперь выберите пункт выдачи.')
        return back('pickup')

    @app.post('/checkout')
    @login_required
    def checkout():
        token = request.form.get('checkout_token', '')
        if not token or not hmac.compare_digest(session.get('checkout_token', ''), token):
            flash('Откройте корзину заново, чтобы оформить заказ.')
            return redirect(url_for('cart'))
        point_id = request.form.get('pickup_id', type=int)
        try:
            with connection() as db:
                cur = db.cursor()
                if not point_id or not cur.execute('SELECT ID FROM PickupPoints WHERE ID=?', point_id).fetchone():
                    flash('Выберите существующий пункт выдачи.')
                    return redirect(url_for('cart'))
                items = cart_rows(db, g.user['ID'])
                if not items:
                    flash('Корзина пуста.')
                    return redirect(url_for('cart'))
                total = sum(i['Quantity'] * i['Price'] for i in items)
                cur.execute('''INSERT INTO OrderHeaders (UserID, PickupID, CreatedAt, OrderStatus, PickupCode, TotalAmount, CheckoutToken)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''', g.user['ID'], point_id, datetime.now(), 'created', f'{secrets.randbelow(1000000):06d}', total, token)
                order_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                for item in items:
                    if not item['IsActive']:
                        raise StockError('Один из товаров больше не продаётся. Удалите его из корзины.')
                    # Условное обновление атомарно резервирует остаток: отрицательные значения невозможны.
                    cur.execute('UPDATE ProductVariants SET Stock=Stock-? WHERE ID=? AND Stock>=?', item['Quantity'], item['VariantID'], item['Quantity'])
                    if cur.rowcount != 1:
                        raise StockError('Остатки изменились. Обновите количество товаров в корзине.')
                    cur.execute('''INSERT INTO OrderItems (OrderID, ProductID, ProductTitle, UnitPrice, Quantity, VariantID, OptionsText)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''', order_id, item['ID'], item['Brand'] + ' / ' + item['Title'], item['Price'], item['Quantity'], item['VariantID'], item['SizeLabel'] + ' · ' + item['ColorLabel'])
                event(cur, order_id, 'created')
                cur.execute('DELETE FROM CartItems WHERE UserID=?', g.user['ID'])
                cur.execute('UPDATE Users SET SelectedPickupID=? WHERE ID=?', point_id, g.user['ID'])
        except StockError as error:
            flash(str(error))
            return redirect(url_for('cart'))
        except pyodbc.IntegrityError:
            flash('Этот заказ уже оформлен. Проверьте список заказов.')
            return redirect(url_for('orders'))
        session.pop('checkout_token', None)
        flash(f'Заказ №{order_id} оформлен. Оплата не списывается.')
        return redirect(url_for('orders'))

    @app.get('/orders')
    @login_required
    def orders():
        with connection() as db:
            result = load_orders(db, 'o.UserID=?', [g.user['ID']])
        return render_template('orders.html', orders=result, staff_mode=False, status_filter='', search='')

    @app.post('/orders/<int:order_id>/cancel')
    @login_required
    def cancel_order(order_id):
        with connection() as db:
            cur = db.cursor()
            cur.execute("UPDATE OrderHeaders SET OrderStatus='cancelled' WHERE ID=? AND UserID=? AND OrderStatus='created'", order_id, g.user['ID'])
            if cur.rowcount != 1:
                abort(403)
            for item in records(cur.execute('SELECT VariantID, Quantity FROM OrderItems WHERE OrderID=?', order_id)):
                if item['VariantID']:
                    cur.execute('UPDATE ProductVariants SET Stock=Stock+? WHERE ID=?', item['Quantity'], item['VariantID'])
            event(cur, order_id, 'cancelled')
        flash('Заказ отменён.')
        return redirect(url_for('orders'))

    @app.post('/orders/<int:order_id>/repeat')
    @login_required
    def repeat_order(order_id):
        added = 0
        with connection() as db:
            cur = db.cursor()
            if not cur.execute('SELECT ID FROM OrderHeaders WHERE ID=? AND UserID=?', order_id, g.user['ID']).fetchone():
                abort(403)
            items = records(cur.execute('SELECT * FROM OrderItems WHERE OrderID=?', order_id))
            for item in items:
                v = cur.execute('''SELECT v.ID, v.Stock FROM ProductVariants AS v INNER JOIN Products AS p ON v.ProductID=p.ID
                    WHERE v.ID=? AND p.IsActive=True''', item['VariantID'] or 0).fetchone()
                if not v or v.Stock <= 0:
                    continue
                row = cur.execute('SELECT ID, Quantity FROM CartItems WHERE UserID=? AND VariantID=?', g.user['ID'], v.ID).fetchone()
                qty = min(99, v.Stock, item['Quantity'] + (row.Quantity if row else 0))
                if row:
                    cur.execute('UPDATE CartItems SET Quantity=? WHERE ID=?', qty, row.ID)
                else:
                    cur.execute('INSERT INTO CartItems (UserID, ProductID, VariantID, Quantity) VALUES (?, ?, ?, ?)', g.user['ID'], item['ProductID'], v.ID, qty)
                added += 1
        flash('Доступные товары добавлены в корзину по текущим ценам.' if added else 'Товары из этого заказа сейчас недоступны.')
        return redirect(url_for('cart'))

    @app.get('/staff')
    @role_required('admin', 'staff')
    def staff():
        search = request.args.get('search', '').strip()[:20]
        status = request.args.get('status', '')
        condition, params = ('1=1', []) if g.user['UserRole'] == 'admin' else ('o.PickupID=?', [g.user['PickupID'] or 0])
        if search:
            condition += ' AND o.ID=?'
            params.append(int(search) if search.isdigit() and len(search) < 10 else 0)
        if status in STATUSES:
            condition += ' AND o.OrderStatus=?'
            params.append(status)
        with connection() as db:
            result = load_orders(db, condition, params)
        return render_template('orders.html', orders=result, staff_mode=True, status_filter=status, search=search)

    @app.post('/staff/orders/<int:order_id>')
    @role_required('admin', 'staff')
    def process_order(order_id):
        with connection() as db:
            cur = db.cursor()
            rows = records(cur.execute('SELECT * FROM OrderHeaders WHERE ID=?', order_id))
            if not rows:
                abort(404)
            order = rows[0]
            if g.user['UserRole'] != 'admin' and order['PickupID'] != g.user['PickupID']:
                abort(403)
            action = request.form.get('action')
            new_status = None
            if action == 'shipping' and order['OrderStatus'] == 'created' and g.user['UserRole'] == 'admin':
                new_status = 'shipping'
            elif action == 'ready' and order['OrderStatus'] in {'created', 'shipping'}:
                new_status = 'ready'
            elif action == 'issue' and order['OrderStatus'] == 'ready':
                if hmac.compare_digest(order['PickupCode'], request.form.get('code', '').strip()):
                    new_status = 'issued'
                else:
                    flash('Код получения неверный. Заказ не выдан.')
                    return redirect(url_for('staff'))
            if not new_status:
                flash('Это действие недоступно для текущего статуса заказа.')
            else:
                cur.execute('UPDATE OrderHeaders SET OrderStatus=? WHERE ID=? AND OrderStatus=?', new_status, order_id, order['OrderStatus'])
                if cur.rowcount == 1:
                    event(cur, order_id, new_status)
                flash('Статус заказа обновлён.')
        return redirect(url_for('staff'))

    @app.get('/admin')
    @role_required('admin')
    def admin():
        with connection() as db:
            users = records(db.cursor().execute('SELECT ID, DisplayName, Email, UserRole, PickupID FROM Users ORDER BY ID'))
            points = records(db.cursor().execute('SELECT * FROM PickupPoints ORDER BY ID'))
            cats = categories(db)
            products = enrich(db, records(db.cursor().execute('SELECT * FROM Products ORDER BY ID DESC')))
            stats = {name: db.cursor().execute(f'SELECT COUNT(*) FROM [{table}]').fetchone()[0] for name, table in [('users', 'Users'), ('orders', 'OrderHeaders'), ('points', 'PickupPoints')]}
        return render_template('admin.html', users=users, points=points, categories=cats, products=products, stats=stats)

    @app.post('/admin/pickup')
    @role_required('admin')
    def create_pickup():
        city, address, hours = [request.form.get(k, '').strip() for k in ['city', 'address', 'hours']]
        if not 2 <= len(city) <= 80 or not 4 <= len(address) <= 200 or not 3 <= len(hours) <= 80:
            flash('Заполните город, адрес и часы работы пункта.')
        else:
            with connection() as db:
                db.cursor().execute('INSERT INTO PickupPoints (City, Address, Hours) VALUES (?, ?, ?)', city, address, hours)
            flash('Пункт выдачи создан.')
        return redirect(url_for('admin') + '#points')

    @app.post('/admin/users/<int:user_id>')
    @role_required('admin')
    def change_role(user_id):
        role = request.form.get('role')
        point_id = request.form.get('pickup_id', type=int) if role == 'staff' else None
        if role not in ROLES:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            target = cur.execute('SELECT UserRole FROM Users WHERE ID=?', user_id).fetchone()
            if not target:
                abort(404)
            if role == 'staff' and (not point_id or not cur.execute('SELECT ID FROM PickupPoints WHERE ID=?', point_id).fetchone()):
                flash('Для сотрудника выберите пункт выдачи.')
            elif target.UserRole == 'admin' and role != 'admin' and cur.execute("SELECT COUNT(*) FROM Users WHERE UserRole='admin'").fetchone()[0] <= 1:
                flash('Нельзя убрать права у последнего администратора.')
            else:
                cur.execute('UPDATE Users SET UserRole=?, PickupID=? WHERE ID=?', role, point_id, user_id)
                flash('Права пользователя обновлены.')
        return redirect(url_for('admin') + '#users')

    @app.post('/admin/categories')
    @role_required('admin')
    def create_category():
        title = request.form.get('title', '').strip()
        parent = request.form.get('parent_id', type=int)
        if not 2 <= len(title) <= 80:
            flash('Название категории должно содержать от 2 до 80 символов.')
        else:
            with connection() as db:
                if parent and not db.cursor().execute('SELECT ID FROM Categories WHERE ID=? AND ParentID IS NULL', parent).fetchone():
                    abort(400)
                db.cursor().execute('INSERT INTO Categories (Title, ParentID) VALUES (?, ?)', title, parent)
            flash('Категория добавлена.')
        return redirect(url_for('admin') + '#categories')

    @app.route('/admin/products/new', methods=['GET', 'POST'])
    @app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
    @role_required('admin')
    def edit_product(product_id=None):
        with connection() as db:
            cats = categories(db)
            old = records(db.cursor().execute('SELECT * FROM Products WHERE ID=?', product_id)) if product_id else []
            if product_id and not old:
                abort(404)
            p = enrich(db, old)[0] if old else None
        if request.method == 'GET':
            return render_template('edit_product.html', p=p, categories=cats)
        try:
            title, brand = request.form.get('title', '').strip(), request.form.get('brand', '').strip()
            category_id = request.form.get('category_id', type=int)
            if not 2 <= len(title) <= 150 or not 1 <= len(brand) <= 80 or category_id not in {c['ID'] for c in cats}:
                raise ValueError('Проверьте название, бренд и категорию.')
            try:
                price = Decimal(request.form.get('price', '').replace(',', '.'))
                old_price = Decimal(request.form.get('old_price', '').replace(',', '.') or price)
                if not price.is_finite() or not old_price.is_finite() or not 0 < price <= old_price <= 100000000 or price != price.quantize(Decimal('0.01')) or old_price != old_price.quantize(Decimal('0.01')):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise ValueError('Укажите положительную цену до 100 000 000 ₽ и не более двух знаков после запятой. Старая цена должна быть не меньше текущей.')
            description = request.form.get('description', '').strip()[:10000]
            specifications = request.form.get('specifications', '').strip()[:10000]
            active = request.form.get('active') == 'on'
            sizes, colors, stocks, variant_ids = [request.form.getlist(key) for key in ['variant_size', 'variant_color', 'variant_stock', 'variant_id']]
            if not 1 <= len(sizes) <= 80 or not len(sizes) == len(colors) == len(stocks) == len(variant_ids):
                raise ValueError('Добавьте хотя бы один размер и цвет с остатком.')
            variants = []
            for size, color, stock, vid in zip(sizes, colors, stocks, variant_ids):
                if not 1 <= len(size.strip()) <= 30 or not 1 <= len(color.strip()) <= 40 or not stock.isdigit() or not 0 <= int(stock) <= 1000000:
                    raise ValueError('Заполните размер, цвет и целый остаток от 0 до 1 000 000.')
                variants.append((int(vid) if vid.isdigit() else None, size.strip(), color.strip(), int(stock)))
            if len({(v[1], v[2]) for v in variants}) != len(variants):
                raise ValueError('Одинаковые сочетания размера и цвета не должны повторяться.')
            ids = [v[0] for v in variants if v[0]]
            if len(ids) != len(set(ids)):
                raise ValueError('Один вариант товара указан несколько раз.')
            images = save_uploads(request.files.getlist('photos'))
            with connection() as db:
                cur = db.cursor()
                if product_id:
                    cur.execute('UPDATE Products SET CategoryID=?, Brand=?, Title=?, Price=?, OldPrice=?, Description=?, Specifications=?, IsActive=? WHERE ID=?',
                                category_id, brand, title, price, old_price, description, specifications, active, product_id)
                else:
                    cur.execute('''INSERT INTO Products (CategoryID, Brand, Title, Price, OldPrice, Rating, Reviews, Picture, Tint, Description, Specifications, IsActive)
                        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)''', category_id, brand, title, price, old_price, 'shirt', '#f4f3f6', description, specifications, active)
                    product_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                current_ids = {r.ID for r in cur.execute('SELECT ID FROM ProductVariants WHERE ProductID=?', product_id)}
                for vid, size, color, stock in variants:
                    if vid and vid not in current_ids:
                        raise ValueError('Вариант товара не принадлежит этой карточке.')
                    if vid:
                        cur.execute('UPDATE ProductVariants SET SizeLabel=?, ColorLabel=?, Stock=? WHERE ID=?', size, color, stock, vid)
                    else:
                        cur.execute('INSERT INTO ProductVariants (ProductID, SizeLabel, ColorLabel, Stock) VALUES (?, ?, ?, ?)', product_id, size, color, stock)
                # Существующие варианты не удаляются: исторические заказы сохраняют свои связи.
                for missing_id in current_ids - {v[0] for v in variants if v[0]}:
                    cur.execute('UPDATE ProductVariants SET Stock=0 WHERE ID=?', missing_id)
                if images and request.form.get('replace_photos') == 'on':
                    cur.execute('DELETE FROM ProductImages WHERE ProductID=?', product_id)
                maximum = cur.execute('SELECT MAX(SortOrder) FROM ProductImages WHERE ProductID=?', product_id).fetchone()[0] or 0
                for number, image in enumerate(images, maximum + 1):
                    cur.execute('INSERT INTO ProductImages (ProductID, ImagePath, SortOrder) VALUES (?, ?, ?)', product_id, image, number)
            flash('Карточка товара сохранена.')
            return redirect(url_for('admin') + '#products')
        except ValueError as error:
            flash(str(error))
            return render_template('edit_product.html', p=p, categories=cats), 400

    @app.post('/admin/products/<int:product_id>/visibility')
    @role_required('admin')
    def product_visibility(product_id):
        with connection() as db:
            cur = db.cursor()
            row = cur.execute('SELECT IsActive FROM Products WHERE ID=?', product_id).fetchone()
            if not row:
                abort(404)
            cur.execute('UPDATE Products SET IsActive=? WHERE ID=?', not row.IsActive, product_id)
        flash('Видимость товара изменена. История заказов сохранена.')
        return redirect(url_for('admin') + '#products')

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('error.html', code=403, message='Эта страница доступна только пользователям с нужной ролью.'), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', code=404, message='Страница не найдена. Возможно, товар больше не продаётся.'), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template('error.html', code=413, message='Фотографии слишком большие. Общий размер загрузки — до 24 МБ.'), 413

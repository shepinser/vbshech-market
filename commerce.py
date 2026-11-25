"""Каталог, покупки и управление магазином. Все данные хранятся в Access."""
import hmac
import secrets
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
import pyodbc
from db import BASE_DIR, connection, records
from catalog_data import categories, category_ids, enrich, search_items, search_key

STATUSES = {'created': 'Оформлен', 'shipping': 'В пути', 'ready': 'Готов к выдаче', 'issued': 'Выдан', 'return_requested': 'Ожидает возврата', 'returned': 'Возвращён', 'cancelled': 'Отменён'}
ROLES = {'customer': 'Покупатель', 'staff': 'Сотрудник ПВЗ', 'admin': 'Администратор', 'supplier': 'Поставщик'}

class StockError(Exception):
    pass

def restore_stock(cur, order_id):
    # Вызывается только после успешного условного перехода статуса в той же транзакции.
    items = records(cur.execute('SELECT КодВарианта, Количество FROM СоставЗаказов WHERE КодЗаказа=?', order_id))
    for item in items:
        if item['КодВарианта']:
            cur.execute('UPDATE ВариантыТоваров SET Остаток=Остаток+? WHERE Код=?', item['Количество'], item['КодВарианта'])

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
        # Внешний ключ Товары.КодКатегории соединяет товар с его подкатегорией.
        sql = '''SELECT p.*, c.Название AS CategoryTitle FROM Товары AS p
                 INNER JOIN Категории AS c ON p.КодКатегории=c.Код WHERE p.Активен=True'''
        params = []
        if q:
            found = [item['id'] for item in search_items(db) if search_key(q) in item['key']]
            sql += ' AND p.Код IN (' + ','.join('?' for _ in found) + ')' if found else ' AND 1=0'
            params += found
        if category:
            ids = category_ids(cats, category)
            sql += ' AND p.КодКатегории IN (' + ','.join('?' for _ in ids) + ')'
            params += ids
        if low:
            sql += ' AND p.Цена>=?'
            params.append(low)
        if high:
            sql += ' AND p.Цена<=?'
            params.append(high)
        if color or size or request.args.get('in_stock'):
            sql += ' AND p.Код IN (SELECT КодТовара FROM ВариантыТоваров WHERE Остаток>0'
            if color:
                sql += ' AND Цвет=?'
                params.append(color)
            if size:
                sql += ' AND Размер=?'
                params.append(size)
            sql += ')'
        sql += {'cheap': ' ORDER BY p.Цена ASC', 'expensive': ' ORDER BY p.Цена DESC', 'new': ' ORDER BY p.Код DESC'}.get(sort, ' ORDER BY p.Код')
        products = enrich(db, records(db.cursor().execute(sql, params)))
        colors = [r[0] for r in db.cursor().execute('SELECT DISTINCT Цвет FROM ВариантыТоваров ORDER BY Цвет')]
        sizes = [r[0] for r in db.cursor().execute("SELECT DISTINCT Размер FROM ВариантыТоваров WHERE Размер<>'Без размера' ORDER BY Размер")]
    if sort == 'rating':
        products.sort(key=lambda p: p['rating'] or 0, reverse=True)
    selected = next((c for c in cats if c['Код'] == category), None)
    filtered = any([q, category, low, high, color, size, request.args.get('in_stock')])
    return render_template('index.html', products=products, categories=cats, q=q, category=category,
                           sort=sort, color=color, size=size, low=low, high=high, colors=colors,
                           sizes=sizes, selected=selected, filtered=filtered)

def cart_rows(db, user_id):
    # Access требует скобки для цепочки JOIN: корзина → товар → выбранный размер и цвет.
    return records(db.cursor().execute('''SELECT ci.Код AS ItemID, ci.Количество, ci.КодВарианта,
        v.Размер, v.Цвет, v.Остаток, p.* FROM
        (Корзина AS ci INNER JOIN Товары AS p ON ci.КодТовара=p.Код)
        INNER JOIN ВариантыТоваров AS v ON ci.КодВарианта=v.Код
        WHERE ci.КодПользователя=? ORDER BY ci.Код''', user_id))

def event(cur, order_id, status):
    cur.execute('INSERT INTO ИсторияЗаказов (КодЗаказа, КодСотрудника, СтатусСобытия, ДатаСоздания) VALUES (?, ?, ?, ?)',
                order_id, g.user['Код'], status, datetime.now())

def load_orders(db, condition, params):
    result = records(db.cursor().execute('''SELECT o.*, p.Город, p.Адрес, p.ЧасыРаботы, u.Имя
        FROM (Заказы AS o INNER JOIN ПунктыВыдачи AS p ON o.КодПунктаВыдачи=p.Код)
        INNER JOIN Пользователи AS u ON o.КодПользователя=u.Код WHERE ''' + condition + ' ORDER BY o.Код DESC', params))
    for order in result:
        order['delivery_elapsed'] = max(0, (datetime.now() - order['ДатаСоздания']).total_seconds())
        # Полоса не доходит до конца, пока сервер не подтвердит поступление в ПВЗ.
        order['delivery_progress'] = min(86, 6 + order['delivery_elapsed'] / 30 * 80)
        order['items'] = records(db.cursor().execute('SELECT * FROM СоставЗаказов WHERE КодЗаказа=?', order['Код']))
        order['events'] = records(db.cursor().execute('''SELECT e.*, u.Имя FROM ИсторияЗаказов AS e
            LEFT JOIN Пользователи AS u ON e.КодСотрудника=u.Код WHERE e.КодЗаказа=? ORDER BY e.Код''', order['Код']))
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
            point_id = g.user.get('ВыбранныйПунктВыдачи') if g.user else session.get('selected_pickup_id')
            point = records(db.cursor().execute('SELECT * FROM ПунктыВыдачи WHERE Код=?', point_id)) if point_id else []
            points = records(db.cursor().execute('SELECT * FROM ПунктыВыдачи ORDER BY Город, Адрес'))
            search_index = search_items(db)
        city = point[0]['Город'] if point else (g.user.get('ВыбранныйГород') if g.user else None) or session.get('selected_city', 'Ижевск')
        return {'statuses': STATUSES, 'roles': ROLES, 'nav_categories': cats, 'all_points': points, 'search_index': search_index,
                'city_has_points': any(p['Город'] == city for p in points),
                'all_cities': sorted({p['Город'] for p in points} | {'Ижевск', 'Москва', 'Санкт-Петербург', 'Казань', 'Самара', 'Екатеринбург'}),
                'selected_point': point[0] if point else None,
                'selected_city': city}

    @app.get('/api/notifications')
    @login_required
    def notifications():
        with connection() as db:
            rows = records(db.cursor().execute('''SELECT o.Код, p.Город, p.Адрес FROM Заказы AS o
                INNER JOIN ПунктыВыдачи AS p ON o.КодПунктаВыдачи=p.Код
                WHERE o.КодПользователя=? AND o.СтатусЗаказа='ready' ORDER BY o.Код DESC''', g.user['Код']))
            condition, params = ('1=1', []) if request.args.get('scope') == 'staff' and g.user['Роль'] == 'admin' else (
                ('КодПунктаВыдачи=?', [g.user['КодПунктаВыдачи'] or 0]) if g.user['Роль'] == 'staff'
                and request.args.get('scope') == 'staff' else ('КодПользователя=?', [g.user['Код']]))
            states = [{'id': r.Код, 'status': r.СтатусЗаказа} for r in db.cursor().execute(
                'SELECT Код, СтатусЗаказа FROM Заказы WHERE ' + condition + ' ORDER BY Код DESC', params)]
        return jsonify(user_id=g.user['Код'], states=states, notifications=[{
            'id': r['Код'], 'message': f"Заказ №{r['Код']} готов к выдаче. {r['Город']}, {r['Адрес']}",
            'url': url_for('orders')} for r in rows])

    @app.get('/api/suggestions')
    def suggestions():
        q = request.args.get('q', '').strip()[:80]
        if not q:
            return jsonify([])
        with connection() as db:
            matches = [item for item in search_items(db) if search_key(q) in item['key']][:6]
        return jsonify([{'title': item['title'], 'url': item['url']} for item in matches])

    @app.get('/product/<int:product_id>')
    def product(product_id):
        with connection() as db:
            rows = records(db.cursor().execute('SELECT * FROM Товары WHERE Код=? AND Активен=True', product_id))
            if not rows:
                abort(404)
            p = enrich(db, rows)[0]
            reviews = records(db.cursor().execute('''SELECT r.*, u.Имя FROM Отзывы AS r
                INNER JOIN Пользователи AS u ON r.КодПользователя=u.Код WHERE r.КодТовара=? ORDER BY r.Код DESC''', product_id))
            can_review = False
            if g.user:
                can_review = bool(db.cursor().execute('''SELECT TOP 1 i.Код FROM СоставЗаказов AS i
                    INNER JOIN Заказы AS o ON i.КодЗаказа=o.Код
                    WHERE o.КодПользователя=? AND i.КодТовара=? AND o.СтатусЗаказа='issued' ''', g.user['Код'], product_id).fetchone())
            related = enrich(db, records(db.cursor().execute('SELECT TOP 4 * FROM Товары WHERE КодКатегории=? AND Код<>? AND Активен=True ORDER BY Код', p['КодКатегории'], product_id)))
        variant_id = request.args.get('variant', type=int)
        variant = next((v for v in p['variants'] if v['Код'] == variant_id), None) or next((v for v in p['variants'] if v['Остаток'] > 0), p['variants'][0] if p['variants'] else None)
        return render_template('product.html', p=p, variant=variant, reviews=reviews, can_review=can_review, related=related)

    @app.route('/favorites')
    @login_required
    def favorites():
        with connection() as db:
            # Избранное связано одновременно с пользователем и товаром.
            products = enrich(db, records(db.cursor().execute('''SELECT p.* FROM Товары AS p
                INNER JOIN Избранное AS f ON p.Код=f.КодТовара WHERE f.КодПользователя=? AND p.Активен=True ORDER BY f.Код DESC''', g.user['Код'])))
        return render_template('favorites.html', products=products)

    @app.post('/favorites/<int:product_id>')
    @login_required
    def toggle_favorite(product_id):
        with connection() as db:
            cur = db.cursor()
            if not cur.execute('SELECT Код FROM Товары WHERE Код=? AND Активен=True', product_id).fetchone():
                abort(404)
            item = cur.execute('SELECT Код FROM Избранное WHERE КодПользователя=? AND КодТовара=?', g.user['Код'], product_id).fetchone()
            if item:
                cur.execute('DELETE FROM Избранное WHERE Код=?', item.Код)
            else:
                cur.execute('INSERT INTO Избранное (КодПользователя, КодТовара) VALUES (?, ?)', g.user['Код'], product_id)
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
            allowed = cur.execute('''SELECT TOP 1 i.Код FROM СоставЗаказов AS i INNER JOIN Заказы AS o ON i.КодЗаказа=o.Код
                WHERE o.КодПользователя=? AND i.КодТовара=? AND o.СтатусЗаказа='issued' ''', g.user['Код'], product_id).fetchone()
            if not allowed:
                abort(403)
            old = cur.execute('SELECT Код FROM Отзывы WHERE КодПользователя=? AND КодТовара=?', g.user['Код'], product_id).fetchone()
            if old:
                cur.execute('UPDATE Отзывы SET Оценка=?, ТекстОтзыва=?, ДатаСоздания=? WHERE Код=?', score, text, datetime.now(), old.Код)
            else:
                cur.execute('INSERT INTO Отзывы (КодПользователя, КодТовара, Оценка, ТекстОтзыва, ДатаСоздания) VALUES (?, ?, ?, ?, ?)', g.user['Код'], product_id, score, text, datetime.now())
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
            if not cur.execute('SELECT Код FROM Товары WHERE Код=? AND Активен=True', product_id).fetchone():
                abort(404)
            variants = records(cur.execute('SELECT * FROM ВариантыТоваров WHERE КодТовара=? ORDER BY Код', product_id))
            variant = next((v for v in variants if v['Код'] == variant_id), None) if variant_id else next((v for v in variants if v['Остаток'] > 0), None)
            if not variant or variant['Остаток'] < quantity:
                return feedback('Выбранный вариант закончился. Выберите другой размер или цвет.', error=True)
            item = cur.execute('SELECT Код, Количество FROM Корзина WHERE КодПользователя=? AND КодВарианта=?', g.user['Код'], variant['Код']).fetchone()
            new_qty = quantity + (item.Количество if item else 0)
            if new_qty > min(99, variant['Остаток']):
                return feedback('В корзине уже максимально доступное количество.', error=True)
            if item:
                cur.execute('UPDATE Корзина SET Количество=? WHERE Код=?', new_qty, item.Код)
            else:
                cur.execute('INSERT INTO Корзина (КодПользователя, КодТовара, КодВарианта, Количество) VALUES (?, ?, ?, ?)', g.user['Код'], product_id, variant['Код'], new_qty)
            count = cur.execute('SELECT SUM(Количество) FROM Корзина WHERE КодПользователя=?', g.user['Код']).fetchone()[0]
        return feedback('Товар добавлен в корзину', count=count, variant_id=variant['Код'], quantity=new_qty,
                        stock=variant['Остаток'])

    @app.post('/cart/quantity/<int:variant_id>')
    @login_required
    def set_cart_quantity(variant_id):
        """Кнопки на карточке задают количество выбранного размера и цвета."""
        quantity = request.form.get('quantity', type=int)
        if quantity is None or not 0 <= quantity <= 99:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            variant = cur.execute('''SELECT v.КодТовара, v.Остаток, p.Активен, p.Цена FROM
                ВариантыТоваров AS v INNER JOIN Товары AS p ON v.КодТовара=p.Код
                WHERE v.Код=?''', variant_id).fetchone()
            if not variant:
                abort(404)
            if quantity and (not variant.Активен or quantity > variant.Остаток):
                return feedback('Такого количества сейчас нет в наличии.', error=True)
            item = cur.execute('SELECT Код FROM Корзина WHERE КодПользователя=? AND КодВарианта=?',
                               g.user['Код'], variant_id).fetchone()
            if quantity == 0:
                if item:
                    cur.execute('DELETE FROM Корзина WHERE Код=?', item.Код)
            elif item:
                cur.execute('UPDATE Корзина SET Количество=? WHERE Код=?', quantity, item.Код)
            else:
                cur.execute('INSERT INTO Корзина (КодПользователя, КодТовара, КодВарианта, Количество) VALUES (?, ?, ?, ?)',
                            g.user['Код'], variant.КодТовара, variant_id, quantity)
            count = cur.execute('SELECT SUM(Количество) FROM Корзина WHERE КодПользователя=?', g.user['Код']).fetchone()[0] or 0
            items = cart_rows(db, g.user['Код'])
            total = sum(item['Цена'] * item['Количество'] for item in items)
            can_checkout = all(item['Активен'] and item['Остаток'] >= item['Количество'] for item in items)
        return feedback('Количество изменено' if quantity else 'Товар убран из корзины',
                        count=count, variant_id=variant_id, quantity=quantity, stock=variant.Остаток,
                        total=float(total), line_total=float(variant.Цена * quantity), can_checkout=can_checkout)

    @app.route('/cart')
    @login_required
    def cart():
        with connection() as db:
            items = cart_rows(db, g.user['Код'])
            enrich(db, items)
            city = g.user.get('ВыбранныйГород') or session.get('selected_city', 'Ижевск')
            if g.user.get('ВыбранныйПунктВыдачи'):
                selected = db.cursor().execute('SELECT Город FROM ПунктыВыдачи WHERE Код=?', g.user['ВыбранныйПунктВыдачи']).fetchone()
                if selected:
                    city = selected.Город
            points = records(db.cursor().execute('SELECT * FROM ПунктыВыдачи WHERE Город=? ORDER BY Адрес', city))
        total = sum(item['Цена'] * item['Количество'] for item in items)
        session.setdefault('checkout_token', secrets.token_hex(24))
        can_checkout = all(i['Активен'] and i['Остаток'] >= i['Количество'] for i in items)
        return render_template('cart.html', items=items, total=total, points=points, can_checkout=can_checkout)

    @app.post('/cart/change/<int:item_id>')
    @login_required
    def change_cart(item_id):
        quantity = 0 if request.form.get('action') == 'remove' else request.form.get('quantity', type=int)
        if quantity is None or not 0 <= quantity <= 99:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            row = cur.execute('''SELECT c.Код, v.Остаток FROM Корзина AS c INNER JOIN ВариантыТоваров AS v ON c.КодВарианта=v.Код
                WHERE c.Код=? AND c.КодПользователя=?''', item_id, g.user['Код']).fetchone()
            if not row:
                abort(404)
            if quantity == 0:
                cur.execute('DELETE FROM Корзина WHERE Код=?', item_id)
            elif quantity <= row.Остаток:
                cur.execute('UPDATE Корзина SET Количество=? WHERE Код=?', quantity, item_id)
            else:
                flash('Такого количества сейчас нет в наличии.')
        return redirect(url_for('cart'))

    @app.route('/pickup')
    def pickup():
        with connection() as db:
            points = records(db.cursor().execute('SELECT * FROM ПунктыВыдачи ORDER BY Город, Адрес'))
        cities = sorted({p['Город'] for p in points} | {'Ижевск', 'Москва', 'Санкт-Петербург', 'Казань', 'Самара', 'Екатеринбург'})
        return render_template('pickup.html', points=points, cities=cities)

    @app.post('/location')
    def select_location():
        point_id = request.form.get('pickup_id', type=int)
        city = request.form.get('city', '').strip()
        with connection() as db:
            if point_id:
                found = db.cursor().execute('SELECT Город FROM ПунктыВыдачи WHERE Код=?', point_id).fetchone()
                if not found:
                    abort(400)
                city = found.Город
            if not 2 <= len(city) <= 80:
                abort(400)
            if g.user:
                db.cursor().execute('UPDATE Пользователи SET ВыбранныйПунктВыдачи=?, ВыбранныйГород=? WHERE Код=?', point_id, city, g.user['Код'])
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
                point = cur.execute('SELECT Код, Город FROM ПунктыВыдачи WHERE Код=?', point_id).fetchone() if point_id else None
                if not point:
                    flash('Выберите существующий пункт выдачи.')
                    return redirect(url_for('cart'))
                items = cart_rows(db, g.user['Код'])
                if not items:
                    flash('Корзина пуста.')
                    return redirect(url_for('cart'))
                total = sum(i['Количество'] * i['Цена'] for i in items)
                cur.execute('''INSERT INTO Заказы (КодПользователя, КодПунктаВыдачи, ДатаСоздания, СтатусЗаказа, КодПолучения, ОбщаяСумма, КлючОформления)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''', g.user['Код'], point_id, datetime.now(), 'shipping', f'{secrets.randbelow(1000000):06d}', total, token)
                order_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                for item in items:
                    if not item['Активен']:
                        raise StockError('Один из товаров больше не продаётся. Удалите его из корзины.')
                    # Условное обновление атомарно резервирует остаток: отрицательные значения невозможны.
                    cur.execute('UPDATE ВариантыТоваров SET Остаток=Остаток-? WHERE Код=? AND Остаток>=?', item['Количество'], item['КодВарианта'], item['Количество'])
                    if cur.rowcount != 1:
                        raise StockError('Остатки изменились. Обновите количество товаров в корзине.')
                    cur.execute('''INSERT INTO СоставЗаказов (КодЗаказа, КодТовара, НазваниеТовара, ЦенаЗаЕдиницу, Количество, КодВарианта, Параметры)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''', order_id, item['Код'], item['Бренд'] + ' / ' + item['Название'], item['Цена'], item['Количество'], item['КодВарианта'], item['Размер'] + ' · ' + item['Цвет'])
                event(cur, order_id, 'shipping')
                cur.execute('DELETE FROM Корзина WHERE КодПользователя=?', g.user['Код'])
                cur.execute('UPDATE Пользователи SET ВыбранныйПунктВыдачи=?, ВыбранныйГород=? WHERE Код=?', point_id, point.Город, g.user['Код'])
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
            result = load_orders(db, 'o.КодПользователя=?', [g.user['Код']])
        return render_template('orders.html', orders=result, staff_mode=False, status_filter='', search='')

    @app.post('/orders/<int:order_id>/cancel')
    @login_required
    def cancel_order(order_id):
        with connection() as db:
            cur = db.cursor()
            cur.execute("UPDATE Заказы SET СтатусЗаказа='cancelled' WHERE Код=? AND КодПользователя=? AND СтатусЗаказа IN ('created','shipping')", order_id, g.user['Код'])
            if cur.rowcount != 1:
                abort(403)
            restore_stock(cur, order_id)
            event(cur, order_id, 'cancelled')
        flash('Заказ отменён.')
        return redirect(url_for('orders'))

    @app.post('/orders/<int:order_id>/return')
    @login_required
    def request_return(order_id):
        with connection() as db:
            cur = db.cursor()
            cur.execute("UPDATE Заказы SET СтатусЗаказа='return_requested' WHERE Код=? AND КодПользователя=? AND СтатусЗаказа='issued'", order_id, g.user['Код'])
            if cur.rowcount != 1:
                abort(403)
            event(cur, order_id, 'return_requested')
        flash('Возврат оформлен. Принеси все товары заказа в тот же ПВЗ и покажи код возврата.')
        return redirect(url_for('orders'))

    @app.post('/orders/<int:order_id>/repeat')
    @login_required
    def repeat_order(order_id):
        added = 0
        with connection() as db:
            cur = db.cursor()
            if not cur.execute('SELECT Код FROM Заказы WHERE Код=? AND КодПользователя=?', order_id, g.user['Код']).fetchone():
                abort(403)
            items = records(cur.execute('SELECT * FROM СоставЗаказов WHERE КодЗаказа=?', order_id))
            for item in items:
                v = cur.execute('''SELECT v.Код, v.Остаток FROM ВариантыТоваров AS v INNER JOIN Товары AS p ON v.КодТовара=p.Код
                    WHERE v.Код=? AND p.Активен=True''', item['КодВарианта'] or 0).fetchone()
                if not v or v.Остаток <= 0:
                    continue
                row = cur.execute('SELECT Код, Количество FROM Корзина WHERE КодПользователя=? AND КодВарианта=?', g.user['Код'], v.Код).fetchone()
                qty = min(99, v.Остаток, item['Количество'] + (row.Количество if row else 0))
                if row:
                    cur.execute('UPDATE Корзина SET Количество=? WHERE Код=?', qty, row.Код)
                else:
                    cur.execute('INSERT INTO Корзина (КодПользователя, КодТовара, КодВарианта, Количество) VALUES (?, ?, ?, ?)', g.user['Код'], item['КодТовара'], v.Код, qty)
                added += 1
        flash('Доступные товары добавлены в корзину по текущим ценам.' if added else 'Товары из этого заказа сейчас недоступны.')
        return redirect(url_for('cart'))

    def staff_page(found=None, lookup_code='', lookup_error='', issued_id=None):
        search = request.args.get('search', '').strip()[:20]
        status = request.args.get('status', '')
        condition, params = ('1=1', []) if g.user['Роль'] == 'admin' else ('o.КодПунктаВыдачи=?', [g.user['КодПунктаВыдачи'] or 0])
        if search:
            condition += ' AND o.Код=?'
            params.append(int(search) if search.isdigit() and len(search) < 10 else 0)
        if status in STATUSES:
            condition += ' AND o.СтатусЗаказа=?'
            params.append(status)
        with connection() as db:
            result = load_orders(db, condition, params)
            issued = load_orders(db, "o.Код=? AND o.СтатусЗаказа IN ('issued','returned')" +
                ('' if g.user['Роль'] == 'admin' else ' AND o.КодПунктаВыдачи=?'),
                [issued_id] if g.user['Роль'] == 'admin' else [issued_id, g.user['КодПунктаВыдачи'] or 0]) if issued_id else []
            found = found or []
            product_ids = sorted({i['КодТовара'] for order in found for i in order['items']})
            photos = {}
            if product_ids:
                products = enrich(db, records(db.cursor().execute('SELECT * FROM Товары WHERE Код IN (' +
                    ','.join('?' for _ in product_ids) + ')', product_ids)))
                photos = {p['Код']: p['images'][0] for p in products}
            for order in found:
                for item in order['items']:
                    item['image'] = photos.get(item['КодТовара'])
        return render_template('staff.html', orders=result, found=found, lookup_code=lookup_code,
                               lookup_error=lookup_error, issued=issued[0] if issued else None,
                               status_filter=status, search=search, lookup_mode=request.method == 'POST')

    @app.get('/staff')
    @role_required('admin', 'staff')
    def staff():
        return staff_page(issued_id=session.pop('pvz_issued_id', None))

    @app.post('/staff/find')
    @role_required('admin', 'staff')
    def find_pickup_order():
        code = request.form.get('code', '').strip()
        if not re.fullmatch(r'[0-9]{6}', code):
            return staff_page(lookup_code=code[:6], lookup_error='Введи шестизначный код покупателя.')
        condition = "o.КодПолучения=? AND o.СтатусЗаказа IN ('ready','return_requested')"
        params = [code]
        if g.user['Роль'] != 'admin':
            condition += ' AND o.КодПунктаВыдачи=?'
            params.append(g.user['КодПунктаВыдачи'] or 0)
        with connection() as db:
            found = load_orders(db, condition, params)
        return staff_page(found=found, lookup_code=code,
                          lookup_error='' if found else 'Заказ не найден. Проверь код и убедись, что заказ поступил в твой ПВЗ.')

    @app.post('/staff/orders/<int:order_id>')
    @role_required('admin', 'staff')
    def process_order(order_id):
        with connection() as db:
            cur = db.cursor()
            rows = records(cur.execute('SELECT * FROM Заказы WHERE Код=?', order_id))
            if not rows:
                abort(404)
            order = rows[0]
            if g.user['Роль'] != 'admin' and order['КодПунктаВыдачи'] != g.user['КодПунктаВыдачи']:
                abort(403)
            action = request.form.get('action')
            new_status = None
            if action == 'shipping' and order['СтатусЗаказа'] == 'created' and g.user['Роль'] == 'admin':
                new_status = 'shipping'
            elif action == 'ready' and order['СтатусЗаказа'] in {'created', 'shipping'}:
                new_status = 'ready'
            elif action == 'issue' and order['СтатусЗаказа'] == 'ready':
                if hmac.compare_digest(order['КодПолучения'], request.form.get('code', '').strip()):
                    new_status = 'issued'
                else:
                    flash('Код получения неверный. Заказ не выдан.')
                    return redirect(url_for('staff'))
            elif action in {'return', 'refuse'} and order['СтатусЗаказа'] == ('return_requested' if action == 'return' else 'ready'):
                if not hmac.compare_digest(order['КодПолучения'], request.form.get('code', '').strip()):
                    flash('Код неверный. Возврат не принят.')
                    return redirect(url_for('staff'))
                new_status = 'returned'
            if not new_status:
                flash('Это действие недоступно для текущего статуса заказа.')
            else:
                cur.execute('UPDATE Заказы SET СтатусЗаказа=? WHERE Код=? AND СтатусЗаказа=?', new_status, order_id, order['СтатусЗаказа'])
                if cur.rowcount == 1:
                    if new_status == 'returned':
                        restore_stock(cur, order_id)
                    event(cur, order_id, new_status)
                    if new_status in {'issued', 'returned'} and request.form.get('desk') == '1':
                        session['pvz_issued_id'] = order_id
                if not (new_status in {'issued', 'returned'} and request.form.get('desk') == '1'):
                    flash('Статус заказа обновлён.')
        return redirect(url_for('staff'))

    @app.get('/admin')
    @role_required('admin')
    def admin():
        section = request.args.get('section', 'products')
        if section not in {'products', 'points', 'users', 'suppliers'}:
            section = 'products'
        with connection() as db:
            users = records(db.cursor().execute('SELECT Код, Имя, ЭлектроннаяПочта, Роль, КодПунктаВыдачи, ЗаявкаПоставщика, НазваниеКомпании FROM Пользователи ORDER BY Код')) if section in {'users', 'suppliers'} else []
            points = records(db.cursor().execute('SELECT * FROM ПунктыВыдачи ORDER BY Город, Адрес')) if section in {'users', 'points'} else []
            cats = categories(db)
            products = enrich(db, records(db.cursor().execute('SELECT * FROM Товары ORDER BY Код DESC'))) if section == 'products' else []
        return render_template('admin.html', section=section, users=users, points=points, categories=cats, products=products)

    @app.post('/admin/pickup')
    @role_required('admin')
    def create_pickup():
        city, address, hours = [request.form.get(k, '').strip() for k in ['city', 'address', 'hours']]
        if not 2 <= len(city) <= 80 or not 4 <= len(address) <= 200 or not 3 <= len(hours) <= 80:
            flash('Заполните город, адрес и часы работы пункта.')
        else:
            with connection() as db:
                db.cursor().execute('INSERT INTO ПунктыВыдачи (Город, Адрес, ЧасыРаботы) VALUES (?, ?, ?)', city, address, hours)
            flash('Пункт выдачи создан.')
        return redirect(url_for('admin', section='points'))

    @app.post('/admin/users/<int:user_id>')
    @role_required('admin')
    def change_role(user_id):
        role = request.form.get('role')
        point_id = request.form.get('pickup_id', type=int) if role == 'staff' else None
        if role not in ROLES:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            target = cur.execute('SELECT Роль FROM Пользователи WHERE Код=?', user_id).fetchone()
            if not target:
                abort(404)
            if role == 'staff' and (not point_id or not cur.execute('SELECT Код FROM ПунктыВыдачи WHERE Код=?', point_id).fetchone()):
                flash('Для сотрудника выберите пункт выдачи.')
            elif target.Роль == 'admin' and role != 'admin' and cur.execute("SELECT COUNT(*) FROM Пользователи WHERE Роль='admin'").fetchone()[0] <= 1:
                flash('Нельзя убрать права у последнего администратора.')
            else:
                cur.execute('UPDATE Пользователи SET Роль=?, КодПунктаВыдачи=? WHERE Код=?', role, point_id, user_id)
                flash('Права пользователя обновлены.')
        return redirect(url_for('admin', section='users'))

    @app.post('/supplier/apply')
    @role_required('customer')
    def apply_supplier():
        company = request.form.get('company', '').strip()
        if not 2 <= len(company) <= 100:
            flash('Укажите название магазина от 2 до 100 символов.')
        else:
            with connection() as db:
                db.cursor().execute("UPDATE Пользователи SET НазваниеКомпании=?, ЗаявкаПоставщика='pending' WHERE Код=?", company, g.user['Код'])
            flash('Заявка отправлена администратору.')
        return redirect(url_for('account'))

    @app.post('/admin/suppliers/<int:user_id>')
    @role_required('admin')
    def review_supplier(user_id):
        action = request.form.get('action')
        if action not in {'approve', 'reject'}:
            abort(400)
        with connection() as db:
            cur = db.cursor()
            target = cur.execute('SELECT Роль, ЗаявкаПоставщика FROM Пользователи WHERE Код=?', user_id).fetchone()
            if not target or target.Роль != 'customer' or target.ЗаявкаПоставщика != 'pending':
                abort(409)
            cur.execute('UPDATE Пользователи SET Роль=?, ЗаявкаПоставщика=? WHERE Код=?',
                        'supplier' if action == 'approve' else 'customer', 'approved' if action == 'approve' else 'rejected', user_id)
        flash('Поставщик добавлен.' if action == 'approve' else 'Заявка отклонена.')
        return redirect(url_for('admin', section='suppliers'))

    @app.get('/supplier')
    @role_required('supplier')
    def supplier():
        with connection() as db:
            products = enrich(db, records(db.cursor().execute('SELECT * FROM Товары WHERE КодПоставщика=? ORDER BY Код DESC', g.user['Код'])))
            cats = categories(db)
        return render_template('supplier.html', products=products, categories=cats)

    @app.post('/admin/categories')
    @role_required('admin')
    def create_category():
        title = request.form.get('title', '').strip()
        parent = request.form.get('parent_id', type=int)
        if not 2 <= len(title) <= 80:
            flash('Название категории должно содержать от 2 до 80 символов.')
        else:
            with connection() as db:
                if parent and not db.cursor().execute('SELECT Код FROM Категории WHERE Код=? AND КодРодителя IS NULL', parent).fetchone():
                    abort(400)
                db.cursor().execute('INSERT INTO Категории (Название, КодРодителя) VALUES (?, ?)', title, parent)
            flash('Категория добавлена.')
        return redirect(url_for('admin'))

    @app.route('/admin/products/new', methods=['GET', 'POST'])
    @app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
    @role_required('admin', 'supplier')
    def edit_product(product_id=None):
        with connection() as db:
            cats = categories(db)
            old = records(db.cursor().execute('SELECT * FROM Товары WHERE Код=?', product_id)) if product_id else []
            if product_id and not old:
                abort(404)
            if old and g.user['Роль'] == 'supplier' and old[0]['КодПоставщика'] != g.user['Код']:
                abort(403)
            p = enrich(db, old)[0] if old else None
        if request.method == 'GET':
            return render_template('edit_product.html', p=p, categories=cats)
        try:
            title, brand = request.form.get('title', '').strip(), request.form.get('brand', '').strip()
            category_id = request.form.get('category_id', type=int)
            if not 2 <= len(title) <= 150 or not 1 <= len(brand) <= 80 or category_id not in {c['Код'] for c in cats}:
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
                    cur.execute('UPDATE Товары SET КодКатегории=?, Бренд=?, Название=?, Цена=?, СтараяЦена=?, Описание=?, Характеристики=?, Активен=? WHERE Код=?',
                                category_id, brand, title, price, old_price, description, specifications, active, product_id)
                else:
                    cur.execute('''INSERT INTO Товары (КодКатегории, Бренд, Название, Цена, СтараяЦена, Рейтинг, КоличествоОтзывов, Изображение, Оттенок, Описание, Характеристики, Активен)
                        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)''', category_id, brand, title, price, old_price, 'shirt', '#f4f3f6', description, specifications, active)
                    product_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                    if g.user['Роль'] == 'supplier':
                        cur.execute('UPDATE Товары SET КодПоставщика=? WHERE Код=?', g.user['Код'], product_id)
                current_ids = {r.Код for r in cur.execute('SELECT Код FROM ВариантыТоваров WHERE КодТовара=?', product_id)}
                for vid, size, color, stock in variants:
                    if vid and vid not in current_ids:
                        raise ValueError('Вариант товара не принадлежит этой карточке.')
                    if vid:
                        cur.execute('UPDATE ВариантыТоваров SET Размер=?, Цвет=?, Остаток=? WHERE Код=?', size, color, stock, vid)
                    else:
                        cur.execute('INSERT INTO ВариантыТоваров (КодТовара, Размер, Цвет, Остаток) VALUES (?, ?, ?, ?)', product_id, size, color, stock)
                # Существующие варианты не удаляются: исторические заказы сохраняют свои связи.
                for missing_id in current_ids - {v[0] for v in variants if v[0]}:
                    cur.execute('UPDATE ВариантыТоваров SET Остаток=0 WHERE Код=?', missing_id)
                if images and request.form.get('replace_photos') == 'on':
                    cur.execute('DELETE FROM ФотографииТоваров WHERE КодТовара=?', product_id)
                maximum = cur.execute('SELECT MAX(Порядок) FROM ФотографииТоваров WHERE КодТовара=?', product_id).fetchone()[0] or 0
                for number, image in enumerate(images, maximum + 1):
                    cur.execute('INSERT INTO ФотографииТоваров (КодТовара, ПутьКФото, Порядок) VALUES (?, ?, ?)', product_id, image, number)
            flash('Карточка товара сохранена.')
            return redirect(url_for('supplier') if g.user['Роль'] == 'supplier' else url_for('admin'))
        except ValueError as error:
            flash(str(error))
            return render_template('edit_product.html', p=p, categories=cats), 400

    @app.post('/admin/products/<int:product_id>/visibility')
    @role_required('admin', 'supplier')
    def product_visibility(product_id):
        with connection() as db:
            cur = db.cursor()
            row = cur.execute('SELECT Активен, КодПоставщика FROM Товары WHERE Код=?', product_id).fetchone()
            if not row:
                abort(404)
            if g.user['Роль'] == 'supplier' and row.КодПоставщика != g.user['Код']:
                abort(403)
            cur.execute('UPDATE Товары SET Активен=? WHERE Код=?', not row.Активен, product_id)
        flash('Видимость товара изменена. История заказов сохранена.')
        return redirect(url_for('supplier') if g.user['Роль'] == 'supplier' else url_for('admin'))

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('error.html', code=403, message='Эта страница доступна только пользователям с нужной ролью.'), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', code=404, message='Страница не найдена. Возможно, товар больше не продаётся.'), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template('error.html', code=413, message='Фотографии слишком большие. Общий размер загрузки — до 24 МБ.'), 413

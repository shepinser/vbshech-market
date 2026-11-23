"""Проверка сценариев на отдельной настоящей БД Access; рабочая база не изменяется."""
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix='vbsh-check-') as temporary:
    os.environ['VBSH_DATABASE'] = str(Path(temporary) / 'test.accdb')
    from init_db import initialize
    from db import connection
    initialize()
    from app import app
    from werkzeug.security import check_password_hash
    app.config['TESTING'] = True
    customer, admin, staff, outsider = [app.test_client() for _ in range(4)]

    def post(client, url, **data):
        client.get('/')
        with client.session_transaction() as state:
            data['csrf'] = state['csrf']
        return client.post(url, data=data)

    def register(client, email, name):
        result = post(client, '/register', name=name, email=email,
                      password='Test-password-123', confirm='Test-password-123', role='admin')
        assert result.status_code == 302
        with client.session_transaction() as state:
            return state['user_id']

    buyer_id = register(customer, 'buyer@example.test', 'Покупатель')
    admin_id = register(admin, 'admin@example.test', 'Администратор')
    staff_id = register(staff, 'staff@example.test', 'Сотрудник')
    other_id = register(outsider, 'other@example.test', 'Другой покупатель')
    with connection() as db:
        row = db.cursor().execute('SELECT UserRole, PasswordHash FROM Users WHERE ID=?', buyer_id).fetchone()
        assert row.UserRole == 'customer'  # Подмена роли через форму не даёт прав.
        assert row.PasswordHash != 'Test-password-123'
        assert check_password_hash(row.PasswordHash, 'Test-password-123')
        db.cursor().execute("UPDATE Users SET UserRole='admin' WHERE ID=?", admin_id)
    assert customer.get('/admin').status_code == 403
    assert customer.post('/cart/add/1').status_code == 400  # CSRF.
    assert post(customer, '/admin/pickup', city='Ижевск', address='Чужой адрес', hours='9–21').status_code == 403
    assert post(admin, '/admin/pickup', city='Ижевск', address='Тестовая улица, 1', hours='09:00–21:00').status_code == 302
    assert post(admin, '/admin/pickup', city='Ижевск', address='Тестовая улица, 2', hours='09:00–21:00').status_code == 302
    with connection() as db:
        point_ids = [r.ID for r in db.cursor().execute('SELECT ID FROM PickupPoints ORDER BY ID')]
    point_id, other_point = point_ids
    assert post(admin, f'/admin/users/{staff_id}', role='staff', pickup_id=point_id).status_code == 302
    assert staff.get('/admin').status_code == 403
    assert admin.get('/admin').status_code == 200
    assert staff.get('/staff').status_code == 200
    assert post(admin, f'/admin/users/{admin_id}', role='customer').status_code == 302
    assert admin.get('/admin').status_code == 200  # Последний администратор защищён.
    post(customer, '/cart/add/1')
    post(customer, '/cart/add/1')
    assert customer.get('/cart').status_code == 200
    with customer.session_transaction() as state:
        token = state['checkout_token']
    assert post(customer, '/checkout', pickup_id=point_id, checkout_token=token).status_code == 302
    post(customer, '/checkout', pickup_id=point_id, checkout_token=token)
    with connection() as db:
        assert db.cursor().execute('SELECT COUNT(*) FROM OrderHeaders').fetchone()[0] == 1
        order = db.cursor().execute('SELECT ID, PickupCode, TotalAmount FROM OrderHeaders').fetchone()
        assert order.TotalAmount == 4980
        assert db.cursor().execute('SELECT COUNT(*) FROM CartItems WHERE UserID=?', buyer_id).fetchone()[0] == 0
    assert post(outsider, f'/orders/{order.ID}/cancel').status_code == 403
    assert post(staff, f'/staff/orders/{order.ID}', action='issue', code=order.PickupCode).status_code == 302
    with connection() as db:
        assert db.cursor().execute('SELECT OrderStatus FROM OrderHeaders WHERE ID=?', order.ID).fetchone()[0] == 'created'
    post(admin, f'/admin/users/{staff_id}', role='staff', pickup_id=other_point)
    assert post(staff, f'/staff/orders/{order.ID}', action='ready').status_code == 403
    post(admin, f'/admin/users/{staff_id}', role='staff', pickup_id=point_id)
    post(staff, f'/staff/orders/{order.ID}', action='ready')
    wrong_code = '000000' if order.PickupCode != '000000' else '111111'
    post(staff, f'/staff/orders/{order.ID}', action='issue', code=wrong_code)
    with connection() as db:
        assert db.cursor().execute('SELECT OrderStatus FROM OrderHeaders WHERE ID=?', order.ID).fetchone()[0] == 'ready'
    assert order.PickupCode.encode() in customer.get('/orders').data
    assert order.PickupCode.encode() not in staff.get('/staff').data
    post(staff, f'/staff/orders/{order.ID}', action='issue', code=order.PickupCode)
    with connection() as db:
        assert db.cursor().execute('SELECT OrderStatus FROM OrderHeaders WHERE ID=?', order.ID).fetchone()[0] == 'issued'
    for client, paths in [(customer, ['/', '/cart', '/orders', '/account', '/pickup']), (admin, ['/admin', '/staff']), (staff, ['/staff'])]:
        for path in paths:
            assert client.get(path).status_code == 200, path
    post(customer, '/logout')
    assert post(customer, '/login', email='buyer@example.test', password='wrong').status_code == 200
    assert post(customer, '/login', email='BUYER@example.test', password='Test-password-123').status_code == 302
    duplicate = post(app.test_client(), '/register', name='Повтор', email='buyer@example.test', password='Test-password-123', confirm='Test-password-123')
    assert duplicate.status_code == 200
    # Access должен реально запрещать запись с несуществующими внешними ключами.
    import pyodbc
    try:
        with connection() as db:
            db.cursor().execute('INSERT INTO CartItems (UserID, ProductID, Quantity) VALUES (?, ?, 1)', 999999, 999999)
    except pyodbc.IntegrityError:
        pass
    else:
        raise AssertionError('Access relationships do not enforce integrity')
    # Избранное хранится в Access и переключается независимо от других аккаунтов.
    customer.get('/')
    with customer.session_transaction() as state:
        favorite_csrf = state['csrf']
    async_result = customer.post('/favorites/2', data={'csrf': favorite_csrf}, headers={'X-Requested-With': 'fetch'})
    assert async_result.json['active'] is True
    with connection() as db:
        assert db.cursor().execute('SELECT COUNT(*) FROM Favorites WHERE UserID=? AND ProductID=2', buyer_id).fetchone()[0] == 1
    assert customer.get('/favorites').status_code == 200
    post(customer, '/favorites/2')
    assert post(outsider, '/product/1/review', score=5, text='Не покупал этот товар').status_code == 403
    assert post(customer, '/product/1/review', score=5, text='Удобные наушники, всё работает').status_code == 302
    with connection() as db:
        assert db.cursor().execute('SELECT Score FROM ProductReviews WHERE UserID=? AND ProductID=1', buyer_id).fetchone()[0] == 5
    post(customer, '/product/1/review', score=4, text='Дополняю отзыв после использования')
    with connection() as db:
        assert db.cursor().execute('SELECT COUNT(*) FROM ProductReviews WHERE UserID=? AND ProductID=1', buyer_id).fetchone()[0] == 1
    assert customer.get('/?min_price=800&max_price=1200&color=Белый&sort=cheap').status_code == 200
    assert customer.get('/?size=M&in_stock=1&sort=rating').status_code == 200
    assert len(customer.get('/api/suggestions?q=SOUND').json) == 2
    post(customer, '/location', pickup_id=point_id)
    with connection() as db:
        assert db.cursor().execute('SELECT SelectedPickupID FROM Users WHERE ID=?', buyer_id).fetchone()[0] == point_id
    # Неудачный заказ не оставляет заголовок, строки или частично списанный остаток.
    with connection() as db:
        first = db.cursor().execute('SELECT TOP 1 ID, Stock FROM ProductVariants WHERE ProductID=2 ORDER BY ID').fetchone()
    post(customer, '/cart/add/2', variant_id=first.ID, quantity=2)
    customer.get('/cart')
    with customer.session_transaction() as state:
        checkout_token = state['checkout_token']
    with connection() as db:
        db.cursor().execute('UPDATE ProductVariants SET Stock=1 WHERE ID=?', first.ID)
        before_count = db.cursor().execute('SELECT COUNT(*) FROM OrderHeaders').fetchone()[0]
    post(customer, '/checkout', pickup_id=point_id, checkout_token=checkout_token)
    with connection() as db:
        assert db.cursor().execute('SELECT COUNT(*) FROM OrderHeaders').fetchone()[0] == before_count
        assert db.cursor().execute('SELECT Stock FROM ProductVariants WHERE ID=?', first.ID).fetchone()[0] == 1
        db.cursor().execute('UPDATE ProductVariants SET Stock=10 WHERE ID=?', first.ID)
    post(customer, '/checkout', pickup_id=point_id, checkout_token=checkout_token)
    with connection() as db:
        second_order = db.cursor().execute('SELECT MAX(ID) FROM OrderHeaders').fetchone()[0]
        assert db.cursor().execute('SELECT Stock FROM ProductVariants WHERE ID=?', first.ID).fetchone()[0] == 8
    post(customer, f'/orders/{second_order}/cancel')
    with connection() as db:
        assert db.cursor().execute('SELECT Stock FROM ProductVariants WHERE ID=?', first.ID).fetchone()[0] == 10
    assert post(customer, f'/orders/{second_order}/cancel').status_code == 403
    post(customer, f'/orders/{second_order}/repeat')
    with connection() as db:
        assert db.cursor().execute('SELECT Quantity FROM CartItems WHERE UserID=? AND VariantID=?', buyer_id, first.ID).fetchone()[0] == 2
    assert admin.get('/admin/products/new').status_code == 200
    assert admin.get('/admin/products/2/edit').status_code == 200
    assert customer.get('/admin/products/new').status_code == 403
    # Проверяем сохранение товара с реальным изображением, вариантами и ценой.
    from io import BytesIO
    from PIL import Image
    photo = BytesIO()
    Image.new('RGB', (100, 100), '#8040a0').save(photo, 'PNG')
    photo.seek(0)
    admin.get('/')
    with admin.session_transaction() as state:
        csrf = state['csrf']
    data = dict(csrf=csrf, title='Тестовый рюкзак', brand='TEST', category_id='1', price='1500', old_price='2000',
                description='Описание', specifications='Материал: текстиль', active='on',
                variant_id=[''], variant_size=['Без размера'], variant_color=['Фиолетовый'], variant_stock=['7'],
                photos=(photo, 'sample.png'))
    response = admin.post('/admin/products/new', data=data, content_type='multipart/form-data')
    assert response.status_code == 302, response.data[:500]
    with connection() as db:
        created_id = db.cursor().execute('SELECT MAX(ID) FROM Products').fetchone()[0]
        image_path = db.cursor().execute('SELECT ImagePath FROM ProductImages WHERE ProductID=?', created_id).fetchone()[0]
    assert customer.get(f'/product/{created_id}').status_code == 200
    with connection() as db:
        created_variant = db.cursor().execute('SELECT ID FROM ProductVariants WHERE ProductID=?', created_id).fetchone()[0]
    edit_data = dict(title='Тестовый рюкзак обновлён', brand='TEST', category_id='1', price='1500.0000', old_price='2000.0000',
                     description='Обновлённое описание', active='on', variant_id=[str(created_variant)],
                     variant_size=['Без размера'], variant_color=['Фиолетовый'], variant_stock=['9'])
    assert post(admin, f'/admin/products/{created_id}/edit', **edit_data).status_code == 302
    edit_data['price'] = '1500.123'
    assert post(admin, f'/admin/products/{created_id}/edit', **edit_data).status_code == 400
    with connection() as db:
        assert db.cursor().execute('SELECT Price FROM Products WHERE ID=?', created_id).fetchone()[0] == 1500
        assert db.cursor().execute('SELECT Stock FROM ProductVariants WHERE ID=?', created_variant).fetchone()[0] == 9
    post(admin, f'/admin/products/{created_id}/visibility')
    assert customer.get(f'/product/{created_id}').status_code == 404
    post(admin, '/admin/categories', title='Рюкзаки', parent_id='1')
    # Удаляется только собственная фотография, созданная этой проверкой.
    from db import BASE_DIR
    (BASE_DIR / 'static' / image_path).unlink()
    print('PASS: auth, roles, Access relations, variants, stock rollback, cancellation, favorites, reviews, locations, photo upload and admin catalog')

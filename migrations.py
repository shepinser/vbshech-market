"""Дополнение существующей Access без удаления аккаунтов и заказов."""
from db import connection, records

def migrate():
    with connection() as db:
        cur = db.cursor()
        tables = {r.table_name for r in cur.tables(tableType='TABLE')}
        def column(table, name, kind):
            if name.lower() not in {r.column_name.lower() for r in cur.columns(table=table)}:
                cur.execute(f'ALTER TABLE [{table}] ADD COLUMN [{name}] {kind}')
        column('Categories', 'ParentID', 'LONG')
        column('Users', 'SelectedPickupID', 'LONG')
        column('Users', 'SelectedCity', 'TEXT(80)')
        column('Products', 'Description', 'MEMO')
        column('Products', 'Specifications', 'MEMO')
        column('Products', 'IsActive', 'YESNO')
        column('CartItems', 'VariantID', 'LONG')
        column('OrderItems', 'VariantID', 'LONG')
        column('OrderItems', 'OptionsText', 'TEXT(150)')
        statements = {
            'ProductVariants': '''CREATE TABLE ProductVariants (ID COUNTER PRIMARY KEY,
                ProductID LONG NOT NULL, SizeLabel TEXT(30) NOT NULL,
                ColorLabel TEXT(40) NOT NULL, Stock LONG NOT NULL)''',
            'ProductImages': '''CREATE TABLE ProductImages (ID COUNTER PRIMARY KEY,
                ProductID LONG NOT NULL, ImagePath TEXT(255) NOT NULL, SortOrder LONG NOT NULL)''',
            'Favorites': '''CREATE TABLE Favorites (ID COUNTER PRIMARY KEY,
                UserID LONG NOT NULL, ProductID LONG NOT NULL)''',
            'ProductReviews': '''CREATE TABLE ProductReviews (ID COUNTER PRIMARY KEY,
                UserID LONG NOT NULL, ProductID LONG NOT NULL,
                Score LONG NOT NULL, ReviewText MEMO, CreatedAt DATETIME NOT NULL)''',
            'OrderEvents': '''CREATE TABLE OrderEvents (ID COUNTER PRIMARY KEY,
                OrderID LONG NOT NULL, ActorID LONG NOT NULL,
                EventStatus TEXT(20) NOT NULL, CreatedAt DATETIME NOT NULL)''',
            'SchemaVersions': 'CREATE TABLE SchemaVersions (VersionNumber LONG PRIMARY KEY)',
        }
        for name, sql in statements.items():
            if name not in tables:
                cur.execute(sql)
        done = cur.execute('SELECT VersionNumber FROM SchemaVersions WHERE VersionNumber=2').fetchone()
        if done:
            return
        for sql in [
            'ALTER TABLE Categories ADD CONSTRAINT FK_Category_Parent FOREIGN KEY (ParentID) REFERENCES Categories (ID)',
            'ALTER TABLE Users ADD CONSTRAINT FK_User_SelectedPoint FOREIGN KEY (SelectedPickupID) REFERENCES PickupPoints (ID)',
            'ALTER TABLE ProductVariants ADD CONSTRAINT FK_Variant_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
            'ALTER TABLE ProductImages ADD CONSTRAINT FK_Image_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
            'ALTER TABLE Favorites ADD CONSTRAINT FK_Favorite_User FOREIGN KEY (UserID) REFERENCES Users (ID)',
            'ALTER TABLE Favorites ADD CONSTRAINT FK_Favorite_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
            'CREATE UNIQUE INDEX UX_Favorite ON Favorites (UserID, ProductID)',
            'ALTER TABLE ProductReviews ADD CONSTRAINT FK_Review_User FOREIGN KEY (UserID) REFERENCES Users (ID)',
            'ALTER TABLE ProductReviews ADD CONSTRAINT FK_Review_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
            'CREATE UNIQUE INDEX UX_ProductReview ON ProductReviews (UserID, ProductID)',
            'ALTER TABLE OrderEvents ADD CONSTRAINT FK_Event_Order FOREIGN KEY (OrderID) REFERENCES OrderHeaders (ID)',
            'ALTER TABLE OrderEvents ADD CONSTRAINT FK_Event_Actor FOREIGN KEY (ActorID) REFERENCES Users (ID)',
            'ALTER TABLE CartItems ADD CONSTRAINT FK_Cart_Variant FOREIGN KEY (VariantID) REFERENCES ProductVariants (ID)',
            'ALTER TABLE OrderItems ADD CONSTRAINT FK_OrderItem_Variant FOREIGN KEY (VariantID) REFERENCES ProductVariants (ID)',
            'DROP INDEX UX_Cart_UserProduct ON CartItems',
            'CREATE UNIQUE INDEX UX_Cart_UserVariant ON CartItems (UserID, VariantID)',
        ]:
            cur.execute(sql)
        roots = records(cur.execute('SELECT ID, Title FROM Categories WHERE ParentID IS NULL'))
        children = {'Электроника': ['Наушники'], 'Одежда': ['Футболки'], 'Обувь': ['Кроссовки'], 'Дом и уют': ['Кружки']}
        child_ids = {}
        for root in roots:
            for title in children.get(root['Title'], []):
                cur.execute('INSERT INTO Categories (Title, ParentID) VALUES (?, ?)', title, root['ID'])
                child_ids[root['ID']] = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
        products = records(cur.execute('SELECT ID, Picture, CategoryID, Title FROM Products'))
        for p in products:
            description = {'headphones': 'Полноразмерные наушники для музыки, учёбы и работы. Мягкие амбушюры и регулируемое оголовье.',
                           'shirt': 'Базовая футболка свободного кроя из хлопкового трикотажа. Круглый вырез, прямой силуэт и мягкая ткань.',
                           'shoe': 'Повседневная обувь со шнуровкой и мягкой стелькой. Для прогулок и городского ритма.',
                           'cup': 'Керамическая кружка для любимого напитка. Удобная ручка, гладкая поверхность и лаконичная форма.'}.get(p['Picture'], p['Title'])
            specs = {'headphones': 'Тип: полноразмерные\nНазначение: музыка и разговоры\nКомплектация: наушники, инструкция',
                     'shirt': 'Состав: хлопок 100%\nКрой: свободный\nУход: деликатная стирка при 30 °C',
                     'shoe': 'Верх: искусственная кожа, текстиль\nПодошва: резина\nЗастёжка: шнуровка',
                     'cup': 'Материал: керамика\nОбъём: 350 мл\nКомплектация: кружка'}.get(p['Picture'], '')
            cur.execute('UPDATE Products SET IsActive=True, Description=?, Specifications=?, Rating=0, Reviews=0, CategoryID=? WHERE ID=?',
                        description, specs, child_ids.get(p['CategoryID'], p['CategoryID']), p['ID'])
            sizes = ['S', 'M', 'L', 'XL'] if p['Picture'] == 'shirt' else ['38', '39', '40', '41', '42', '43'] if p['Picture'] == 'shoe' else ['Без размера']
            color = 'Чёрный' if p['Picture'] == 'headphones' else 'Белый'
            for size in sizes:
                cur.execute('INSERT INTO ProductVariants (ProductID, SizeLabel, ColorLabel, Stock) VALUES (?, ?, ?, ?)', p['ID'], size, color, 20)
            variant_id = cur.execute('SELECT MIN(ID) FROM ProductVariants WHERE ProductID=?', p['ID']).fetchone()[0]
            cur.execute('UPDATE CartItems SET VariantID=? WHERE ProductID=?', variant_id, p['ID'])
            # Фотографии находятся локально; после установки интернет не нужен.
            images = {'headphones': ['headphones-black.jpg', 'headphones-white.jpg'], 'shirt': ['shirt-flat.jpg', 'shirt-hanger.jpg'],
                      'shoe': ['sneakers.jpg'], 'cup': ['mug-white.jpg', 'mug-studio.jpg']}.get(p['Picture'], [])
            # Разные цвета не изображают один вариант товара: дополнительные фото тех же типов отмечены как примеры в описании.
            if p['Picture'] == 'headphones':
                images = ['headphones-black.jpg']
            for order, image in enumerate(images):
                cur.execute('INSERT INTO ProductImages (ProductID, ImagePath, SortOrder) VALUES (?, ?, ?)', p['ID'], 'photos/' + image, order)
        # Старое название термокружки не совпадало с демонстрационным фото керамики.
        cur.execute("UPDATE Products SET Title='Кружка керамическая, 350 мл' WHERE Picture='cup' AND ID=4")
        cur.execute("UPDATE Products SET Title='Кружка для кофе, керамика' WHERE Picture='cup' AND ID=7")
        cur.execute('INSERT INTO SchemaVersions (VersionNumber) VALUES (2)')
    print('Access schema updated to v2; existing accounts and orders retained.')

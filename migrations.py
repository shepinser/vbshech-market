"""Дополнение существующей Access без удаления аккаунтов и заказов."""
from db import connection, records

def migrate_store():
    """Поставщики и часы учебной доставки; существующие записи сохраняются."""
    import subprocess
    from datetime import datetime, timedelta
    from db import BASE_DIR, DB_PATH
    with connection() as db:
        if db.cursor().execute('SELECT НомерВерсии FROM ВерсииСхемы WHERE НомерВерсии=3').fetchone():
            return
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(BASE_DIR / 'create_database.ps1'), '-DatabasePath', str(DB_PATH),
                    '-AllowSystemEvents'], check=True)
    with connection() as db:
        cur = db.cursor()
        for table, name, kind in [('Пользователи', 'ЗаявкаПоставщика', 'TEXT(20)'),
                                   ('Пользователи', 'НазваниеКомпании', 'TEXT(100)'),
                                   ('Товары', 'КодПоставщика', 'LONG')]:
            if name not in {r.column_name for r in cur.columns(table=table)}:
                cur.execute(f'ALTER TABLE [{table}] ADD COLUMN [{name}] {kind}')
        cur.execute('ALTER TABLE Товары ADD CONSTRAINT СвязьПоставщика FOREIGN KEY (КодПоставщика) REFERENCES Пользователи (Код)')
        cur.execute('CREATE TABLE НастройкиМагазина (Код LONG PRIMARY KEY, СледующаяДоставка DATETIME NOT NULL)')
        cur.execute('INSERT INTO НастройкиМагазина (Код, СледующаяДоставка) VALUES (1, ?)', datetime.now() + timedelta(seconds=30))
        cur.execute('INSERT INTO ВерсииСхемы (НомерВерсии) VALUES (3)')

def migrate():
    with connection() as db:
        cur = db.cursor()
        tables = {r.table_name for r in cur.tables(tableType='TABLE')}
        def column(table, name, kind):
            if name.lower() not in {r.column_name.lower() for r in cur.columns(table=table)}:
                cur.execute(f'ALTER TABLE [{table}] ADD COLUMN [{name}] {kind}')
        column('Категории', 'КодРодителя', 'LONG')
        column('Пользователи', 'ВыбранныйПунктВыдачи', 'LONG')
        column('Пользователи', 'ВыбранныйГород', 'TEXT(80)')
        column('Товары', 'Описание', 'MEMO')
        column('Товары', 'Характеристики', 'MEMO')
        column('Товары', 'Активен', 'YESNO')
        column('Корзина', 'КодВарианта', 'LONG')
        column('СоставЗаказов', 'КодВарианта', 'LONG')
        column('СоставЗаказов', 'Параметры', 'TEXT(150)')
        statements = {
            'ВариантыТоваров': '''CREATE TABLE ВариантыТоваров (Код COUNTER PRIMARY KEY,
                КодТовара LONG NOT NULL, Размер TEXT(30) NOT NULL,
                Цвет TEXT(40) NOT NULL, Остаток LONG NOT NULL)''',
            'ФотографииТоваров': '''CREATE TABLE ФотографииТоваров (Код COUNTER PRIMARY KEY,
                КодТовара LONG NOT NULL, ПутьКФото TEXT(255) NOT NULL, Порядок LONG NOT NULL)''',
            'Избранное': '''CREATE TABLE Избранное (Код COUNTER PRIMARY KEY,
                КодПользователя LONG NOT NULL, КодТовара LONG NOT NULL)''',
            'Отзывы': '''CREATE TABLE Отзывы (Код COUNTER PRIMARY KEY,
                КодПользователя LONG NOT NULL, КодТовара LONG NOT NULL,
                Оценка LONG NOT NULL, ТекстОтзыва MEMO, ДатаСоздания DATETIME NOT NULL)''',
            'ИсторияЗаказов': '''CREATE TABLE ИсторияЗаказов (Код COUNTER PRIMARY KEY,
                КодЗаказа LONG NOT NULL, КодСотрудника LONG NOT NULL,
                СтатусСобытия TEXT(20) NOT NULL, ДатаСоздания DATETIME NOT NULL)''',
            'ВерсииСхемы': 'CREATE TABLE ВерсииСхемы (НомерВерсии LONG PRIMARY KEY)',
        }
        for name, sql in statements.items():
            if name not in tables:
                cur.execute(sql)
        done = cur.execute('SELECT НомерВерсии FROM ВерсииСхемы WHERE НомерВерсии=2').fetchone()
        if done:
            return
        for sql in [
            'ALTER TABLE Категории ADD CONSTRAINT FK_Category_Parent FOREIGN KEY (КодРодителя) REFERENCES Категории (Код)',
            'ALTER TABLE Пользователи ADD CONSTRAINT FK_User_SelectedPoint FOREIGN KEY (ВыбранныйПунктВыдачи) REFERENCES ПунктыВыдачи (Код)',
            'ALTER TABLE ВариантыТоваров ADD CONSTRAINT FK_Variant_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
            'ALTER TABLE ФотографииТоваров ADD CONSTRAINT FK_Image_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
            'ALTER TABLE Избранное ADD CONSTRAINT FK_Favorite_User FOREIGN KEY (КодПользователя) REFERENCES Пользователи (Код)',
            'ALTER TABLE Избранное ADD CONSTRAINT FK_Favorite_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
            'CREATE UNIQUE INDEX UX_Favorite ON Избранное (КодПользователя, КодТовара)',
            'ALTER TABLE Отзывы ADD CONSTRAINT FK_Review_User FOREIGN KEY (КодПользователя) REFERENCES Пользователи (Код)',
            'ALTER TABLE Отзывы ADD CONSTRAINT FK_Review_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
            'CREATE UNIQUE INDEX UX_ProductReview ON Отзывы (КодПользователя, КодТовара)',
            'ALTER TABLE ИсторияЗаказов ADD CONSTRAINT FK_Event_Order FOREIGN KEY (КодЗаказа) REFERENCES Заказы (Код)',
            'ALTER TABLE ИсторияЗаказов ADD CONSTRAINT FK_Event_Actor FOREIGN KEY (КодСотрудника) REFERENCES Пользователи (Код)',
            'ALTER TABLE Корзина ADD CONSTRAINT FK_Cart_Variant FOREIGN KEY (КодВарианта) REFERENCES ВариантыТоваров (Код)',
            'ALTER TABLE СоставЗаказов ADD CONSTRAINT FK_OrderItem_Variant FOREIGN KEY (КодВарианта) REFERENCES ВариантыТоваров (Код)',
            'DROP INDEX UX_Cart_UserProduct ON Корзина',
            'CREATE UNIQUE INDEX UX_Cart_UserVariant ON Корзина (КодПользователя, КодВарианта)',
        ]:
            cur.execute(sql)
        roots = records(cur.execute('SELECT Код, Название FROM Категории WHERE КодРодителя IS NULL'))
        children = {'Электроника': ['Наушники'], 'Одежда': ['Футболки'], 'Обувь': ['Кроссовки'], 'Дом и уют': ['Кружки']}
        child_ids = {}
        for root in roots:
            for title in children.get(root['Название'], []):
                cur.execute('INSERT INTO Категории (Название, КодРодителя) VALUES (?, ?)', title, root['Код'])
                child_ids[root['Код']] = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
        products = records(cur.execute('SELECT Код, Изображение, КодКатегории, Название FROM Товары'))
        for p in products:
            description = {'headphones': 'Полноразмерные наушники для музыки, учёбы и работы. Мягкие амбушюры и регулируемое оголовье.',
                           'shirt': 'Базовая футболка свободного кроя из хлопкового трикотажа. Круглый вырез, прямой силуэт и мягкая ткань.',
                           'shoe': 'Повседневная обувь со шнуровкой и мягкой стелькой. Для прогулок и городского ритма.',
                           'cup': 'Керамическая кружка для любимого напитка. Удобная ручка, гладкая поверхность и лаконичная форма.'}.get(p['Изображение'], p['Название'])
            specs = {'headphones': 'Тип: полноразмерные\nНазначение: музыка и разговоры\nКомплектация: наушники, инструкция',
                     'shirt': 'Состав: хлопок 100%\nКрой: свободный\nУход: деликатная стирка при 30 °C',
                     'shoe': 'Верх: искусственная кожа, текстиль\nПодошва: резина\nЗастёжка: шнуровка',
                     'cup': 'Материал: керамика\nОбъём: 350 мл\nКомплектация: кружка'}.get(p['Изображение'], '')
            cur.execute('UPDATE Товары SET Активен=True, Описание=?, Характеристики=?, Рейтинг=0, КоличествоОтзывов=0, КодКатегории=? WHERE Код=?',
                        description, specs, child_ids.get(p['КодКатегории'], p['КодКатегории']), p['Код'])
            sizes = ['S', 'M', 'L', 'XL'] if p['Изображение'] == 'shirt' else ['38', '39', '40', '41', '42', '43'] if p['Изображение'] == 'shoe' else ['Без размера']
            color = 'Чёрный' if p['Изображение'] == 'headphones' else 'Белый'
            for size in sizes:
                cur.execute('INSERT INTO ВариантыТоваров (КодТовара, Размер, Цвет, Остаток) VALUES (?, ?, ?, ?)', p['Код'], size, color, 20)
            variant_id = cur.execute('SELECT MIN(Код) FROM ВариантыТоваров WHERE КодТовара=?', p['Код']).fetchone()[0]
            cur.execute('UPDATE Корзина SET КодВарианта=? WHERE КодТовара=?', variant_id, p['Код'])
            # Фотографии находятся локально; после установки интернет не нужен.
            images = {'headphones': ['headphones-black.jpg', 'headphones-white.jpg'], 'shirt': ['shirt-flat.jpg', 'shirt-hanger.jpg'],
                      'shoe': ['sneakers.jpg'], 'cup': ['mug-white.jpg', 'mug-studio.jpg']}.get(p['Изображение'], [])
            # Разные цвета не изображают один вариант товара: дополнительные фото тех же типов отмечены как примеры в описании.
            if p['Изображение'] == 'headphones':
                images = ['headphones-black.jpg']
            for order, image in enumerate(images):
                cur.execute('INSERT INTO ФотографииТоваров (КодТовара, ПутьКФото, Порядок) VALUES (?, ?, ?)', p['Код'], 'photos/' + image, order)
        # Старое название термокружки не совпадало с демонстрационным фото керамики.
        cur.execute("UPDATE Товары SET Название='Кружка керамическая, 350 мл' WHERE Изображение='cup' AND Код=4")
        cur.execute("UPDATE Товары SET Название='Кружка для кофе, керамика' WHERE Изображение='cup' AND Код=7")
        cur.execute('INSERT INTO ВерсииСхемы (НомерВерсии) VALUES (2)')
    print('Access schema updated to v2; existing accounts and orders retained.')

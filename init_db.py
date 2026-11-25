"""Создаёт таблицы, реальные внешние ключи и демонстрационный каталог."""
import subprocess
from db import BASE_DIR, DB_PATH, connection

SCHEMA = [
    '''CREATE TABLE Категории (
        Код COUNTER CONSTRAINT PK_Categories PRIMARY KEY,
        Название TEXT(80) NOT NULL)''',
    '''CREATE TABLE Пользователи (
        Код COUNTER CONSTRAINT PK_Users PRIMARY KEY,
        Имя TEXT(80) NOT NULL,
        ЭлектроннаяПочта TEXT(254) NOT NULL,
        ХешПароля TEXT(255) NOT NULL,
        ДатаСоздания DATETIME NOT NULL,
        Роль TEXT(20) NOT NULL,
        КодПунктаВыдачи LONG)''',
    'CREATE UNIQUE INDEX UX_Users_Email ON Пользователи (ЭлектроннаяПочта)',
    '''CREATE TABLE Товары (
        Код COUNTER CONSTRAINT PK_Products PRIMARY KEY,
        КодКатегории LONG NOT NULL,
        Бренд TEXT(80) NOT NULL,
        Название TEXT(150) NOT NULL,
        Цена CURRENCY NOT NULL,
        СтараяЦена CURRENCY NOT NULL,
        Рейтинг DOUBLE NOT NULL,
        КоличествоОтзывов LONG NOT NULL,
        Изображение TEXT(50) NOT NULL,
        Оттенок TEXT(20) NOT NULL)''',
    '''CREATE TABLE Корзина (
        Код COUNTER CONSTRAINT PK_CartItems PRIMARY KEY,
        КодПользователя LONG NOT NULL,
        КодТовара LONG NOT NULL,
        Количество LONG NOT NULL)''',
    'CREATE UNIQUE INDEX UX_Cart_UserProduct ON Корзина (КодПользователя, КодТовара)',
    # Эти ограничения создают связи непосредственно в Access.
    'ALTER TABLE Товары ADD CONSTRAINT FK_Product_Category FOREIGN KEY (КодКатегории) REFERENCES Категории (Код)',
    'ALTER TABLE Корзина ADD CONSTRAINT FK_Cart_User FOREIGN KEY (КодПользователя) REFERENCES Пользователи (Код)',
    'ALTER TABLE Корзина ADD CONSTRAINT FK_Cart_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
    '''CREATE TABLE ПунктыВыдачи (
        Код COUNTER CONSTRAINT PK_Pickup PRIMARY KEY,
        Город TEXT(80) NOT NULL, Адрес TEXT(200) NOT NULL, ЧасыРаботы TEXT(80) NOT NULL)''',
    'ALTER TABLE Пользователи ADD CONSTRAINT FK_User_Pickup FOREIGN KEY (КодПунктаВыдачи) REFERENCES ПунктыВыдачи (Код)',
    '''CREATE TABLE Заказы (
        Код COUNTER CONSTRAINT PK_Orders PRIMARY KEY,
        КодПользователя LONG NOT NULL, КодПунктаВыдачи LONG NOT NULL,
        ДатаСоздания DATETIME NOT NULL, СтатусЗаказа TEXT(20) NOT NULL,
        КодПолучения TEXT(12) NOT NULL, ОбщаяСумма CURRENCY NOT NULL,
        КлючОформления TEXT(64) NOT NULL)''',
    'CREATE UNIQUE INDEX UX_Order_Token ON Заказы (КлючОформления)',
    'ALTER TABLE Заказы ADD CONSTRAINT FK_Order_User FOREIGN KEY (КодПользователя) REFERENCES Пользователи (Код)',
    'ALTER TABLE Заказы ADD CONSTRAINT FK_Order_Pickup FOREIGN KEY (КодПунктаВыдачи) REFERENCES ПунктыВыдачи (Код)',
    '''CREATE TABLE СоставЗаказов (
        Код COUNTER CONSTRAINT PK_OrderItems PRIMARY KEY,
        КодЗаказа LONG NOT NULL, КодТовара LONG NOT NULL,
        НазваниеТовара TEXT(240) NOT NULL, ЦенаЗаЕдиницу CURRENCY NOT NULL, Количество LONG NOT NULL)''',
    'ALTER TABLE СоставЗаказов ADD CONSTRAINT FK_Item_Order FOREIGN KEY (КодЗаказа) REFERENCES Заказы (Код)',
    'ALTER TABLE СоставЗаказов ADD CONSTRAINT FK_Item_Product FOREIGN KEY (КодТовара) REFERENCES Товары (Код)',
]

def initialize():
    if not DB_PATH.exists():
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(BASE_DIR / 'create_database.ps1'),
                        '-DatabasePath', str(DB_PATH)], check=True)
    with connection() as db:
        existing = {row.table_name for row in db.cursor().tables(tableType='TABLE')}
        if 'Пользователи' in existing:
            print('Access database is ready:', DB_PATH)
            existing_database = True
        else:
            existing_database = False
    if existing_database:
        from migrations import migrate
        migrate()
        extend_catalog()
        from migrations import migrate_store
        migrate_store()
        seed_pickup_points()
        return
    with connection() as db:
        cur = db.cursor()
        for sql in SCHEMA:
            cur.execute(sql)
        for title in ['Электроника', 'Одежда', 'Обувь', 'Дом и уют']:
            cur.execute('INSERT INTO Категории (Название) VALUES (?)', title)
        categories = {r.Название: r.Код for r in cur.execute('SELECT Код, Название FROM Категории')}
        products = [
            ('Электроника', 'SOUND', 'Беспроводные наушники с микрофоном', 2490, 4990, 4.8, 124, 'headphones', '#eee8f5'),
            ('Одежда', 'BASIC', 'Футболка свободного кроя, хлопок', 890, 1590, 4.9, 87, 'shirt', '#e9edf0'),
            ('Обувь', 'STEP', 'Кроссовки на каждый день', 3290, 5490, 4.7, 216, 'shoe', '#efeee9'),
            ('Дом и уют', 'HOME', 'Термокружка, 450 мл', 1190, 1990, 4.9, 63, 'cup', '#e8efeb'),
            ('Электроника', 'SOUND', 'Наушники полноразмерные, Bluetooth', 3690, 6290, 4.8, 92, 'headphones', '#e8edf5'),
            ('Одежда', 'BASIC', 'Футболка оверсайз, унисекс', 1090, 1890, 4.8, 153, 'shirt', '#f2e8ec'),
            ('Дом и уют', 'HOME', 'Кружка для кофе с крышкой', 790, 1290, 4.6, 45, 'cup', '#efe9e0'),
            ('Обувь', 'STEP', 'Кеды с мягкой стелькой', 2790, 4590, 4.8, 78, 'shoe', '#e9e8f2'),
        ]
        for category, *values in products:
            cur.execute('''INSERT INTO Товары
                (КодКатегории, Бренд, Название, Цена, СтараяЦена, Рейтинг, КоличествоОтзывов, Изображение, Оттенок)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', categories[category], *values)
    print('Created Access database, relationships and demo products:', DB_PATH)
    from migrations import migrate
    migrate()
    extend_catalog()
    from migrations import migrate_store
    migrate_store()
    seed_pickup_points()

def seed_pickup_points():
    """Демонстрационные адреса: 1–4 пункта в каждом из шести городов."""
    addresses = {
        'Ижевск': ['Учебная улица, 10', 'Садовая улица, 24', 'Школьная улица, 7'],
        'Москва': ['Учебная улица, 12', 'Парковая улица, 18', 'Садовая улица, 3', 'Школьная улица, 21'],
        'Санкт-Петербург': ['Учебная улица, 8', 'Садовая улица, 16'],
        'Казань': ['Учебная улица, 5', 'Парковая улица, 9', 'Школьная улица, 14'],
        'Самара': ['Учебная улица, 11', 'Садовая улица, 6'],
        'Екатеринбург': ['Учебная улица, 15'],
    }
    with connection() as db:
        cur = db.cursor()
        for city, choices in addresses.items():
            existing = {r[0] for r in cur.execute('SELECT Адрес FROM ПунктыВыдачи WHERE Город=?', city)}
            for address in choices:
                if len(existing) >= len(choices):
                    break
                if address not in existing:
                    cur.execute('INSERT INTO ПунктыВыдачи (Город, Адрес, ЧасыРаботы) VALUES (?, ?, ?)',
                                city, address, 'Ежедневно 09:00–21:00')
                    existing.add(address)

def extend_catalog():
    """Семь дополнительных товаров. Повторный запуск не создаёт дубликаты."""
    additions = [
        ('Одежда', 'Рюкзаки', 'CITY', 'Рюкзак городской, 18 л', 1890, 2490, 'backpack', 'Синий', ['Без размера'],
         'Городской рюкзак с мягкими лямками, передним карманом и отделением для ноутбука.', 'Объём: 18 л\nМатериал: полиэстер\nОтделение: для ноутбука до 15 дюймов'),
        ('Электроника', 'Клавиатуры', 'INPUT', 'Клавиатура механическая компактная', 2990, 3990, 'keyboard', 'Серый', ['Без размера'],
         'Компактная проводная клавиатура для работы и игр. Съёмный кабель USB-C.', 'Подключение: USB-C\nТип: механическая\nРаскладка: русская и английская'),
        ('Электроника', 'Мыши', 'CLICK', 'Мышь беспроводная бесшумная', 990, 1490, 'mouse', 'Чёрный', ['Без размера'],
         'Беспроводная мышь с тихими кнопками и USB-приёмником для повседневной работы.', 'Подключение: USB-приёмник\nКнопки: 3\nПитание: батарейка AA'),
        ('Дом и уют', 'Бутылки', 'FLOW', 'Бутылка для воды, 750 мл', 690, 990, 'bottle', 'Зелёный', ['Без размера'],
         'Лёгкая многоразовая бутылка с завинчивающейся крышкой и петлёй для переноски.', 'Объём: 750 мл\nМатериал: тритан\nНазначение: холодные напитки'),
        ('Дом и уют', 'Освещение', 'LIGHT', 'Лампа настольная светодиодная', 1590, 2190, 'lamp', 'Белый', ['Без размера'],
         'Настольный светильник с регулируемым наклоном для чтения и учёбы.', 'Источник света: светодиоды\nПитание: USB\nКорпус: пластик и металл'),
        ('Одежда', 'Толстовки', 'COZY', 'Худи свободного кроя с капюшоном', 2290, 3290, 'hoodie', 'Бежевый', ['S', 'M', 'L', 'XL'],
         'Мягкое худи с капюшоном, карманом-кенгуру и эластичными манжетами.', 'Состав: хлопок 80%, полиэстер 20%\nКрой: свободный\nУход: стирка при 30 °C'),
        ('Электроника', 'Часы', 'TIME', 'Часы электронные спортивные', 1490, 1990, 'watch', 'Чёрный', ['Без размера'],
         'Электронные наручные часы с мягким ремешком, подсветкой, будильником и секундомером.', 'Механизм: электронный\nРемешок: силикон\nФункции: часы, будильник, секундомер'),
    ]
    with connection() as db:
        cur = db.cursor()
        for parent, category, brand, title, price, old_price, picture, color, sizes, description, specs in additions:
            if cur.execute('SELECT Код FROM Товары WHERE Бренд=? AND Название=?', brand, title).fetchone():
                continue
            parent_id = cur.execute('SELECT Код FROM Категории WHERE Название=? AND КодРодителя IS NULL', parent).fetchone()[0]
            child = cur.execute('SELECT Код FROM Категории WHERE Название=? AND КодРодителя=?', category, parent_id).fetchone()
            if child:
                category_id = child[0]
            else:
                cur.execute('INSERT INTO Категории (Название, КодРодителя) VALUES (?, ?)', category, parent_id)
                category_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
            cur.execute('''INSERT INTO Товары
                (КодКатегории, Бренд, Название, Цена, СтараяЦена, Рейтинг, КоличествоОтзывов,
                 Изображение, Оттенок, Описание, Характеристики, Активен)
                VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, True)''',
                category_id, brand, title, price, old_price, picture, '#f3f1f5', description, specs)
            product_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
            for size in sizes:
                cur.execute('INSERT INTO ВариантыТоваров (КодТовара, Размер, Цвет, Остаток) VALUES (?, ?, ?, ?)',
                            product_id, size, color, 20)

if __name__ == '__main__':
    initialize()

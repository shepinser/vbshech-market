"""Создаёт таблицы, реальные внешние ключи и демонстрационный каталог."""
import subprocess
from db import BASE_DIR, DB_PATH, connection

SCHEMA = [
    '''CREATE TABLE Categories (
        ID COUNTER CONSTRAINT PK_Categories PRIMARY KEY,
        Title TEXT(80) NOT NULL)''',
    '''CREATE TABLE Users (
        ID COUNTER CONSTRAINT PK_Users PRIMARY KEY,
        DisplayName TEXT(80) NOT NULL,
        Email TEXT(254) NOT NULL,
        PasswordHash TEXT(255) NOT NULL,
        CreatedAt DATETIME NOT NULL,
        UserRole TEXT(20) NOT NULL,
        PickupID LONG)''',
    'CREATE UNIQUE INDEX UX_Users_Email ON Users (Email)',
    '''CREATE TABLE Products (
        ID COUNTER CONSTRAINT PK_Products PRIMARY KEY,
        CategoryID LONG NOT NULL,
        Brand TEXT(80) NOT NULL,
        Title TEXT(150) NOT NULL,
        Price CURRENCY NOT NULL,
        OldPrice CURRENCY NOT NULL,
        Rating DOUBLE NOT NULL,
        Reviews LONG NOT NULL,
        Picture TEXT(50) NOT NULL,
        Tint TEXT(20) NOT NULL)''',
    '''CREATE TABLE CartItems (
        ID COUNTER CONSTRAINT PK_CartItems PRIMARY KEY,
        UserID LONG NOT NULL,
        ProductID LONG NOT NULL,
        Quantity LONG NOT NULL)''',
    'CREATE UNIQUE INDEX UX_Cart_UserProduct ON CartItems (UserID, ProductID)',
    # Эти ограничения создают связи непосредственно в Access.
    'ALTER TABLE Products ADD CONSTRAINT FK_Product_Category FOREIGN KEY (CategoryID) REFERENCES Categories (ID)',
    'ALTER TABLE CartItems ADD CONSTRAINT FK_Cart_User FOREIGN KEY (UserID) REFERENCES Users (ID)',
    'ALTER TABLE CartItems ADD CONSTRAINT FK_Cart_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
    '''CREATE TABLE PickupPoints (
        ID COUNTER CONSTRAINT PK_Pickup PRIMARY KEY,
        City TEXT(80) NOT NULL, Address TEXT(200) NOT NULL, Hours TEXT(80) NOT NULL)''',
    'ALTER TABLE Users ADD CONSTRAINT FK_User_Pickup FOREIGN KEY (PickupID) REFERENCES PickupPoints (ID)',
    '''CREATE TABLE OrderHeaders (
        ID COUNTER CONSTRAINT PK_Orders PRIMARY KEY,
        UserID LONG NOT NULL, PickupID LONG NOT NULL,
        CreatedAt DATETIME NOT NULL, OrderStatus TEXT(20) NOT NULL,
        PickupCode TEXT(12) NOT NULL, TotalAmount CURRENCY NOT NULL,
        CheckoutToken TEXT(64) NOT NULL)''',
    'CREATE UNIQUE INDEX UX_Order_Token ON OrderHeaders (CheckoutToken)',
    'ALTER TABLE OrderHeaders ADD CONSTRAINT FK_Order_User FOREIGN KEY (UserID) REFERENCES Users (ID)',
    'ALTER TABLE OrderHeaders ADD CONSTRAINT FK_Order_Pickup FOREIGN KEY (PickupID) REFERENCES PickupPoints (ID)',
    '''CREATE TABLE OrderItems (
        ID COUNTER CONSTRAINT PK_OrderItems PRIMARY KEY,
        OrderID LONG NOT NULL, ProductID LONG NOT NULL,
        ProductTitle TEXT(240) NOT NULL, UnitPrice CURRENCY NOT NULL, Quantity LONG NOT NULL)''',
    'ALTER TABLE OrderItems ADD CONSTRAINT FK_Item_Order FOREIGN KEY (OrderID) REFERENCES OrderHeaders (ID)',
    'ALTER TABLE OrderItems ADD CONSTRAINT FK_Item_Product FOREIGN KEY (ProductID) REFERENCES Products (ID)',
]

def initialize():
    if not DB_PATH.exists():
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(BASE_DIR / 'create_database.ps1'),
                        '-DatabasePath', str(DB_PATH)], check=True)
    with connection() as db:
        existing = {row.table_name for row in db.cursor().tables(tableType='TABLE')}
        if 'Users' in existing:
            print('Access database is ready:', DB_PATH)
            existing_database = True
        else:
            existing_database = False
    if existing_database:
        from migrations import migrate
        migrate()
        return
    with connection() as db:
        cur = db.cursor()
        for sql in SCHEMA:
            cur.execute(sql)
        for title in ['Электроника', 'Одежда', 'Обувь', 'Дом и уют']:
            cur.execute('INSERT INTO Categories (Title) VALUES (?)', title)
        categories = {r.Title: r.ID for r in cur.execute('SELECT ID, Title FROM Categories')}
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
            cur.execute('''INSERT INTO Products
                (CategoryID, Brand, Title, Price, OldPrice, Rating, Reviews, Picture, Tint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', categories[category], *values)
    print('Created Access database, relationships and demo products:', DB_PATH)
    from migrations import migrate
    migrate()

if __name__ == '__main__':
    initialize()

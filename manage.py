"""Первый администратор создаётся владельцем на компьютере, без публичной регистрации."""
import getpass
from datetime import datetime
from werkzeug.security import generate_password_hash
from db import connection
from init_db import initialize
import re

def create_admin():
    initialize()
    email = input('Admin email: ').strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise SystemExit('Invalid email')
    with connection() as db:
        cur = db.cursor()
        if cur.execute("SELECT ID FROM Users WHERE UserRole='admin'").fetchone():
            raise SystemExit('Admin already exists. Assign roles through the admin panel.')
        if cur.execute('SELECT ID FROM Users WHERE Email=?', email).fetchone():
            cur.execute("UPDATE Users SET UserRole='admin', PickupID=NULL WHERE Email=?", email)
        else:
            password = getpass.getpass('Password (8-128 characters): ')
            if not 8 <= len(password) <= 128:
                raise SystemExit('Invalid password length')
            if password != getpass.getpass('Repeat password: '):
                raise SystemExit('Passwords do not match')
            cur.execute('INSERT INTO Users (DisplayName, Email, PasswordHash, CreatedAt, UserRole) VALUES (?, ?, ?, ?, ?)',
                        'Администратор', email, generate_password_hash(password), datetime.now(), 'admin')
    print('Admin account is ready. Sign in through the regular login page.')

if __name__ == '__main__':
    create_admin()

"""Первый этап ВБШЕЧ: каталог, аккаунты и персональная корзина в Access."""
import hmac
import re
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import pyodbc
from db import BASE_DIR, connection, records

app = Flask(__name__)
secret_file = BASE_DIR / '.secret'
if not secret_file.exists():
    secret_file.write_text(secrets.token_hex(32), encoding='ascii')
app.secret_key = secret_file.read_text(encoding='ascii').strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=24 * 1024 * 1024)

@app.template_filter('rub')
def rub(value):
    return f'{value:,.0f}'.replace(',', ' ') + ' ₽'

@app.before_request
def prepare_request():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(24)
    if request.method == 'POST' and not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
        abort(400, 'Срок действия формы истёк. Обновите страницу.')
    g.user = None
    g.cart_count = 0
    g.favorites = set()
    if session.get('user_id'):
        with connection() as db:
            users = records(db.cursor().execute('SELECT ID, DisplayName, Email, UserRole, PickupID, SelectedPickupID, SelectedCity FROM Users WHERE ID=?', session['user_id']))
            if users:
                g.user = users[0]
                count = db.cursor().execute('SELECT SUM(Quantity) FROM CartItems WHERE UserID=?', g.user['ID']).fetchone()[0]
                g.cart_count = count or 0
                g.favorites = {r.ProductID for r in db.cursor().execute('SELECT ProductID FROM Favorites WHERE UserID=?', g.user['ID'])}
            else:
                session.pop('user_id', None)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash('Войдите, чтобы пользоваться корзиной.')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped

@app.route('/')
def index():
    from commerce import catalog
    return catalog()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('account'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not 2 <= len(name) <= 80:
            flash('Имя должно содержать от 2 до 80 символов.')
        elif len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            flash('Введите корректный email.')
        elif not 8 <= len(password) <= 128:
            flash('Пароль должен содержать от 8 до 128 символов.')
        elif password != request.form.get('confirm'):
            flash('Пароли не совпадают.')
        else:
            try:
                with connection() as db:
                    cur = db.cursor()
                    if cur.execute('SELECT ID FROM Users WHERE Email=?', email).fetchone():
                        flash('Этот email уже зарегистрирован. Попробуйте войти.')
                        return render_template('auth.html', mode='register')
                    # Значения передаются отдельно от SQL; в БД записывается хеш пароля.
                    cur.execute('INSERT INTO Users (DisplayName, Email, PasswordHash, CreatedAt, UserRole) VALUES (?, ?, ?, ?, ?)',
                                name, email, generate_password_hash(password), datetime.now(), 'customer')
                    user_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                location = {k: session[k] for k in ['selected_city', 'selected_pickup_id'] if k in session}
                session.clear()
                session.update(location)
                session['user_id'] = user_id
                flash('Аккаунт создан. Добро пожаловать!')
                return redirect(url_for('index'))
            except pyodbc.IntegrityError:
                flash('Этот email уже зарегистрирован.')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('account'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()[:254]
        password = request.form.get('password', '')
        with connection() as db:
            users = records(db.cursor().execute('SELECT ID, PasswordHash FROM Users WHERE Email=?', email))
        if len(password) <= 128 and users and check_password_hash(users[0]['PasswordHash'], password):
            location = {k: session[k] for k in ['selected_city', 'selected_pickup_id'] if k in session}
            session.clear()
            session.update(location)
            session['user_id'] = users[0]['ID']
            return redirect(url_for('index'))
        flash('Неверный email или пароль.')
    return render_template('auth.html', mode='login')

@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/account')
@login_required
def account():
    return render_template('account.html')

def role_required(*roles):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user['UserRole'] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate

from commerce import register_routes
register_routes(app, login_required, role_required)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5055, debug=False)

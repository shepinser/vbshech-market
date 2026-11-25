"""Запуск сайта, регистрация, вход и проверка прав пользователя."""
import hmac
import re
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import pyodbc
from db import BASE_DIR, connection, records, close_connection

app = Flask(__name__)
app.teardown_appcontext(close_connection)
secret_file = BASE_DIR / '.secret'
if not secret_file.exists():
    secret_file.write_text(secrets.token_hex(32), encoding='ascii')
app.secret_key = secret_file.read_text(encoding='ascii').strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=24 * 1024 * 1024,
                  SEND_FILE_MAX_AGE_DEFAULT=3600)

@app.template_filter('rub')
def rub(value):
    return f'{value:,.0f}'.replace(',', ' ') + ' ₽'

@app.before_request
def prepare_request():
    g.user = None
    g.cart_count = 0
    g.favorites = set()
    g.cart_variants = {}
    # Фотографии, стили и скрипты не требуют чтения аккаунта из базы.
    if request.endpoint == 'static':
        return
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(24)
    if request.method == 'POST' and not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
        abort(400, 'Срок действия формы истёк. Обновите страницу.')
    if session.get('user_id') and request.endpoint != 'suggestions':
        with connection() as db:
            users = records(db.cursor().execute('SELECT Код, Имя, ЭлектроннаяПочта, Роль, КодПунктаВыдачи, ВыбранныйПунктВыдачи, ВыбранныйГород, ЗаявкаПоставщика, НазваниеКомпании FROM Пользователи WHERE Код=?', session['user_id']))
            if users:
                g.user = users[0]
                g.cart_variants = {r.КодВарианта: r.Количество for r in db.cursor().execute(
                    'SELECT КодВарианта, Количество FROM Корзина WHERE КодПользователя=?', g.user['Код'])}
                g.cart_count = sum(g.cart_variants.values())
                g.favorites = {r.КодТовара for r in db.cursor().execute('SELECT КодТовара FROM Избранное WHERE КодПользователя=?', g.user['Код'])}
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
    # GET открывает страницу; POST приходит после отправки заполненной формы.
    if g.user:
        return redirect(url_for('account'))
    if request.method == 'POST':
        # Имена name, email и password совпадают с name полей в auth.html.
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
                    if cur.execute('SELECT Код FROM Пользователи WHERE ЭлектроннаяПочта=?', email).fetchone():
                        flash('Этот email уже зарегистрирован. Попробуйте войти.')
                        return render_template('auth.html', mode='register')
                    # Значения передаются отдельно от SQL; в БД записывается хеш пароля.
                    cur.execute('INSERT INTO Пользователи (Имя, ЭлектроннаяПочта, ХешПароля, ДатаСоздания, Роль) VALUES (?, ?, ?, ?, ?)',
                                name, email, generate_password_hash(password), datetime.now(), 'customer')
                    # Access возвращает код только что добавленного пользователя.
                    user_id = int(cur.execute('SELECT @@IDENTITY').fetchone()[0])
                location = {k: session[k] for k in ['selected_city', 'selected_pickup_id'] if k in session}
                session.clear()
                session.update(location)
                # Запоминаем код аккаунта, чтобы после перехода пользователь оставался в системе.
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
            users = records(db.cursor().execute('SELECT Код, ХешПароля FROM Пользователи WHERE ЭлектроннаяПочта=?', email))
        if len(password) <= 128 and users and check_password_hash(users[0]['ХешПароля'], password):
            location = {k: session[k] for k in ['selected_city', 'selected_pickup_id'] if k in session}
            session.clear()
            session.update(location)
            session['user_id'] = users[0]['Код']
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
            if g.user['Роль'] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate

from commerce import register_routes
register_routes(app, login_required, role_required)

if __name__ == '__main__':
    from init_db import initialize
    from delivery import start_delivery_worker
    initialize()
    start_delivery_worker()
    app.run(host='127.0.0.1', port=5055, debug=False)

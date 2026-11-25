"""Учебная доставка: каждые 30 секунд один случайный заказ готов к выдаче."""
from datetime import datetime, timedelta
import logging
import secrets
from threading import Event, Lock, Thread
from db import connection

_lock = Lock()
_stop = Event()
_worker = None

def deliver_one(now=None):
    now = now or datetime.now()
    with _lock, connection() as db:
        cur = db.cursor()
        # Общие часы в Access исключают повторную доставку из нескольких вкладок.
        cur.execute('UPDATE НастройкиМагазина SET СледующаяДоставка=? WHERE Код=1 AND СледующаяДоставка<=?',
                    now + timedelta(seconds=30), now)
        if cur.rowcount != 1:
            return None
        pending = [r[0] for r in cur.execute("SELECT Код FROM Заказы WHERE СтатусЗаказа IN ('created','shipping')")]
        if not pending:
            return None
        order_id = secrets.choice(pending)
        cur.execute("UPDATE Заказы SET СтатусЗаказа='ready' WHERE Код=? AND СтатусЗаказа IN ('created','shipping')", order_id)
        if cur.rowcount != 1:
            return None
        cur.execute('INSERT INTO ИсторияЗаказов (КодЗаказа, КодСотрудника, СтатусСобытия, ДатаСоздания) VALUES (?, NULL, ?, ?)',
                    order_id, 'ready', now)
        return order_id

def start_delivery_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    def run():
        while not _stop.wait(30):
            try:
                deliver_one()
            except Exception:
                logging.exception('Не удалось выполнить учебную доставку')
    _worker = Thread(target=run, name='store-delivery', daemon=True)
    _worker.start()

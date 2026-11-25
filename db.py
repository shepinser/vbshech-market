"""Соединение с настоящей базой Microsoft Access через установленный ODBC-драйвер."""
import os
from contextlib import contextmanager
from pathlib import Path
import pyodbc
from flask import g, has_request_context

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('VBSH_DATABASE', BASE_DIR / 'database.accdb'))

@contextmanager
def connection():
    # В пределах одного запроса используем одно соединение с Access.
    shared = has_request_context()
    db = g.get('access_db') if shared else None
    if db is None:
        db = pyodbc.connect(
            'DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};'
            f'DBQ={DB_PATH};', autocommit=False
        )
        if shared:
            g.access_db = db
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if not shared:
            db.close()

def close_connection(error=None):
    db = g.pop('access_db', None)
    if db is not None:
        db.close()

def records(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]

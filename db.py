"""Соединение с настоящей базой Microsoft Access через установленный ODBC-драйвер."""
import os
from contextlib import contextmanager
from pathlib import Path
import pyodbc

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('VBSH_DATABASE', BASE_DIR / 'database.accdb'))

@contextmanager
def connection():
    db = pyodbc.connect(
        'DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};'
        f'DBQ={DB_PATH};', autocommit=False
    )
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def records(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]

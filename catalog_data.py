"""Чтение каталога и расчёт рейтингов по реальным отзывам."""
from flask import g, url_for
from db import records

# Одинаковый поиск для русских названий и их записи латиницей: нау / nau.
TRANSLIT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
    ['a','b','v','g','d','e','e','zh','z','i','i','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']))

def search_key(value):
    return ''.join(TRANSLIT.get(char, char) for char in value.casefold()).strip()

def search_items(db):
    return [{'title': row.Бренд + ' / ' + row.Название,
             'key': search_key(row.Бренд + ' / ' + row.Название),
             'id': row.Код, 'url': url_for('product', product_id=row.Код)}
            for row in db.cursor().execute('SELECT Код, Название, Бренд FROM Товары WHERE Активен=True ORDER BY Код')]

def categories(db):
    return records(db.cursor().execute('SELECT * FROM Категории ORDER BY Код'))

def category_ids(all_categories, selected):
    found = {selected}
    while True:
        updated = found | {c['Код'] for c in all_categories if c['КодРодителя'] in found}
        if updated == found:
            return sorted(found)
        found = updated

def enrich(db, products):
    if not products:
        return products
    ids = [p['Код'] for p in products]
    marks = ','.join('?' for _ in ids)
    images = records(db.cursor().execute(f'SELECT * FROM ФотографииТоваров WHERE КодТовара IN ({marks}) ORDER BY Порядок, Код', ids))
    variants = records(db.cursor().execute(f'SELECT * FROM ВариантыТоваров WHERE КодТовара IN ({marks}) ORDER BY Код', ids))
    ratings = records(db.cursor().execute(f'SELECT КодТовара, AVG(Оценка) AS AverageScore, COUNT(*) AS ReviewCount FROM Отзывы WHERE КодТовара IN ({marks}) GROUP BY КодТовара', ids))
    stats = {r['КодТовара']: r for r in ratings}
    for p in products:
        p['images'] = [url_for('static', filename=i['ПутьКФото']) for i in images if i['КодТовара'] == p['Код']]
        if not p['images']:
            p['images'] = [url_for('static', filename='products/' + p['Изображение'] + '.svg')]
        p['variants'] = [v for v in variants if v['КодТовара'] == p['Код']]
        for v in p['variants']:
            v['cart_quantity'] = getattr(g, 'cart_variants', {}).get(v['Код'], 0)
        p['cart_variant'] = next((v for v in p['variants'] if v['cart_quantity']), None)
        p['cart_quantity'] = sum(v['cart_quantity'] for v in p['variants'])
        p['stock'] = sum(max(0, v['Остаток']) for v in p['variants'])
        p['rating'] = round(stats[p['Код']]['AverageScore'], 1) if p['Код'] in stats else None
        p['review_count'] = stats[p['Код']]['ReviewCount'] if p['Код'] in stats else 0
        p['favorite'] = p['Код'] in getattr(g, 'favorites', set())
    return products

"""Чтение каталога и расчёт рейтингов по реальным отзывам."""
from flask import g, url_for
from db import records

def categories(db):
    return records(db.cursor().execute('SELECT * FROM Categories ORDER BY ID'))

def category_ids(all_categories, selected):
    found = {selected}
    while True:
        updated = found | {c['ID'] for c in all_categories if c['ParentID'] in found}
        if updated == found:
            return sorted(found)
        found = updated

def enrich(db, products):
    if not products:
        return products
    ids = [p['ID'] for p in products]
    marks = ','.join('?' for _ in ids)
    images = records(db.cursor().execute(f'SELECT * FROM ProductImages WHERE ProductID IN ({marks}) ORDER BY SortOrder, ID', ids))
    variants = records(db.cursor().execute(f'SELECT * FROM ProductVariants WHERE ProductID IN ({marks}) ORDER BY ID', ids))
    ratings = records(db.cursor().execute(f'SELECT ProductID, AVG(Score) AS AverageScore, COUNT(*) AS ReviewCount FROM ProductReviews WHERE ProductID IN ({marks}) GROUP BY ProductID', ids))
    stats = {r['ProductID']: r for r in ratings}
    for p in products:
        p['images'] = [url_for('static', filename=i['ImagePath']) for i in images if i['ProductID'] == p['ID']]
        if not p['images']:
            p['images'] = [url_for('static', filename='products/' + p['Picture'] + '.svg')]
        p['variants'] = [v for v in variants if v['ProductID'] == p['ID']]
        p['stock'] = sum(max(0, v['Stock']) for v in p['variants'])
        p['rating'] = round(stats[p['ID']]['AverageScore'], 1) if p['ID'] in stats else None
        p['review_count'] = stats[p['ID']]['ReviewCount'] if p['ID'] in stats else 0
        p['favorite'] = p['ID'] in getattr(g, 'favorites', set())
    return products

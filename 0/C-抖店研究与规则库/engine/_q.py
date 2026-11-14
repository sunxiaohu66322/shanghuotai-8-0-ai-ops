# -*- coding: utf-8 -*-
import sqlite3
db = r'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3.db'
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
cur = c.cursor()
tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
print('TABLES:', tabs)
q = """select p.product_id, p.original_title, p.status,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='main') m,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='detail') d,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='sku') s,
 (select count(*) from v3_sku k where k.product_id=p.product_id) sku
 from v3_product p"""
rows = [dict(r) for r in cur.execute(q)]
print('TOTAL', len(rows))
ok = [r for r in rows if r['m'] >= 1 and r['d'] >= 1]
print('有主图+详情:', len(ok))
for r in ok[:30]:
    print(r['product_id'], r['status'], 'm', r['m'], 'd', r['d'], 'sku', r['sku'], (r['original_title'] or '')[:40])

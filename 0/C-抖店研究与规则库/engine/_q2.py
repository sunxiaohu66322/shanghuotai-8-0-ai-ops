# -*- coding: utf-8 -*-
import sqlite3
db = r'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3.db'
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
for pid in (1390, 1391, 1392, 1393):
    p = c.execute("select * from v3_product where product_id=?", (pid,)).fetchone()
    print('=' * 60)
    print(pid, p['status'], (p['original_title'] or '')[:50])
    for s in c.execute("select * from v3_sku where product_id=? order by seq", (pid,)):
        print('   sku', s['sku_id'], 'spec=', s['spec'], 'code=', s['sku_code'], 'price=', s['price'], 'stock=', s['stock'])

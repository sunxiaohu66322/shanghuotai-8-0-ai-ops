# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3.db")
Q = """
select p.product_id, substr(p.original_title,1,26) t,
 (select count(*) from v3_sku s where s.product_id=p.product_id) nsku,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='video' and i.dl_state='ok') nvid,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='main' and i.dl_state='ok') nmain,
 (select count(*) from v3_image i where i.product_id=p.product_id and i.role='sku' and i.dl_state='ok') nskimg
from v3_product p
where (select count(*) from v3_image i where i.product_id=p.product_id and i.role='video' and i.dl_state='ok') > 0
  and (select count(*) from v3_sku s where s.product_id=p.product_id) between 2 and 6
order by p.product_id desc limit 14
"""
print("%-8s %-28s %5s %5s %6s %7s" % ("pid", "title", "sku", "vid", "main", "skuimg"))
for r in c.execute(Q):
    print("%-8s %-28s %5d %5d %6d %7d" % (r[0], r[1], r[2], r[3], r[4], r[5]))
c.close()

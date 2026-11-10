import json,glob,os
rows=[]
for f in sorted(glob.glob("plan-*.json"), key=lambda x:int(x.split("-")[1].split(".")[0])):
    try:
        p=json.load(open(f,encoding="utf-8"))
    except Exception as e:
        rows.append((f,"ERR",str(e)[:30],"","","","")); continue
    sk=p.get("skus") or []
    imgs=sum(1 for s in sk if s.get("img"))
    pics=p.get("pics") or p.get("images") or []
    det=p.get("detail") or []
    prices=[s.get("price") for s in sk if s.get("price")]
    dims=set()
    for s in sk:
        sp=s.get("spec") or []
        for i,v in enumerate(sp): dims.add(i)
    rows.append((f,len(sk),imgs,f"{min(prices) if prices else 0}~{max(prices) if prices else 0}",
                 len(pics),len(det), f"{len(skusdim:=dims)}维" ))
print(f"{'file':16}{'SKU':>5}{'有图':>5}{' 价格区间':>16}{'主图':>5}{'详情':>5}{'维度':>5}")
for r in rows:
    print(f"{r[0]:16}{str(r[1]):>5}{str(r[2]):>5}{str(r[3]):>16}{str(r[4]):>5}{str(r[5]):>5}{str(r[6] if len(r)>6 else ''):>5}")

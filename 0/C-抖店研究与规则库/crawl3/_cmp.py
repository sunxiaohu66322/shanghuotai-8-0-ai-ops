import json
live = {"1391":("3851698328731517051",78),"1390":("3851703205792710708",496),
        "1393":("3851699887888531936",31793),"1409":("3851703132778266995",198934),
        "1424":("3851715918895906838",55)}
print(f"{'plan':6}{'SKU':>4}{'plan库存和':>12}{'线上ID':>22}{'线上库存':>10}{'差':>8}  价格")
for k,(pid,ls) in live.items():
    p=json.load(open(f"plan-{k}.json",encoding="utf-8"))
    sk=p.get("skus") or []
    s=sum(int(x.get("stock") or 0) for x in sk)
    pr=[float(x.get("price") or 0) for x in sk]
    print(f"{k:6}{len(sk):>4}{s:>12}{pid:>22}{ls:>10}{ls-s:>8}  {min(pr):g}~{max(pr):g}")

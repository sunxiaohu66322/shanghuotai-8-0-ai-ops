import json
for b in ["B11","B12","B13"]:
    try: e=json.load(open(f"errors-{b}.json",encoding="utf-8"))
    except Exception as x: print(b,"ERR",x); continue
    from collections import Counter
    c=Counter((x.get("item"),x.get("stage")) for x in e)
    print(f"--- {b}: {len(e)} 条")
    for k,v in c.most_common(12): print("   ",k,v)

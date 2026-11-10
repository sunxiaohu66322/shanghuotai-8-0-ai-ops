"""
把本轮跑出来的错误与修法批量入库（错误库.py 的数据源）
用法：python 导入本轮.py
"""
import json, os, sqlite3, glob, datetime, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "上货错误库.sqlite3")

FIX_MAP = {
    "品牌": ("键盘法：真实鼠标点 .ecom-g-select 框 → ArrowDown → Enter；DOM 事件无效", "fillBrand"),
    "价格": ("切「价格库存」tab 后，按 tr 逐行填：每行 input[0]=价格 input[1]=库存", "fillSkuRows"),
    "现货库存": ("切「价格库存」tab 后，按 tr 逐行填 input[1]", "fillSkuRows"),
    "商品毛重": ("重量输入框 placeholder 含 0.001到9999.999，属属性页", "fillWeight"),
    "支撑件木种": ("在「属性信息」tab 用同样键盘法选第一个；且必须点过填写检查后才会暴露", "fillAttrSelect"),
    "功能": ("多选属性，键盘法选第一个；出现在填写检查之后", "fillAttrSelect"),
    "规格值": ("规格下拉：真实鼠标点框→ArrowDown→Enter；规格值每个值一个 input，最后一个空框输入后 Tab", "fillSkuSpec"),
    "主图视频": ("非必填项，只影响转化率 +0.5%，不阻塞发布；要补需上传视频", None),
    "商品详情": ("详情图要在「图文信息」tab 的商详区上传，不在主图区", "uploadDetail"),
}


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(open(os.path.join(HERE, "错误库.py"), encoding="utf-8").read().split('SCHEMA = """')[1].split('"""')[0])

    inserted_err = inserted_les = 0
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for f in sorted(glob.glob(os.path.join(HERE, "errors-*.json"))):
        batch = os.path.basename(f)[len("errors-"):-len(".json")]
        try:
            errs = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        items = {e.get("item") for e in errs}
        for it in items:
            c.execute(
                "INSERT INTO runs(batch,item_key,stage,created_at,updated_at) VALUES(?,?,'imported',?,?) "
                "ON CONFLICT(batch,item_key) DO NOTHING",
                (batch, it, now, now),
            )
        for e in errs:
            c.execute(
                "INSERT INTO errors(batch,item_key,stage,field,msg,category,seen_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(batch,item_key,field,msg) DO UPDATE SET seen_at=excluded.seen_at",
                (batch, e.get("item"), e.get("stage"), e.get("field"), e.get("msg"), e.get("category"), now),
            )
            inserted_err += 1
            field = e.get("field") or ""
            if field in FIX_MAP:
                fix, fn = FIX_MAP[field]
                c.execute(
                    "INSERT INTO lessons(field,symptom,fix,script_fn,times_seen,first_seen,last_seen) VALUES(?,?,?,?,1,?,?) "
                    "ON CONFLICT(field,symptom) DO UPDATE SET times_seen=times_seen+1, last_seen=excluded.last_seen",
                    (field, e.get("msg") or "", fix, fn, now, now),
                )
                inserted_les += 1

    # 已知成功案例（人工确认过的成果）写进 runs
    for batch, item, pid, status in [
        ("B6-成功", "1531", "3851691287216521249", "published"),
        ("B7-成功", "1391", "3851688617936290279", "published"),
    ]:
        c.execute(
            "INSERT INTO runs(batch,item_key,stage,pid,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(batch,item_key) DO UPDATE SET stage=excluded.stage, pid=excluded.pid, updated_at=excluded.updated_at",
            (batch, item, "published", pid, now, now),
        )

    c.commit()
    print(f"导入错误 {inserted_err} 条 / 沉淀修法 {inserted_les} 条")
    for t in ("runs", "errors", "lessons"):
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()

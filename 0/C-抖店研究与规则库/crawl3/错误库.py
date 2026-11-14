"""
抖店上货 · 错误库与知识库
==========================
设计原则（按主人 2026-11-02 09:15 的架构）：
  1. 脚本先跑，不管对错，跑完为止
  2. 每件商品的每个错误，原样入库（字段/原文/阶段）
  3. AI 回头逐件看错误 → 按平台左侧提示补 → 把「怎么修的」也入库
  4. 下次脚本遇到同样字段，先查 lessons 表，直接照方抓药

三张表：
  runs     一次上货尝试（一件商品一行生命周期）
  errors   该次尝试里冒出来的每一个错误（可多次、可重复）
  lessons  修法沉淀：同一个字段 + 同一种症状，怎么修（脚本下次直接读这张表）

命令行：
  python 错误库.py init
  python 错误库.py add-run   --batch B1 --key ITEM --title "..."
  python 错误库.py add-error --batch B1 --key ITEM --field 价格 --msg "..." --stage filled
  python 错误库.py set-stage --batch B1 --key ITEM --stage published --pid 3851...
  python 错误库.py learn     --field 价格 --symptom "该项为必填，请输入" --fix "填 550" --script fill_price
  python 错误库.py report    [--batch B1]
  python 错误库.py lessons
  python 错误库.py todo      [--batch B1]      # 列出还没修完的错误
"""
import sqlite3, json, sys, argparse, datetime, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "上货错误库.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch TEXT NOT NULL,
  item_key TEXT NOT NULL,
  title TEXT,
  stage TEXT DEFAULT 'created',      -- created|filled|cropped|attrs|price|ready|published|failed
  pid TEXT,                           -- 抖店商品 ID
  url TEXT,
  created_at TEXT,
  updated_at TEXT,
  notes TEXT,
  UNIQUE(batch, item_key)
);

CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch TEXT NOT NULL,
  item_key TEXT NOT NULL,
  stage TEXT,
  field TEXT,                         -- 支撑件木种 / 规格值 / 价格 / 现货库存 / 主图视频 / 品牌 ...
  msg TEXT,                           -- 平台原文，一字不改
  category TEXT,                      -- required|format|media|category|brand|other
  seen_at TEXT,
  fix_attempts INTEGER DEFAULT 0,
  fixed INTEGER DEFAULT 0,
  fix_method TEXT,
  UNIQUE(batch, item_key, field, msg)
);

CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  field TEXT NOT NULL,
  symptom TEXT NOT NULL,
  fix TEXT NOT NULL,
  script_fn TEXT,                     -- 哪个函数/步骤负责修
  times_seen INTEGER DEFAULT 1,
  first_seen TEXT,
  last_seen TEXT,
  UNIQUE(field, symptom)
);

CREATE INDEX IF NOT EXISTS ix_err_batch ON errors(batch, item_key);
CREATE INDEX IF NOT EXISTS ix_run_batch ON runs(batch);
"""

CATEGORY = {
    "brand": ["品牌", "品牌资质"],
    "category": ["类目", "开通"],
    "media": ["主图", "视频", "图片", "详情图"],
    "required": ["该项为必填", "请输入", "必填"],
    "format": ["不能重复", "格式", "超出"],
}


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def guess_category(field, msg):
    s = f"{field or ''} {msg or ''}"
    for cat, kws in CATEGORY.items():
        if any(k in s for k in kws):
            return cat
    return "other"


def cmd_init(_):
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    print(f"OK  库已就绪 {DB}")
    for t in ("runs", "errors", "lessons"):
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t}: {n} 行")


def cmd_add_run(a):
    c = conn()
    t = now()
    c.execute(
        "INSERT INTO runs(batch,item_key,title,stage,created_at,updated_at) VALUES(?,?,?,'created',?,?) "
        "ON CONFLICT(batch,item_key) DO UPDATE SET title=COALESCE(excluded.title,title), updated_at=excluded.updated_at",
        (a.batch, a.key, a.title, t, t),
    )
    c.commit()
    print(f"OK  run {a.batch}/{a.key}")


def cmd_add_error(a):
    c = conn()
    cat = a.category or guess_category(a.field, a.msg)
    c.execute(
        "INSERT INTO errors(batch,item_key,stage,field,msg,category,seen_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(batch,item_key,field,msg) DO UPDATE SET seen_at=excluded.seen_at",
        (a.batch, a.key, a.stage, a.field, a.msg, cat, now()),
    )
    c.commit()
    print(f"OK  error {a.field} [{cat}] {a.msg[:40]}")


def cmd_set_stage(a):
    c = conn()
    c.execute(
        "UPDATE runs SET stage=?, pid=COALESCE(?,pid), updated_at=? WHERE batch=? AND item_key=?",
        (a.stage, a.pid, now(), a.batch, a.key),
    )
    c.commit()
    print(f"OK  {a.batch}/{a.key} → {a.stage}" + (f"  pid={a.pid}" if a.pid else ""))


def cmd_learn(a):
    c = conn()
    t = now()
    c.execute(
        "INSERT INTO lessons(field,symptom,fix,script_fn,times_seen,first_seen,last_seen) VALUES(?,?,?,?,1,?,?) "
        "ON CONFLICT(field,symptom) DO UPDATE SET fix=excluded.fix, script_fn=COALESCE(excluded.script_fn,script_fn),"
        "times_seen=times_seen+1, last_seen=excluded.last_seen",
        (a.field, a.symptom, a.fix, a.script_fn, t, t),
    )
    c.commit()
    print(f"OK  lesson [{a.field}] {a.symptom[:30]} → {a.fix[:40]}")


def cmd_mark_fixed(a):
    c = conn()
    c.execute(
        "UPDATE errors SET fixed=1, fix_method=?, fix_attempts=fix_attempts+1 WHERE batch=? AND item_key=? AND field=?",
        (a.method, a.batch, a.key, a.field),
    )
    c.commit()
    print(f"OK  fixed {a.key}/{a.field} via {a.method}")


def cmd_lessons(_):
    c = conn()
    rows = c.execute("SELECT * FROM lessons ORDER BY times_seen DESC, id").fetchall()
    if not rows:
        print("(空) 还没有任何修法沉淀")
        return
    print(f"{'字段':<14}{'症状':<28}{'修法':<40}{'次数'}")
    print("-" * 100)
    for r in rows:
        print(f"{r['field']:<14}{r['symptom'][:26]:<28}{r['fix'][:38]:<40}{r['times_seen']}")


def cmd_report(a):
    c = conn()
    q = "SELECT * FROM runs" + (" WHERE batch=?" if a.batch else "") + " ORDER BY item_key"
    runs = c.execute(q, (a.batch,) if a.batch else ()).fetchall()
    print(f"# 批次 {a.batch or 'ALL'}  共 {len(runs)} 件\n")
    print(f"{'item':<22}{'stage':<12}{'pid':<22}{'错误数':<8}")
    print("-" * 70)
    stat = {}
    for r in runs:
        n = c.execute(
            "SELECT COUNT(*) FROM errors WHERE batch=? AND item_key=?",
            (r["batch"], r["item_key"]),
        ).fetchone()[0]
        stat[r["stage"]] = stat.get(r["stage"], 0) + 1
        print(f"{r['item_key']:<22}{r['stage']:<12}{(r['pid'] or '-'):<22}{n:<8}")
    print("\n阶段分布:", json.dumps(stat, ensure_ascii=False))

    eq = "SELECT field, msg, category, COUNT(*) n FROM errors WHERE fixed=0" + (
        " AND batch=?" if a.batch else ""
    ) + " GROUP BY field, msg, category ORDER BY n DESC"
    errs = c.execute(eq, (a.batch,) if a.batch else ()).fetchall()
    if errs:
        print(f"\n## 未修复错误汇总（{len(errs)} 类）")
        for e in errs:
            print(f"  [{e['n']:>2}x] {e['field']:<16} {e['msg'][:52]}")


def cmd_todo(a):
    c = conn()
    q = (
        "SELECT e.batch, e.item_key, e.field, e.msg, e.category, r.title "
        "FROM errors e LEFT JOIN runs r ON r.batch=e.batch AND r.item_key=e.item_key "
        "WHERE e.fixed=0" + (" AND e.batch=?" if a.batch else "") + " ORDER BY e.batch, e.item_key, e.id"
    )
    rows = c.execute(q, (a.batch,) if a.batch else ()).fetchall()
    if not rows:
        print("(空) 没有待修错误")
        return
    cur = None
    for r in rows:
        if r["item_key"] != cur:
            cur = r["item_key"]
            print(f"\n### {r['batch']} / {cur}  {(r['title'] or '')[:40]}")
        print(f"   - [{r['category']}] {r['field']}: {r['msg'][:60]}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    x = sub.add_parser("add-run")
    x.add_argument("--batch", required=True)
    x.add_argument("--key", required=True)
    x.add_argument("--title", default=None)
    x.set_defaults(fn=cmd_add_run)

    x = sub.add_parser("add-error")
    x.add_argument("--batch", required=True)
    x.add_argument("--key", required=True)
    x.add_argument("--field", required=True)
    x.add_argument("--msg", required=True)
    x.add_argument("--stage", default="filled")
    x.add_argument("--category", default=None)
    x.set_defaults(fn=cmd_add_error)

    x = sub.add_parser("set-stage")
    x.add_argument("--batch", required=True)
    x.add_argument("--key", required=True)
    x.add_argument("--stage", required=True)
    x.add_argument("--pid", default=None)
    x.set_defaults(fn=cmd_set_stage)

    x = sub.add_parser("learn")
    x.add_argument("--field", required=True)
    x.add_argument("--symptom", required=True)
    x.add_argument("--fix", required=True)
    x.add_argument("--script", dest="script_fn", default=None)
    x.set_defaults(fn=cmd_learn)

    x = sub.add_parser("mark-fixed")
    x.add_argument("--batch", required=True)
    x.add_argument("--key", required=True)
    x.add_argument("--field", required=True)
    x.add_argument("--method", required=True)
    x.set_defaults(fn=cmd_mark_fixed)

    sub.add_parser("lessons").set_defaults(fn=cmd_lessons)

    x = sub.add_parser("report")
    x.add_argument("--batch", default=None)
    x.set_defaults(fn=cmd_report)

    x = sub.add_parser("todo")
    x.add_argument("--batch", default=None)
    x.set_defaults(fn=cmd_todo)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

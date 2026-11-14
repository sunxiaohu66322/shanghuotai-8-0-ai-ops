# -*- coding: utf-8 -*-
# xz-stats.py —— 读 xz-all-records.json，算上货成功率 + 按目标/来源分类
import json, re
from collections import defaultdict

with open(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\crawl3\xz-all-records.json", encoding="utf-8") as f:
    rows = json.load(f)

# 主行：以「操作人员：」开头且含「复制到：」的为任务行
task_rows = []
for r in rows:
    if "操作人员" not in r or "复制到" not in r:
        continue
    tid = re.search(r"\b(1[5-9]\d{6}|18\d{6})\b", r)
    target = re.search(r"复制到：(\S+?)(?:\s|$)", r)
    op = re.search(r"操作人员：(.+?)\s+(?:商品复制|复制到)", r)
    succ = re.search(r"成功数\s*$", r)  # 主行的列顺序：总数 成功数 失败数 创建日期
    # 主行末尾：... 执行结束 [总数] [成功数] [失败数] [日期] 详情
    m = re.search(r"执行结束\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})", r)
    task_rows.append({
        "tid": tid.group(1) if tid else "?",
        "target": target.group(1) if target else "?",
        "op": op.group(1).strip() if op else "?",
        "total": int(m.group(1)) if m else 0,
        "succ": int(m.group(2)) if m else 0,
        "fail": int(m.group(3)) if m else 0,
        "date": m.group(4) if m else "?",
        "raw": r[:200],
    })

print("TASK_ROWS " + str(len(task_rows)))

# 按目标分类
by_target = defaultdict(lambda: {"tasks": 0, "total": 0, "succ": 0, "fail": 0})
for t in task_rows:
    by_target[t["target"]]["tasks"] += 1
    by_target[t["target"]]["total"] += t["total"]
    by_target[t["target"]]["succ"] += t["succ"]
    by_target[t["target"]]["fail"] += t["fail"]

print("\n=== 按目标 ===")
for tgt, d in by_target.items():
    rate = d["succ"] / d["total"] * 100 if d["total"] else 0
    print(f"{tgt}: 任务{d['tasks']}个 总数{d['total']} 成功{d['succ']} 失败{d['fail']} 成功率{rate:.1f}%")

# 按日期分组
by_date = defaultdict(lambda: {"tasks": 0, "total": 0, "succ": 0, "fail": 0})
for t in task_rows:
    by_date[t["date"]]["tasks"] += 1
    by_date[t["date"]]["total"] += t["total"]
    by_date[t["date"]]["succ"] += t["succ"]
    by_date[t["date"]]["fail"] += t["fail"]

print("\n=== 按日期 ===")
for d in sorted(by_date.keys()):
    v = by_date[d]
    rate = v["succ"] / v["total"] * 100 if v["total"] else 0
    print(f"{d}: 任务{v['tasks']}个 总数{v['total']} 成功{v['succ']} 失败{v['fail']} 成功率{rate:.1f}%")

# 出售中 vs 草稿箱
sale = [t for t in task_rows if t["target"] == "出售中"]
draft = [t for t in task_rows if t["target"] == "草稿箱"]
print(f"\n=== 出售中 vs 草稿箱 ===")
print(f"出售中: {len(sale)}任务 总数{sum(t['total'] for t in sale)} 成功{sum(t['succ'] for t in sale)}")
print(f"草稿箱: {len(draft)}任务 总数{sum(t['total'] for t in draft)} 成功{sum(t['succ'] for t in draft)}")

# 保存
with open(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\crawl3\xz-task-rows.json", "w", encoding="utf-8") as f:
    json.dump(task_rows, f, ensure_ascii=False, indent=2)
print("\nSAVED xz-task-rows.json")

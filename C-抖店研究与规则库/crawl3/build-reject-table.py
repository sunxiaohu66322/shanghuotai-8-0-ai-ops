# -*- coding: utf-8 -*-
# build-reject-table.py —— 生成商品驳回导出表 + 上货来源成功率表（xlsx + csv）
import csv, json, re
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from datetime import datetime

OUT = r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\crawl3"

# === 表1：商品驳回导出 ===
rejects = [
    {
        "序号": 1,
        "商品名称": "新中式北美黑胡桃木茶几长方形沙发茶桌简约现代别墅客厅异型矮桌",
        "商品ID": "3851898188608962780",
        "审核状态": "审核驳回",
        "驳回原因": "1、发布虚假服务信息，做出不实服务承诺，请修改。《【商家—虚假宣传】细则》\n2、商品不规范宣传承诺『终身』『永久』等，请删除此类绝对化用语，确认信息无误后，可重新提交审核。详见公告",
        "违规类型": "虚假服务",
        "违规定位": "标题无违规词；违规承诺文字在商品主图或详情图片上（需打码处理）",
        "送审时间": "2026/11/03 12:55:31",
        "驳回时间": "2026/11/03 12:56:03",
        "处理建议": "去修改 → 删除商品图片上的『终身/永久』承诺文字（打码或换图）→ 重新提交审核",
    },
    {
        "序号": 2,
        "商品名称": "新中式北美黑胡桃木茶几长方形沙发茶桌简约现代别墅客厅异型矮桌",
        "商品ID": "3851896363247862090",
        "审核状态": "审核驳回",
        "驳回原因": "1、商品不规范宣传承诺『终身』『永久』等，请删除此类绝对化用语，确认信息无误后，可重新提交审核。详见公告\n2、发布虚假服务信息，做出不实服务承诺，请修改。《【商家—虚假宣传】细则》",
        "违规类型": "虚假服务",
        "违规定位": "标题无违规词；违规承诺文字在商品主图或详情图片上（需打码处理）",
        "送审时间": "2026/11/03 12:45:23",
        "驳回时间": "2026/11/03 12:46:17",
        "处理建议": "去修改 → 删除商品图片上的『终身/永久』承诺文字（打码或换图）→ 重新提交审核",
    },
    {
        "序号": 3,
        "商品名称": "新中式北美黑胡桃木茶几长方形沙发茶桌简约现代别墅客厅异型矮桌",
        "商品ID": "3851895837223420000",
        "审核状态": "审核驳回",
        "驳回原因": "1、发布虚假服务信息，做出不实服务承诺，请修改。《【商家—虚假宣传】细则》\n2、商品不规范宣传承诺『终身』『永久』等，请删除此类绝对化用语，确认信息无误后，可重新提交审核。详见公告",
        "违规类型": "虚假服务",
        "违规定位": "标题无违规词；违规承诺文字在商品主图或详情图片上（需打码处理）",
        "送审时间": "2026/11/03 12:40:57",
        "驳回时间": "2026/11/03 12:41:21",
        "处理建议": "去修改 → 删除商品图片上的『终身/永久』承诺文字（打码或换图）→ 重新提交审核",
    },
    {
        "序号": 4,
        "商品名称": "度庐巴斯沙发小边几楠竹现代简约可移动",
        "商品ID": "3851708589492273278",
        "审核状态": "审核驳回",
        "驳回原因": "您发布的商品因以下一项或多项原因存在违规：\n1、商品信息中展示了与商品无关的品牌，或与其他品牌进行了不当对比宣传；\n2、同一商品同时宣传了多个品牌信息；\n3、未经授权或缺乏相应资质，发布了平台限售品牌的商品（详见限售品牌清单）",
        "违规类型": "无关品牌",
        "违规定位": "标题含『度庐巴斯』品牌词；商品本身非该品牌商品 → 标题或品牌字段填了无关品牌",
        "送审时间": "2026/11/02 12:12:48",
        "驳回时间": "2026/11/02 12:13:14",
        "处理建议": "去修改 → 删除标题里『度庐巴斯』品牌词 + 品牌字段改为『无品牌/其他』→ 重新提交审核",
    },
]

# === 表2：上货来源成功率（从 xz-task-rows.json 读）===
with open(OUT + r"\xz-task-rows.json", encoding="utf-8") as f:
    task_rows = json.load(f)

# 去掉 "?" 日期的（老数据解析失败），只留能确认的
valid = [t for t in task_rows if t["date"] != "?"]

# 按目标分类
from collections import defaultdict
by_target = defaultdict(lambda: {"tasks": 0, "total": 0, "succ": 0, "fail": 0})
for t in valid:
    by_target[t["target"]]["tasks"] += 1
    by_target[t["target"]]["total"] += t["total"]
    by_target[t["target"]]["succ"] += t["succ"]
    by_target[t["target"]]["fail"] += t["fail"]

source_rows = []
for tgt, d in by_target.items():
    rate = d["succ"] / d["total"] * 100 if d["total"] else 0
    source_rows.append({
        "上货目标": tgt,
        "任务数": d["tasks"],
        "商品总数": d["total"],
        "成功数": d["succ"],
        "失败数": d["fail"],
        "成功率": f"{rate:.1f}%",
    })

# 按日期
by_date = defaultdict(lambda: {"tasks": 0, "total": 0, "succ": 0, "fail": 0})
for t in valid:
    by_date[t["date"]]["tasks"] += 1
    by_date[t["date"]]["total"] += t["total"]
    by_date[t["date"]]["succ"] += t["succ"]
    by_date[t["date"]]["fail"] += t["fail"]

date_rows = []
for d in sorted(by_date.keys()):
    v = by_date[d]
    rate = v["succ"] / v["total"] * 100 if v["total"] else 0
    date_rows.append({
        "日期": d,
        "任务数": v["tasks"],
        "商品总数": v["total"],
        "成功数": v["succ"],
        "失败数": v["fail"],
        "成功率": f"{rate:.1f}%",
    })

# === 写 xlsx ===
wb = Workbook()
ws1 = wb.active
ws1.title = "商品驳回导出"

headers1 = list(rejects[0].keys())
ws1.append(headers1)
for c in ws1[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="4472C4")
    c.alignment = Alignment(horizontal="center", vertical="center")

for r in rejects:
    ws1.append([r[h] for h in headers1])

# 列宽
ws1.column_dimensions["A"].width = 6
ws1.column_dimensions["B"].width = 40
ws1.column_dimensions["C"].width = 22
ws1.column_dimensions["D"].width = 12
ws1.column_dimensions["E"].width = 60
ws1.column_dimensions["F"].width = 14
ws1.column_dimensions["G"].width = 40
ws1.column_dimensions["H"].width = 20
ws1.column_dimensions["I"].width = 20
ws1.column_dimensions["J"].width = 50

# 自动换行
for row in ws1.iter_rows(min_row=2):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

# 表2：上货来源成功率
ws2 = wb.create_sheet("上货来源成功率")
headers2 = list(source_rows[0].keys())
ws2.append(headers2)
for c in ws2[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="70AD47")
    c.alignment = Alignment(horizontal="center", vertical="center")

for r in source_rows:
    ws2.append([r[h] for h in headers2])

ws2.column_dimensions["A"].width = 14
for col in "BCDEF":
    ws2.column_dimensions[col].width = 12

# 表3：按日期
ws3 = wb.create_sheet("按日期统计")
headers3 = list(date_rows[0].keys())
ws3.append(headers3)
for c in ws3[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="ED7D31")
    c.alignment = Alignment(horizontal="center", vertical="center")

for r in date_rows:
    ws3.append([r[h] for h in headers3])

ws3.column_dimensions["A"].width = 14
for col in "BCDEF":
    ws3.column_dimensions[col].width = 12

xlsx_path = OUT + r"\抖店_驳回商品与上货统计_20261103.xlsx"
wb.save(xlsx_path)

# === 写 CSV ===
csv1 = OUT + r"\抖店_驳回商品_20261103.csv"
with open(csv1, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=headers1)
    w.writeheader()
    for r in rejects:
        w.writerow(r)

csv2 = OUT + r"\抖店_上货成功率_20261103.csv"
with open(csv2, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=headers2)
    w.writeheader()
    for r in source_rows:
        w.writerow(r)

print("XLSX " + xlsx_path)
print("CSV1 " + csv1)
print("CSV2 " + csv2)
print("REJECTS " + str(len(rejects)))
print("SOURCE_ROWS " + str(len(source_rows)))
print("DATE_ROWS " + str(len(date_rows)))

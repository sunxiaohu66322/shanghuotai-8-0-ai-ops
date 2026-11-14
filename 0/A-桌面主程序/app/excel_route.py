# -*- coding: utf-8 -*-
"""excel_route.py —— 抖音采集计划(plan-*.json) → 小猪/抖店 Excel 标准模板，一键批量导出。

这个模块把 2026-11-02「Excel 路线」手工跑通的全部经验固化下来，上货台里点一次按钮、
或在命令行敲一句，就能把所有多规格商品一次导出成可直接上传小猪的表，不用再一个个导。

固化的规则（每一条都是实测踩出来的，别随手改）：

1. 表头在第 **2** 行，与官方模板 UPLOAD_DEMO1.xlsx 第 2 行**逐字一致**（A..P 共 16 列）；
   第 1 行是 A1:N1 合并的红色说明，**红色示例行不复制**，数据从第 3 行开始。
2. **每个 SKU 行都要写商品ID**（不是只写首行）—— 小猪按商品ID 聚合，同一商品多行同 ID。
3. 商品ID / SKU编码 / SKU拼单价 单元格设为**文本格式 @**，否则 15 位以上会被 Excel 舍入。
4. 图片只能是**公网 URL**。本地路径 → 查 v3_wx_img_cache(path→mmecimage)；缺的现传并回写缓存。
   拿不到 URL 就留空并计入缺图清单，**绝不伪造**。
5. `SKU规格名称` = 规格1 + 半角空格 + 规格2（空槽不参与拼接）。
6. 源 `skus[].spec` 往往是长度 1 的整串，按 `【胡桃色】xxx` 前缀 或 `a / b` 斜杠拆维度。
7. 类目为空不猜：走 category_hint，再走 CAT_FIX 已知补丁；仍空则报告点名。

用法（命令行，一句导出全部）：
    python -m app.excel_route                        # 默认：全部 SKU>=2 的，输出到 data\\excel-out
    python -m app.excel_route --min-sku 7            # 只导多规格（>=7）
    python -m app.excel_route --pid 1526 --out D:\\x # 单个
    python -m app.excel_route --all --fill-images    # 缺图现场补传图床
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time

# ------------------------------------------------------------------ 路径
# 与 app/paths.py 同口径：外部工作区一律用绝对路径。
# 打包成 exe 后「程序目录的上一级」会指到 dist\\，相对推算找不到数据 —— 所以不靠推算。
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(APP_DIR)                       # 上货台\
WS_ROOT = os.path.dirname(PKG_DIR)                       # 工作区根（源码运行时成立）

TAOBAO_DIR = r'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker'
if not os.path.isdir(TAOBAO_DIR):                        # 换机器/换盘时的兜底
    TAOBAO_DIR = os.path.join(WS_ROOT, 'taobao-picker')

CRAWL_DIR = os.path.join(os.path.dirname(TAOBAO_DIR), '抖店研究', 'crawl3')
if not os.path.isdir(CRAWL_DIR):
    CRAWL_DIR = os.path.join(WS_ROOT, '抖店研究', 'crawl3')

V3_DB = os.path.join(TAOBAO_DIR, 'data', 'v3.db')
OUT_DIR = os.path.join(PKG_DIR, 'data', 'excel-out')

# ------------------------------------------------------------------ 常量
COLUMNS = [
    "商品ID", "商品标题", "商品分类",
    "商品轮播图1", "商品轮播图2", "商品轮播图3",
    "SKU规格名称", "规格1", "规格2", "规格3",
    "SKU编码", "SKU库存", "SKU拼单价", "SKU预览图",
    "商品详情图", "商品属性",
]
TEMPLATE_NOTE = (
    "模板说明：\n"
    "1、当前模板为示例参考，导入数据包可兼容其他软件导出字段信息，详见教程帮助文档\n"
    "2、下述红色字体为商品参考示例，导入前请删除\n"
    "3、商品属性字段非强校验，若无也可导入"
)
DETAIL_SEP = ","
ATTR_KV = ":"
ATTR_ITEM = "；"

# 源数据缺类目路径、已实测可用的补丁（小猪按类目建品，空类目必失败）。
# 只在 plan 里真的没有类目时才用，报告里会标明「用了补丁值」。
CAT_FIX = {1526: '家具 > 几类 > 茶几/边几'}

DEFAULT_ID_PREFIX = '9'          # 商品ID = 前缀 + 13 位补零 pid，纯数字 14 位


# ------------------------------------------------------------------ 图片
class ImgMap:
    """本地图片路径 → 公网 URL（v3_wx_img_cache，可现场补传）。"""

    def __init__(self, db=None, allow_upload=False):
        self.db = db or V3_DB
        self.m = {}
        self.miss = []
        self.uploaded = 0
        self.upload_fail = []
        self.allow_upload = allow_upload
        try:
            con = sqlite3.connect(self.db)
            cur = con.cursor()
            cur.execute('select path, url from v3_wx_img_cache')
            self.m = {_nk(p): u for p, u in cur.fetchall() if p and u}
            con.close()
        except Exception as e:                            # noqa: BLE001
            self.load_error = '%s: %s' % (type(e).__name__, e)
        else:
            self.load_error = ''

    # -- 查
    def get(self, local):
        if not local:
            return ''
        return self.m.get(_nk(local), '')

    def __call__(self, local):
        u = self.get(local)
        if not u and local:
            self.miss.append(local)
        return u

    # -- 补
    def ensure(self, paths):
        """把还没 URL 的图补传图床并写回缓存。返回补传成功数。"""
        uniq = []
        seen = set()
        for p in paths:
            if not p:
                continue
            k = _nk(p)
            if k in seen or k in self.m:
                continue
            seen.add(k)
            uniq.append(p)
        if not uniq or not self.allow_upload:
            return 0
        wxshop = _load_wxshop()
        if wxshop is None:
            self.upload_fail = uniq[:]
            return 0
        try:
            got = wxshop.upload_map(uniq, rate=0.2)
        except Exception as e:                            # noqa: BLE001
            self.upload_fail = uniq[:]
            self.load_error = 'upload_map: %s: %s' % (type(e).__name__, e)
            return 0
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        for path, url in got.items():
            if not url:
                self.upload_fail.append(path)
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            try:
                cur.execute(
                    'insert or replace into v3_wx_img_cache (k, path, url, bytes, created_at) '
                    "values (?,?,?,?,datetime('now','localtime'))",
                    (_md5(path), path, url, size))
            except Exception as e:                        # noqa: BLE001
                self.load_error = 'cache write: %s: %s' % (type(e).__name__, e)
                self.upload_fail.append(path)
                continue
            self.m[_nk(path)] = url
            self.uploaded += 1
        con.commit()
        con.close()
        return self.uploaded


def _nk(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _md5(path):
    import hashlib                                     # noqa: PLC0415
    h = hashlib.md5()                                  # noqa: S324
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


def _load_wxshop():
    """按需 import taobao-picker 的 wxshop（只读用它，不改它）。"""
    import sys                                          # noqa: PLC0415
    for cand in (TAOBAO_DIR, os.path.join(WS_ROOT, 'taobao-picker')):
        if os.path.isdir(os.path.join(cand, 'server', 'v3')):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            break
    try:
        from server.v3 import wxshop                   # noqa: PLC0415
        return wxshop
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------------ 规格拆解
BRACKET = re.compile(r'^[【\[]([^】\]]+)[】\]]\s*')
SLASH = re.compile(r'\s*/\s*')


def split_single(s):
    """把长度 1 的 spec 整串拆成最多 2 个维度取值。"""
    t = (s or '').strip()
    if not t:
        return ['']
    m = BRACKET.match(t)
    if m and t[m.end():].strip():
        return [m.group(1).strip(), t[m.end():].strip()]
    if '/' in t:
        parts = [x for x in SLASH.split(t) if x]
        if len(parts) >= 2:
            return [parts[0], ' '.join(parts[1:])]
    return [t]


def dims_of(plan):
    rows = []
    for s in (plan.get('skus') or []):
        sp = s.get('spec') or []
        if len(sp) >= 2:
            vals = [str(x or '').strip() for x in sp[:3]]
        else:
            vals = split_single(str(sp[0]) if sp else (s.get('name') or ''))
        while len(vals) < 1:
            vals.append('')
        rows.append((s, vals))

    if not rows:
        return [], []

    ncol = max(len(v) for _, v in rows)
    dims = []
    for i in range(ncol):
        vals = [(v[i] if i < len(v) else '') for _, v in rows]
        uniq = list(dict.fromkeys(x for x in vals if x))
        dims.append({'idx': i, 'values': uniq, 'constant': len(uniq) <= 1})
    dims = [d for d in dims if d['values']]
    if len(dims) > 1:                                   # 恒定维折叠，但至少留 1 维
        keep = [d for d in dims if not d['constant']] or dims[:1]
        dims = keep
    return dims, rows


# ------------------------------------------------------------------ 单商品
def build(plan, out_path, prod_id=None, id_prefix=DEFAULT_ID_PREFIX,
          keep_note=True, max_carousel=3, drop_attrs=(), set_attrs=None,
          cat_override=None, img=None):
    """plan(dict) → xlsx，返回报告 dict。"""
    from openpyxl import Workbook                       # noqa: PLC0415
    from openpyxl.styles import Alignment, Font         # noqa: PLC0415

    pid = plan.get('pid')
    img = img or ImgMap()
    miss_mark = len(getattr(img, 'miss', []))
    dims, rows = dims_of(plan)
    dim_idx = [d['idx'] for d in dims]

    # ---- 商品级
    main = plan.get('main') or []
    detail = plan.get('detail') or []
    cat = ' > '.join(str(x) for x in (plan.get('cat') or []) if x)
    cat_src = 'plan.cat'
    if not cat:
        cat = str(plan.get('category_hint') or '').strip()
        cat_src = 'category_hint' if cat else ''
    if not cat and pid in CAT_FIX:
        cat = CAT_FIX[pid]
        cat_src = 'CAT_FIX'
    if cat_override:
        cat, cat_src = cat_override, 'override'

    attrs = []
    if plan.get('brand'):
        attrs.append('品牌' + ATTR_KV + str(plan['brand']))
    if plan.get('wood'):
        attrs.append('材质' + ATTR_KV + str(plan['wood']))
    if plan.get('install'):
        attrs.append('安装方式' + ATTR_KV + str(plan['install']))
    if plan.get('ship_days'):
        attrs.append('发货时效' + ATTR_KV + str(plan['ship_days']) + '天')
    attr_str = ATTR_ITEM.join(attrs)
    if drop_attrs:
        attr_str = ATTR_ITEM.join(kv for kv in (attr_str.split(ATTR_ITEM) if attr_str else [])
                                  if kv.split(ATTR_KV)[0] not in drop_attrs)
    for k, v in (set_attrs or {}).items():
        parts = [kv for kv in (attr_str.split(ATTR_ITEM) if attr_str else [])
                 if kv.split(ATTR_KV)[0] != k]
        parts.append(k + ATTR_KV + v)
        attr_str = ATTR_ITEM.join(parts)

    prices = [float(s['price']) for s in (plan.get('skus') or [])
              if s.get('price') not in (None, '')]
    min_price = min(prices) if prices else None

    prod_id = str(prod_id or (id_prefix + ('%013d' % int(pid))))

    carousel = [img(main[n]) if n < len(main) else '' for n in range(max_carousel)]
    detail_urls = [u for u in (img(u) for u in detail if u) if u]

    # ---- SKU 行
    data = []
    price_fallback = 0
    for i, (s, vals) in enumerate(rows):
        take = [vals[j] if j < len(vals) else '' for j in dim_idx]
        spec_name = ' '.join(x for x in take if x).strip() or str(s.get('name') or '')[:40]
        price = s.get('price')
        if price in (None, ''):
            price = min_price
            price_fallback += 1
        rec = dict.fromkeys(COLUMNS, '')
        rec['商品ID'] = prod_id
        if i == 0:
            rec['商品标题'] = plan.get('title') or ''
            rec['商品分类'] = cat
            for n in range(max_carousel):
                rec['商品轮播图%d' % (n + 1)] = carousel[n] if n < len(carousel) else ''
            rec['商品详情图'] = DETAIL_SEP.join(detail_urls)
            rec['商品属性'] = attr_str
        rec['SKU规格名称'] = spec_name
        for n in range(3):
            rec['规格%d' % (n + 1)] = take[n] if n < len(take) else ''
        rec['SKU编码'] = str(s.get('sku_id') or s.get('code') or '')
        rec['SKU库存'] = int(s.get('stock') or 0)
        rec['SKU拼单价'] = '' if price is None else ('%g' % float(price))
        rec['SKU预览图'] = img(s.get('img'))
        data.append(rec)

    # ---- 写盘
    wb = Workbook()
    ws = wb.active
    ws.title = '示例2模板'
    if keep_note:
        ws['A1'] = TEMPLATE_NOTE
        ws['A1'].font = Font(color='FFFF0000', size=11)
        ws['A1'].alignment = Alignment(wrap_text=True, vertical='top')
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=14)
        hdr_row, start = 2, 3
    else:
        hdr_row, start = 1, 2

    for idx, name in enumerate(COLUMNS, start=1):
        ws.cell(row=hdr_row, column=idx, value=name).font = Font(bold=True)

    r = start
    for rec in data:
        for idx, name in enumerate(COLUMNS, start=1):
            v = rec.get(name, '')
            cell = ws.cell(row=r, column=idx)
            cell.value = v
            if name in ('商品ID', 'SKU编码', 'SKU拼单价'):
                cell.number_format = '@'
        r += 1

    widths = {'A': 18, 'B': 62.9, 'C': 23.5, 'D': 92.6, 'E': 25.4, 'F': 25.4, 'G': 32,
              'H': 26, 'I': 26, 'J': 12, 'K': 14, 'L': 10, 'M': 11.9, 'N': 92.6,
              'O': 77.5, 'P': 40}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A%d' % start

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    wb.save(out_path)

    sku_imgs = [rec['SKU预览图'] for rec in data]
    return {
        'pid': pid,
        'title': plan.get('title'),
        'out': os.path.abspath(out_path),
        'rows': len(data),
        'skus_total': len(plan.get('skus') or []),
        'product_id': prod_id,
        'header_row': hdr_row,
        'first_data_row': start,
        'last_row': r - 1,
        'dims': [{'idx': d['idx'], 'n': len(d['values']), 'values': d['values'][:6]}
                 for d in dims],
        'cat': cat,
        'cat_src': cat_src,
        'attr': attr_str,
        'img_carousel': len([u for u in carousel if u]),
        'img_detail': len(detail_urls),
        'img_sku_filled': len([u for u in sku_imgs if u]),
        'img_sku_total': len(sku_imgs),
        'img_miss': len(set(getattr(img, 'miss', [])[miss_mark:])),
        'price_fallback_rows': price_fallback,
        'sample_row': data[0] if data else None,
        'sample_last': data[-1] if data else None,
    }


# ------------------------------------------------------------------ 自检
def selfcheck(path):
    """对着官方模板核对表头；返回 {ok, header_match, diffs, rows}。"""
    from openpyxl import load_workbook                    # noqa: PLC0415
    wb = load_workbook(path)
    ws = wb.active
    hdr = [ws.cell(row=2, column=i).value for i in range(1, 17)]
    diffs = [(i + 1, a, b) for i, (a, b) in enumerate(zip(COLUMNS, hdr)) if (a or '') != (b or '')]
    ids = [ws.cell(row=r, column=1).value for r in range(3, ws.max_row + 1)]
    filled = [x for x in ids if x not in (None, '')]
    fmt_ok = all((ws.cell(row=r, column=1).number_format or '') == '@'
                 for r in range(3, min(ws.max_row, 4)))
    return {
        'ok': not diffs and len(filled) == len(ids) and len(ids) > 0,
        'header_match': not diffs,
        'diffs': diffs[:5],
        'rows': len(ids),
        'id_filled': len(filled),
        'id_text_format': fmt_ok,
    }


# ------------------------------------------------------------------ 批量（一键）
def find_plans(src=None):
    out = []
    for f in sorted(glob.glob(os.path.join(src or CRAWL_DIR, 'plan-*.json'))):
        try:
            plan = json.load(open(f, encoding='utf-8'))
        except Exception:                                 # noqa: BLE001
            continue
        out.append((int(plan.get('pid') or -1), f, plan))
    return out


def export_all(src=None, out=None, min_sku=2, max_sku=0, pids=None,
               fill_images=False, progress=None, log=None):
    """一键导出：遍历 plan，生成 xlsx + 报告。返回汇总 dict。

    progress(done, total, name) 每次开始处理一个商品回调；log(str) 打日志。
    """
    src = src or CRAWL_DIR
    out = out or OUT_DIR
    os.makedirs(out, exist_ok=True)

    plans = find_plans(src)
    todo = []
    for pid, f, plan in plans:
        n = len(plan.get('skus') or [])
        if pids and pid not in pids:
            continue
        if n < max(min_sku, 1):
            continue
        if max_sku and n > max_sku:
            continue
        todo.append((pid, f, plan, n))

    img = ImgMap(allow_upload=fill_images)
    t0 = time.time()
    rows = []
    for i, (pid, f, plan, n) in enumerate(todo):
        if progress:
            try:
                progress(i, len(todo), 'dy-%d' % pid)
            except Exception:                             # noqa: BLE001
                pass
        dst = os.path.join(out, 'dy-%d.xlsx' % pid)
        try:
            rep = build(plan, dst, img=img)
            chk = selfcheck(dst)
            rep['check'] = chk
            rep['ok'] = bool(chk['ok'])
            if not rep.get('cat'):
                rep['warn'] = '类目为空（小猪按类目建品，空类目会失败）'
        except Exception as e:                            # noqa: BLE001
            rep = {'pid': pid, 'ok': False, 'error': '%s: %s' % (type(e).__name__, e),
                   'out': os.path.abspath(dst)}
        rows.append(rep)
        if log:
            try:
                log('  %-6s %3d SKU → %3s 行  %s%s' % (
                    pid, n, rep.get('rows', '?'),
                    '✓' if rep.get('ok') else '✗',
                    ('  ' + rep['warn']) if rep.get('warn') else
                    ('  ' + rep['error'] if rep.get('error') else '')))
            except Exception:                             # noqa: BLE001
                pass

    okn = sum(1 for r in rows if r.get('ok'))
    summary = {
        'ok': okn == len(rows) and bool(rows),
        'src': os.path.abspath(src),
        'out': os.path.abspath(out),
        'count': len(rows),
        'ok_count': okn,
        'fail_count': len(rows) - okn,
        'rows_total': sum(int(r.get('rows') or 0) for r in rows),
        'img_uploaded': getattr(img, 'uploaded', 0),
        'img_miss_unique': len(set(img.miss)),
        'img_miss_sample': list(dict.fromkeys(img.miss))[:8],
        'secs': round(time.time() - t0, 2),
        'items': rows,
    }
    _write_reports(out, summary)
    return summary


def _write_reports(out, summary):
    try:
        json.dump(summary, open(os.path.join(out, '_报告.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    except Exception:                                     # noqa: BLE001
        pass
    lines = ['# 抖音 Excel 导出报告', '',
             '- 源目录：`%s`' % summary['src'],
             '- 输出：`%s`' % summary['out'],
             '- 生成时间：%s' % time.strftime('%Y-%m-%d %H:%M:%S'),
             '- 商品 %d 个（成功 %d / 失败 %d）· 合计 %d 行 · 耗时 %ss'
             % (summary['count'], summary['ok_count'], summary['fail_count'],
                summary['rows_total'], summary['secs']),
             '',
             '| pid | SKU | 行数 | 规格维 | SKU图 | 缺图 | 类目来源 | 自检 | 文件 |',
             '|--:|--:|--:|:--|--:|--:|:--|:--|:--|']
    for r in summary['items']:
        if r.get('error'):
            lines.append('| %s | - | - | - | - | - | - | ✗ %s | - |'
                         % (r['pid'], str(r['error'])[:60]))
            continue
        lines.append('| %s | %s | %s | %s | %s/%s | %s | %s | %s | %s |' % (
            r['pid'], r.get('skus_total'), r.get('rows'),
            ','.join('%d值' % d['n'] for d in r.get('dims') or []),
            r.get('img_sku_filled'), r.get('img_sku_total'),
            r.get('img_miss', 0),
            r.get('cat_src') or '(空)',
            '✓' if r.get('ok') else '✗',
            os.path.basename(r.get('out') or '')))
    if summary.get('img_miss_sample'):
        lines += ['', '## 缺图（需补传图床）', ''] + ['- `%s`' % p
                                                   for p in summary['img_miss_sample']]
    lines += ['', '## 16 列里没有的字段（Excel 管不到）', '',
              '- 重量、发货时间、运费模板、主图视频 —— 在小猪页面上用 `#freightTemplateSelect` '
              '等控件设置，或上架后批量改',
              '- 商品轮播图只有 3 个位置槽，多的丢弃',
              '']
    try:
        open(os.path.join(out, '_报告.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    except Exception:                                     # noqa: BLE001
        pass


# ------------------------------------------------------------------ CLI
def main(argv=None):
    import argparse                                    # noqa: PLC0415
    ap = argparse.ArgumentParser(description='plan-*.json → 小猪 Excel（一键）')
    ap.add_argument('--src', default=None, help='plan 目录，默认 抖店研究\\crawl3')
    ap.add_argument('--out', default=None, help='输出目录，默认 上货台\\data\\excel-out')
    ap.add_argument('--min-sku', type=int, default=2)
    ap.add_argument('--max-sku', type=int, default=0)
    ap.add_argument('--pid', type=int, action='append', default=None)
    ap.add_argument('--all', action='store_true', help='含单规格商品')
    ap.add_argument('--fill-images', action='store_true', help='缺图现场补传图床')
    a = ap.parse_args(argv)
    min_sku = 1 if a.all else a.min_sku
    s = export_all(src=a.src, out=a.out, min_sku=min_sku, max_sku=a.max_sku,
                   pids=set(a.pid) if a.pid else None, fill_images=a.fill_images,
                   log=print)
    print('\n生成 %d 个（成功 %d / 失败 %d）· 合计 %d 行 · %ss'
          % (s['count'], s['ok_count'], s['fail_count'], s['rows_total'], s['secs']))
    print('输出：', s['out'])
    if s['img_miss_unique']:
        print('缺图 %d 张（加 --fill-images 补传）：' % s['img_miss_unique'])
        for p in s['img_miss_sample']:
            print('   ', p)
    return 0 if s['fail_count'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

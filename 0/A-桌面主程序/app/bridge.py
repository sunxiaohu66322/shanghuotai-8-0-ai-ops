# -*- coding: utf-8 -*-
"""后端桥接：把三套既有程序的能力收进一个 API，界面只跟这里打交道。

原则：
    · 不复制它们的代码，直接调用 —— 既有程序改一行，这里立刻跟着变
    · 所有子进程都带 CREATE_NO_WINDOW —— 无控制台的父进程下也不弹黑框
    · 不调用任何 .cmd / .bat —— 启动本地版.cmd 的三步在这里用 Python 重写
    · 不自动打开浏览器、不自动跳页；唯一的 openUrl 是主人**自己点**
      「打开商品页」那一下（人工二次核实用），不在任何后台流程里
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from . import paths
from . import packlabel  # 8.0 统一数据包命名（只写 display_name，不动 pack_code）

_CNW = paths.CREATE_NO_WINDOW


# ============================================================
#  进程
# ============================================================
def _popen(cmd, cwd=None):
    """无窗口启动。cmd 可以是 list 也可以是 str。"""
    kw = dict(
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=_CNW,
    )
    return subprocess.Popen(cmd, **kw)


def _run(cmd, cwd=None, timeout=120):
    """无窗口同步执行，返回 (code, 输出文本)。"""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, timeout=timeout,
            creationflags=_CNW,
        )
        out = (p.stdout or b"") + (p.stderr or b"")
        try:
            text = out.decode("utf-8", "replace")
        except Exception:
            text = str(out)
        return p.returncode, text
    except subprocess.TimeoutExpired:
        return -9, f"超时（>{timeout}s）"
    except FileNotFoundError as e:
        return -2, f"找不到程序：{e}"
    except Exception as e:  # noqa: BLE001
        return -1, f"执行失败：{e}"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


# ============================================================
#  一、采集 + 初筛（淘宝）
# ============================================================
def run_intake(kw: str = "", scroll: int = 0, pages: int = 1,
               auto_learn: bool = True, use_ai: bool = False,
               out_dir: str | None = None, write_csv: bool = True,
               nav: bool = True, page_gap: float = 4.5):
    """跑一条完整的「抓卡 → 初筛 → 品牌自学习 → 出清单」。

    返回 intake.run() 的原样结果。失败时 {"ok": False, "error": ...}
    ★ 这是阻塞调用（要连 CDP、要读页面），必须放在后台线程里跑。
    ★ write_csv=False 时只算不落盘，等界面点「导出清单」再写（主人 2026-10-22 要求）。
    """
    if not paths.ensure_backend_path():
        return {"ok": False, "error": f"找不到采集后端目录：{paths.TAOBAO_SERVER}"}
    try:
        import intake  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"加载采集后端失败：{e}"}
    try:
        return intake.run(
            kw=kw, scroll=int(scroll), pages=int(pages),
            auto_learn=bool(auto_learn), use_ai=bool(use_ai),
            out_dir=out_dir or str(paths.TAOBAO_RESULT),
            write_csv=bool(write_csv), nav=bool(nav),
            page_gap=float(page_gap or 4.5),
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"采集出错：{type(e).__name__}: {e}"}


def run_intake_many(kws_text: str, scroll: int = 0, pages: int = 1,
                    auto_learn: bool = True, use_ai: bool = False,
                    out_dir: str | None = None, page_gap: float = 4.5,
                    on_progress=None):
    """多关键词一次采（逗号分隔），每个关键词各存一个「页签」缓存。

    返回 intake.run_many() 的原样结果（含 items[]，每项带 session 元信息）。
    """
    if not paths.ensure_backend_path():
        return {"ok": False, "error": f"找不到采集后端目录：{paths.TAOBAO_SERVER}"}
    try:
        import intake  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"加载采集后端失败：{e}"}
    try:
        return intake.run_many(
            kws_text=kws_text, scroll=int(scroll), pages=int(pages),
            auto_learn=bool(auto_learn), use_ai=bool(use_ai),
            write_csv=False,           # 界面不自动落盘
            page_gap=float(page_gap or 4.5),
            on_progress=on_progress,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"采集出错：{type(e).__name__}: {e}"}


def stop_intake(who: str = "上货台停止按钮") -> dict:
    """叫停正在跑的采集。

    ★ 这里不绕 HTTP：界面本来就直接 import 后端模块（见 run_intake_many），
      所以直接调 collector.request_stop() —— 同一进程，立刻生效、零延迟。
    """
    if not paths.ensure_backend_path():
        return {"ok": False, "error": f"找不到采集后端目录：{paths.TAOBAO_SERVER}"}
    try:
        import collector  # noqa: PLC0415
        import logbook as _lb  # noqa: PLC0415
        collector.request_stop(who)
        try:
            _lb.log("stop.ui", "上货台点了停止按钮", level="warn", who=who)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "stopped": True, "who": who,
                "info": collector.stop_info()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def intake_status() -> dict:
    """采集在不在跑、有没有被叫停。"""
    if not paths.ensure_backend_path():
        return {"ok": False, "error": "找不到采集后端目录"}
    try:
        import collector  # noqa: PLC0415
        return {"ok": True,
                "running": bool(collector.collect_running()),
                "stop": collector.stop_info(),
                "running_info": collector.running_info()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def sessions_list() -> list[dict]:
    """已缓存的页签（新→旧）。"""
    if not paths.ensure_backend_path():
        return []
    try:
        import sessions  # noqa: PLC0415
        return sessions.lst()
    except Exception:  # noqa: BLE001
        return []


def session_get(sid: str) -> dict | None:
    if not paths.ensure_backend_path():
        return None
    try:
        import sessions  # noqa: PLC0415
        return sessions.get(sid)
    except Exception:  # noqa: BLE001
        return None


def session_delete(sid: str) -> bool:
    if not paths.ensure_backend_path():
        return False
    try:
        import sessions  # noqa: PLC0415
        return sessions.delete(sid)
    except Exception:  # noqa: BLE001
        return False


def session_export(sid: str) -> dict:
    """把某个页签导出成两份 CSV。"""
    if not paths.ensure_backend_path():
        return {"ok": False, "error": "找不到采集后端目录"}
    try:
        import intake  # noqa: PLC0415
        import sessions  # noqa: PLC0415
        d = sessions.get(sid)
        if not d:
            return {"ok": False, "error": "没有这个页签"}
        return intake.export({"summary": d.get("summary") or {},
                              "keep": d.get("keep") or [],
                              "drop": d.get("drop") or []})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def export_intake(result: dict) -> dict:
    """把界面上那份结果写成两份 CSV（点「导出清单」时调）。"""
    if not paths.ensure_backend_path():
        raise RuntimeError(f"找不到采集后端目录：{paths.TAOBAO_SERVER}")
    import intake  # noqa: PLC0415
    return intake.export(result, out_dir=str(paths.TAOBAO_RESULT))


def find_cdp_port(preferred: int = 9222, timeout: float = 0.4) -> int | None:
    """找 Tabbit / Chromium 的调试端口。找不到就返回 None（不猜）。

    ★ 2026-10-25：加 timeout 参数。这个函数在**界面线程每 5 秒**被调一次，
      6 个端口 × 0.4s 超时 = 最坏 2.4 秒硬卡界面（实测离线时单次 407ms）。
      探活一律放到后台线程，并允许把超时压到 0.15s。
    """
    for p in [preferred, 9223, 9224, 9688, 9615, 2222]:
        if port_open(p, timeout=timeout):
            return p
    return None


def brand_stats() -> dict:
    """品牌库条数（taobao-picker/config/brand_library.json）。"""
    try:
        if paths.ensure_backend_path():
            import brandlib  # noqa: PLC0415
            s = brandlib.stats()
            if isinstance(s, dict):
                return s
            return {"raw": s}
    except Exception:  # noqa: BLE001
        pass
    return {}


# ============================================================
#  二、小猪本地化：数据包 / 商品库
# ============================================================
def pig_conn():
    if not paths.PIG_DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{paths.PIG_DB}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        return con
    except Exception:  # noqa: BLE001
        return None


def _tables(con) -> list[str]:
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    except Exception:  # noqa: BLE001
        return []


def pig_overview() -> dict:
    """商品 / 图片 / 数据包 / SKU 的条数，以及图片下载进度。"""
    out = {"ok": False, "db": str(paths.PIG_DB)}
    con = pig_conn()
    if con is None:
        out["error"] = "打不开商品库（文件不存在或被占用）"
        return out
    try:
        tabs = _tables(con)
        out["tables"] = tabs

        def count(t, where=""):
            if t not in tabs:
                return None
            try:
                return con.execute(f'SELECT COUNT(*) FROM "{t}" {"WHERE " + where if where else ""}').fetchone()[0]
            except Exception:  # noqa: BLE001
                return None

        out["goods"] = count("商品")
        out["images"] = count("图片")
        out["skus"] = count("SKU")
        out["attrs"] = count("属性")
        out["packs"] = count("数据包")

        # 图片下载状态（列名不确定 → 先看有什么列）
        img_cols = []
        if "图片" in tabs:
            try:
                img_cols = [r[1] for r in con.execute('PRAGMA table_info("图片")')]
            except Exception:  # noqa: BLE001
                pass
        out["image_columns"] = img_cols
        for col in ("下载状态", "本地路径", "新URL", "类型"):
            if col in img_cols:
                try:
                    rows = con.execute(
                        f'SELECT "{col}", COUNT(*) FROM "图片" GROUP BY 1 ORDER BY 2 DESC LIMIT 8'
                    ).fetchall()
                    out[f"by_{col}"] = {str(r[0]): r[1] for r in rows}
                except Exception:  # noqa: BLE001
                    pass
        out["ok"] = True
        return out
    finally:
        con.close()


def list_packs() -> list[dict]:
    """数据包：库里登记的 + packs 目录里待导入的文件。"""
    rows: list[dict] = []
    con = pig_conn()
    if con is not None:
        try:
            tabs = _tables(con)
            if "数据包" in tabs:
                cols = [r[1] for r in con.execute('PRAGMA table_info("数据包")')]
                sel = ", ".join(f'"{c}"' for c in cols)
                for r in con.execute(f'SELECT {sel} FROM "数据包"'):
                    d = {k: r[k] for k in r.keys()}
                    d["来源"] = "已导入"
                    rows.append(d)
        except Exception:  # noqa: BLE001
            pass
        finally:
            con.close()

    if paths.PIG_PACKS.exists():
        for f in sorted(paths.PIG_PACKS.iterdir()):
            if f.is_file() and f.suffix.lower() in (".csv", ".txt", ".json", ".xlsx"):
                rows.append({"包码": f.stem, "文件": f.name, "来源": "待导入",
                             "大小KB": round(f.stat().st_size / 1024, 1)})
    return rows


def export_pack(pack: str = "") -> tuple[int, str, str]:
    """出 19 列微信小店表。返回 (code, 输出, 输出目录)。"""
    if not paths.EXPORT_SCRIPT.exists():
        return -2, f"找不到出表脚本：{paths.EXPORT_SCRIPT}", ""
    out_dir = paths.PIG_LOCAL / "out"
    cmd = [str(paths.PY), str(paths.EXPORT_SCRIPT), "--dir", str(out_dir)]
    if pack:
        cmd += ["--pack", pack]
    code, text = _run(cmd, cwd=paths.PIG_LOCAL, timeout=300)
    return code, text, str(out_dir)


# ============================================================
#  三、小猪本地链路：本地服务 + 客户端
#     （不调用 启动本地版.cmd —— 那会弹黑框；这里是等价的三步重写）
# ============================================================
def pig_status() -> dict:
    return {
        "server_9109": port_open(9109),
        "client_running": _proc_running("PicDetector.exe"),
        "db_ok": paths.PIG_DB.exists(),
        "packs_dir": paths.PIG_PACKS.exists(),
    }


def _proc_running(name: str) -> bool:
    """判断某个 exe 是否在跑。优先 psutil，没装就用 tasklist ——

    ★ 这里踩过坑：这台机器的 pyenv 里**没有 psutil**，原来的实现 except 后一律返回 False，
      于是每次点「启动本地链路」都会再开一个 PicDetector（实测开了两个，抢同一个商品.db）。
    """
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == name.lower():
                return True
        return False
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess  # noqa: PLC0415
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=paths.CREATE_NO_WINDOW)
        return name.lower() in (out.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return False


def start_pig_stack(log=None, only_csv: str = "") -> dict:
    """起 9109 本地服务 → 导入 packs → 起 PicDetector。全程无黑框。

    log: 可调用对象，收一行文本（给界面日志用）。
    only_csv: 给了就只导入这一个 csv（按显式路径），不扫整个 packs 目录。
    """
    def say(s):
        if log:
            try:
                log(s)
            except Exception:  # noqa: BLE001
                pass

    res = {"ok": False, "steps": []}

    svc = paths.PIG_LOCAL / "本地服务.py"
    if not svc.exists():
        res["error"] = f"找不到本地服务脚本：{svc}"
        return res

    if port_open(9109):
        say("9109 已在监听，跳过启动服务")
        res["steps"].append("服务已在跑")
    else:
        say("启动本地服务 127.0.0.1:9109 …")
        cmd = [str(paths.PY), str(svc), "--port", "9109"]
        _popen(cmd, cwd=paths.PIG_LOCAL)
        for _ in range(40):                     # 最多等 20 秒
            time.sleep(0.5)
            if port_open(9109):
                break
        ok = port_open(9109)
        res["steps"].append(f"服务{'已就绪' if ok else '未就绪'}")
        say(f"服务{'已就绪' if ok else '未就绪（20 秒内没等到 9109）'}")
        if not ok:
            res["error"] = "本地服务没起来，后面的步骤跳过"
            return res

    # 导入 packs —— 只导「选中的那一个」。
    # ★ 千万别在没有 only_csv 时扫整个 packs 目录：根目录还躺着 商品明细.csv / 微信小店样板.csv，
    #   一导就是几百张图现下载，界面看起来就是"点了没反应"（实测卡 >2 分钟）。
    imp = paths.PIG_READER / "导入CSV.py"
    if only_csv:
        csvs = [only_csv] if Path(only_csv).exists() else []
        if not csvs:
            res["error"] = f"要导入的包不存在：{only_csv}"
            return res
    else:
        csvs = []
        say("未指定数据包，跳过导入（第 2 步已经导过；这里只起服务与客户端）")
    if imp.exists() and csvs:
        say(f"导入数据包 {len(csvs)} 个 …")
        for one in csvs:
            code, text = _run([str(paths.PY), str(imp), one], cwd=paths.PIG_READER, timeout=600)
            say(f"  {Path(one).name} → {code}")
        res["steps"].append(f"导入 {len(csvs)} 个包")
    else:
        say("没有选中数据包，跳过导入（不影响后面的件数回读）")

    # 起客户端
    exe = paths.PIG_CLIENT / "PicDetector.exe"
    if not exe.exists():
        res["error"] = f"找不到客户端：{exe}"
        return res
    if _proc_running("PicDetector.exe"):
        say("客户端已在运行")
    else:
        say("启动 PicDetector.exe …")
        _popen([str(exe)], cwd=paths.PIG_CLIENT)
        res["steps"].append("客户端已启动")

    # ★ 真实回读：问一次「待鉴定件数」。这一步才证明链路通了，前面的端口/进程都只是前提。
    pend = pig_pending_count()
    res["pending"] = pend.get("total") if pend.get("ok") else None
    if pend.get("ok"):
        say("✓ 链路已通：9109 返回待鉴定 %d 件（店：%s）" % (pend["total"], pend.get("shop") or "—"))
        res["steps"].append("待鉴定 %d 件" % pend["total"])
    else:
        say("⚠ 9109 在听但取不到件数：%s" % pend.get("error"))
        res["steps"].append("件数读取失败")

    res["ok"] = True
    return res


def stop_pig_stack() -> dict:
    """只停本程序自己起的那些（不碰别的进程）。"""
    out = {"stopped": []}
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        out["error"] = "没有 psutil，无法安全停止"
        return out
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            nm = (p.info.get("name") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or [])
            if nm == "picdetector.exe":
                p.terminate()
                out["stopped"].append("PicDetector.exe")
            elif nm.startswith("python") and "本地服务.py" in cmd and "9109" in cmd:
                p.terminate()
                out["stopped"].append("本地服务.py(9109)")
        except Exception:  # noqa: BLE001
            continue
    return out


# ============================================================
#  四、设置
# ============================================================
DEFAULTS = {
    "cdp_port": 9222,
    "scroll": 6,
    "pages": 1,
    "auto_learn": True,
    "use_ai": False,
    "last_keyword": "",
}


def load_settings() -> dict:
    d = dict(DEFAULTS)
    try:
        if paths.SETTINGS_FILE.exists():
            d.update(json.loads(paths.SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    return d


def save_settings(d: dict) -> None:
    try:
        paths.SETTINGS_FILE.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def env_report() -> list[tuple[str, str, bool]]:
    """设置页用的环境自检：路径存在与否，一眼看清。"""
    items = [
        ("淘宝采集后端", paths.TAOBAO_SERVER, paths.TAOBAO_SERVER.exists()),
        ("包仓库 taobao-picker\\packs", paths.TAOBAO_PACKS, paths.TAOBAO_PACKS.exists()),
        ("① URL初筛 出表位置", paths.PACK_STAGE_URL, paths.PACK_STAGE_URL.exists()),
        ("② 商品细包 放这里", paths.PACK_STAGE_PACK, paths.PACK_STAGE_PACK.exists()),
        ("采集结果目录（老）", paths.TAOBAO_RESULT, paths.TAOBAO_RESULT.exists()),
        ("小猪本地化", paths.PIG_LOCAL, paths.PIG_LOCAL.exists()),
        ("数据包目录 packs", paths.PIG_PACKS, paths.PIG_PACKS.exists()),
        ("小猪客户端 xiaozhu4", paths.PIG_CLIENT, paths.PIG_CLIENT.exists()),
        ("商品库 商品.db", paths.PIG_DB, paths.PIG_DB.exists()),
        ("出表脚本", paths.EXPORT_SCRIPT, paths.EXPORT_SCRIPT.exists()),
        ("词库", paths.PIG_WORDS, paths.PIG_WORDS.exists()),
    ]
    return [(name, str(p), ok) for name, p, ok in items]

# ============================================================
#  六、v3 上货流水线（后端 = taobao-picker\server\v3）
#  说明：直接 import 后端模块，不需要起 9277 服务；
#        与 HTTP 接口是同一套函数，行为完全一致。
# ============================================================
def _v3():
    """拿到后端 v3 模块组。失败抛异常，由调用方包成 {ok:false}。"""
    if not paths.ensure_backend_path():
        raise RuntimeError(f"找不到后端目录：{paths.TAOBAO_SERVER}")
    from v3 import flow, images, importer, keywords, packaging, pack  # noqa: PLC0415
    from v3 import profile as v3profile  # noqa: PLC0415
    from v3 import store, titles  # noqa: PLC0415
    mods = {"flow": flow, "images": images, "importer": importer,
            "keywords": keywords, "packaging": packaging, "pack": pack,
            "profile": v3profile, "store": store, "titles": titles,
            "upload": None}
    # 2026-10-24 新增三块：统一包口径 / 数据等级 / 回收站。**各自 try**，
    # 缺一个不能让上面九个一起挂 —— 界面最多少一个按钮。
    for _name in ("packsrc", "grade", "trash", "safedelete", "words",
                  "cards", "media", "dlcenter", "fieldaudit", "purge",
                  "mask", "enrich", "edits", "repair", "images",
                  "keywords", "adapters", "batches", "publish_guard",
                  # 2026-10-28：选品条件真源 + AI 判官（选品条件页要用）
                  "criteria", "ai_fine"):
        try:
            mods[_name] = __import__("v3." + _name, fromlist=["*"])  # noqa: PLC0415
        except Exception:                                            # noqa: BLE001
            mods[_name] = None
    # 图床上传模块是后来加的：**单独 try**，它缺了不能让上面九个也一起挂掉
    try:
        from v3 import upload as v3upload  # noqa: PLC0415
        mods["upload"] = v3upload
    except Exception:  # noqa: BLE001
        mods["upload"] = None
    # 单件包合并（2026-10-24 新增）：同样单独 try，缺了不影响上面那些
    try:
        from v3 import pack_merge as v3merge  # noqa: PLC0415
        mods["pack_merge"] = v3merge
    except Exception:  # noqa: BLE001
        mods["pack_merge"] = None
    # 抖店 16 列导出（2026-10-25 第四批新增）
    try:
        from v3 import doudian as v3dd  # noqa: PLC0415
        mods["doudian"] = v3dd
    except Exception:  # noqa: BLE001
        mods["doudian"] = None
    # 本地最终媒体包（2026-10-25 第五批新增）：v3 别名包 / server.v3 两条路都试
    v3b = None
    for _mod in ("v3.bundle", "server.v3.bundle"):
        try:
            v3b = __import__(_mod, fromlist=["*"])
            break
        except Exception:      # noqa: BLE001
            v3b = None
    mods["bundle"] = v3b
    # 类目映射（D-35，2026-10-25 第五批新增）
    v3cm = None
    for _mod in ("v3.catmap", "server.v3.catmap"):
        try:
            v3cm = __import__(_mod, fromlist=["*"])
            break
        except Exception:      # noqa: BLE001
            v3cm = None
    mods["catmap"] = v3cm
    # 微信小店直连上货（2026-10-26 第六批新增）：不出表，直接建商品。
    # 两条 import 路都试 —— 打包成 exe 后包名可能是 server.v3。
    v3wx = None
    for _mod in ("v3.wxshop", "server.v3.wxshop"):
        try:
            v3wx = __import__(_mod, fromlist=["*"])
            break
        except Exception:      # noqa: BLE001
            v3wx = None
    mods["wxshop"] = v3wx
    return mods


def _v3conn(m):
    c = m["flow"].conn()
    return c


def _wrap(fn, *a, **kw):
    """统一把异常变成 {ok:False,error}，界面永远拿到 dict。"""
    try:
        m = _v3()
        c = _v3conn(m)
        try:
            return fn(m, c, *a, **kw)
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def v3_overview() -> dict:
    return _wrap(lambda m, c: m["flow"].overview(c))


def v3_sources() -> dict:
    return _wrap(lambda m, c: {"ok": True, **m["importer"].scan_sources()})


def v3_import(source: str = "pig", pack: str = "", path: str = "") -> dict:
    def _do(m, c):
        if source == "pig":
            r = m["importer"].import_pig(pack=pack)
        elif source == "taobao":
            r = m["importer"].import_taobao(keyword=pack)
        elif source == "csv":
            r = m["importer"].import_csv(path, pack_code=pack)
        elif source == "json":
            r = m["importer"].import_json(path, pack_code=pack)
        else:
            return {"ok": False, "error": "source 只能是 pig/taobao/csv/json"}
        if r.get("imported") is None:
            return {"ok": False, "error": r.get("error") or "导入失败"}
        return {"ok": True, **dict(r), "lines": r.lines}
    return _wrap(_do)


def v3_chain(pids: list | None = None, store_id: str = "default") -> dict:
    return _wrap(lambda m, c: m["flow"].run_chain(c, pids, store_id=store_id))


# ---------------------------- 数据包（第二步） ----------------------------
# 一个数据包 = 一份导入文件 + 它在库里的商品。第三步只认这里转换成功的包。

def v3_packs() -> dict:
    """全部数据包 + 收件箱候选。

    ★ 2026-10-22 改（主人投诉「下拉里读不到数据包」）：
      原来只查 `v3_pack` 记账表，于是「商品明细 / 户外野营凳 / 自检」这些
      **库里有商品、但没有导入记账行**的包在第 4/5 步的下拉里根本选不到。
      现在改成：**以 v3_product 的 pack_code 分组为主**，再把 v3_pack 的元信息
      并上去（有元信息就用它的 status/source_file/时间，缺元信息就用实时计数补）。

    返回结构不变（items / inbox / inbox_dir），每个 item 至少带
    pack_code / products_live / images_live / ready，缺记账行的额外带 no_meta=True。
    """
    def _do(m, c):
        base = m["pack"].list_packs()          # 记账行（可能只有一部分包）
        rows = [dict(r) for r in c.execute(
            "SELECT p.pack_code AS code, COUNT(*) AS n,"
            " SUM(CASE WHEN p.status='blocked' THEN 1 ELSE 0 END) AS n_block,"
            " SUM(CASE WHEN p.status='fine_screened' THEN 1 ELSE 0 END) AS n_fine"
            " FROM v3_product p GROUP BY p.pack_code")]
        imgs = {str(r["code"] or ""): int(r["n"] or 0) for r in c.execute(
            "SELECT p.pack_code AS code, COUNT(*) AS n FROM v3_image i"
            " JOIN v3_product p ON p.product_id=i.product_id GROUP BY p.pack_code")}
        live = {str(r["code"] or ""): r for r in rows}

        # ★ 2026-10-24 修（成员包显示 0 件）：成员包的商品不在 v3_product.pack_code 上，
        #   上面按 pack_code 分组数出来是 0 —— 68 件、175 件那种成员包会被显示成空包。
        #   这里单独按 v3_pack_member 数一遍，下面覆写 p / i。
        mem_n: dict[str, int] = {}
        mem_i: dict[str, int] = {}
        try:
            for r in c.execute(
                    "SELECT pack_code AS code, COUNT(*) AS n FROM v3_pack_member"
                    " GROUP BY pack_code"):
                mem_n[str(r["code"] or "")] = int(r["n"] or 0)
            for r in c.execute(
                    "SELECT m.pack_code AS code, COUNT(*) AS n FROM v3_image i"
                    " JOIN v3_pack_member m ON m.product_id=i.product_id"
                    " GROUP BY m.pack_code"):
                mem_i[str(r["code"] or "")] = int(r["n"] or 0)
        except sqlite3.Error:
            pass

        items: list[dict] = []
        used: set[str] = set()
        for r in base:                          # ① 记账行在前，保持原有排序
            it = dict(r)
            code = str(it.get("pack_code") or "")
            used.add(code)
            lv = live.get(code)
            p = int(lv["n"] or 0) if lv else 0
            i = int(imgs.get(code, 0))
            if mem_n.get(code):                 # 成员包：以成员表为准（见上方注释）
                p = mem_n[code]
                i = mem_i.get(code, 0)
                it["is_member_pack"] = True
                it["members"] = p
            it["products_live"] = p
            it["images_live"] = i
            it["products"] = p
            it["ready"] = bool(p) and bool(i)
            if lv:
                it["products_blocked"] = int(lv["n_block"] or 0)
                it["products_fine"] = int(lv["n_fine"] or 0)
            items.append(it)
        rest = [k for k in live if k not in used and k]
        rest.sort(key=lambda k: int(live[k]["n"] or 0), reverse=True)
        for code in rest:                       # ② 只有商品、没有记账的包
            lv = live[code]
            p = int(lv["n"] or 0)
            i = int(imgs.get(code, 0))
            items.append({
                "pack_code": code, "products_live": p, "images_live": i, "products": p,
                "products_blocked": int(lv["n_block"] or 0),
                "products_fine": int(lv["n_fine"] or 0),
                "status": "converted" if p else "empty",
                "source_file": "", "created_at": "", "converted_at": "",
                "ready": bool(p) and bool(i), "no_meta": True,
                "reasons": ([] if p and i else ["没有图片"]) if p else ["没有商品"],
            })
        return {"ok": True, "items": items,
                "inbox": m["pack"].list_inbox(),
                "inbox_dir": m["pack"].ensure_dir(),
                "meta_missing": [x["pack_code"] for x in items if x.get("no_meta")],
                # ★ 2026-11-13 主人：「每个功能页它默认都是挑的最新的数据包」。
                #   本字段是所有下拉的唯一口径，页面不再各自 sort / 取第 0 项。
                "default": packlabel.choose_default(items)}
    return _wrap(_do)


def v3_pack_preview(path: str) -> dict:
    return _wrap(lambda m, c: m["pack"].preview(path))


def _label_new_pack(pack_code: str, stage: str = "") -> str:
    """导入成功后：给新包写一个「MM-DD 第N批 · 筛选前」显示名。

    只写 v3_pack.display_name，绝不碰 pack_code（十几处外键）。用户后来在 2.0
    改名会覆盖它；幂等：已有 display_name 的包不动（历史包 / 用户改过的都保留）。
    """
    code = (pack_code or "").strip()
    if not code:
        return ""
    def _do(m, c):
        try:
            row = c.execute("SELECT display_name FROM v3_pack WHERE pack_code=?",
                            (code,)).fetchone()
        except Exception:                                # noqa: BLE001
            return ""
        if not row:
            return ""
        try:
            cur = row["display_name"]
        except (TypeError, KeyError, IndexError):
            cur = row[0]
        if str(cur or "").strip():
            return ""
        name = packlabel.next_batch(c, stage or packlabel.STAGE_RAW)
        try:
            c.execute("UPDATE v3_pack SET display_name=?, updated_at=?"
                       " WHERE pack_code=? AND (display_name IS NULL OR display_name='')",
                       (name, time.strftime("%Y-%m-%d %H:%M:%S"), code))
            c.commit()
        except Exception:                                # noqa: BLE001
            return ""
        return name
    r = _wrap(_do)
    return str(r) if isinstance(r, str) else ""


def cardfile_classify(path: str) -> dict:
    """★ 2026-11-14：2.0 选文件按**后端适配器真识别**判定，不再看文件头 4KB 猜格式。

    老版拼多多页面快照（H:\\bdpan-txt-download\\*\\*.txt）顶层是
    ``{"store":{"initDataObj":{...}}}``，前 4KB 里根本没有 goods_id/server_time/ui，
    旧 UI 的 ``looks_detail_json`` 必然把它当列表文件挡掉 —— 那 8 个被拒样本全是
    合法详情卡（goodsID/goodsName/topGallery/detailGallery/skus 齐全）。
    detect_file 会完整 json.loads 后交给 ``adapters`` 注册表，认得出就返回平台码。
    认不出时顺手带一份「小样本字段速写」，UI 弹窗能说清是哪种列表/坏文件。
    """
    out = {"ok": False, "kind": "unknown", "platform": None,
           "display_name": "", "reason": "", "fields": {}}
    if not os.path.isfile(path):
        out["reason"] = "文件不存在"
        return out
    try:
        m = _v3()
        ad = m.get("adapters")
        plat, payload, err = ad.detect_file(path)
        if plat:
            out.update({"ok": True, "kind": "card", "platform": plat,
                        "display_name": ad.display_name(plat), "reason": ""})
            return out
        if isinstance(payload, dict):
            out["fields"] = {"top_keys": list(payload.keys())[:12],
                             "size": os.path.getsize(path)}
            ini = (payload.get("store") or {}).get("initDataObj") or {}
            if isinstance(ini, dict) and isinstance(ini.get("goods"), dict):
                out["fields"]["goods_keys"] = list(ini["goods"].keys())[:12]
        out["reason"] = err or "认不出平台"
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = "%s: %s" % (type(e).__name__, e)
        return out


def v3_pack_convert(path: str, pack_code: str = "") -> dict:
    """单文件导入。★ 2026-10-25 加分流：

    这是「2 · 商品卡导入」单文件按钮的真正入口。它原来只走 `pack.convert`，
    而 `pack.convert → importer.import_json` 是**19 列出表模板**的解析器 ——
    商品卡里的 `headImgs / videoUrls / skuSpecs / _raw.detail.detailImg`
    一个字段都不读，结果就是「导入成功、商品在、图 0 张、SKU 0 条」。

    实测铁证：同一份 18775 字节的卡，10-24 走 adapters 导入 → 25 张图；
    10-25 走这个按钮 → 0 张图。**不是数据丢了，是走了另一条解析路。**

    所以这里先让 adapters 认一下：认得出是商品卡（微店/微信小店/淘宝/拼多多）
    就走 `cards.import_folder(单文件模式)` —— 那条路会解析全部媒体、自动下载、OCR。
    认不出（比如 19 列 CSV、txt-json）才回落到原来的 `pack.convert`。
    """
    def _do(m, c):
        ad = m.get("adapters")
        cards_mod = m.get("cards")
        if ad and cards_mod and path and os.path.isfile(path):
            try:
                plat, _payload, _err = ad.detect_file(path)
            except Exception:  # noqa: BLE001
                plat = None
            if plat:
                r = cards_mod.import_folder(path, pack_name=pack_code, download=True, ocr=True)
                r["route"] = "cards/adapters（商品卡真解析）"
                return r
        r = m["pack"].convert(path, pack_code=pack_code)
        r["route"] = "pack.convert（19列模板解析）"
        return r
    r = _wrap(_do)
    # ★ 8.0 统一命名：导入成功后自动写「MM-DD 第N批 · 筛选前」显示名（连接已关，无锁竞争）
    if isinstance(r, dict) and r.get("ok"):
        _label_new_pack(str(r.get("pack_code") or ""))
    return r


def v3_pack_convert_many(files: list, pack_name: str = "",
                          download: bool = True, ocr: bool = True) -> dict:
    """**一批文件 → 一个数据包**（★ 2026-10-26 主人要求）。

    背景：界面多选文件时，原来 `PackConvertWorker` 是**逐文件循环调**
    `v3_pack_convert(f)`，于是 N 个文件产出 N 个单件包 —— 主人每次都得手动删，
    还容易把同一个商品导成多条（payload 相同却各占一个包）。

    现在整批一次交给 `cards.import_folder(files_override=[...])`：
    共享同一个 pack_code，一次解析、一次下载、一次 OCR、一次 SKU 入库。

    回落规则与单文件版一致：整批里**认得出商品卡的按商品卡走**；
    如果一个都认不出（纯 19 列 CSV / txt-json），回落到原来的 `pack.convert`
    逐文件转换 —— 保证旧的表格导入行为不变。
    """
    def _do(m, c):
        lst = [str(x) for x in (files or []) if x and os.path.isfile(str(x))]
        if not lst:
            return {"ok": False, "error": "没有有效的文件"}
        ad = m.get("adapters")
        cards_mod = m.get("cards")
        if ad and cards_mod:
            known = 0
            for p in lst:
                try:
                    plat, _pl, _er = ad.detect_file(p)
                except Exception:  # noqa: BLE001
                    plat = None
                if plat:
                    known += 1
            if known:
                r = cards_mod.import_folder(
                    "", pack_name=pack_name or ("批量导入_%d件" % len(lst)),
                    download=download, ocr=ocr, files_override=lst)
                r["route"] = "cards/adapters 批量（%d/%d 认得出商品卡）" % (known, len(lst))
                r["files"] = len(lst)
                return r
        # 整批都认不出 → 退回逐文件的老路，行为不变
        outs, ok_n = [], 0
        for p in lst:
            try:
                one = m["pack"].convert(p, pack_code=pack_name)
            except Exception as e:  # noqa: BLE001
                one = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if one.get("ok"):
                ok_n += 1
            outs.append({"file": os.path.basename(p),
                         "ok": bool(one.get("ok")),
                         "pack_code": one.get("pack_code"),
                         "error": one.get("error")})
        return {"ok": ok_n > 0, "route": "pack.convert 逐文件（都不认识）",
                "files": len(lst), "converted": ok_n, "detail": outs,
                "message": "逐文件转换 %d/%d（这批文件都不是商品卡格式）" % (ok_n, len(lst))}
    r = _wrap(_do)
    # ★ 8.0 统一命名：cards 整批一个包 / 逐文件老路每个成功的包，都写友好显示名
    if isinstance(r, dict) and r.get("ok"):
        codes = []
        if r.get("pack_code"):
            codes.append(str(r["pack_code"]))
        for x in (r.get("detail") or []):
            if isinstance(x, dict) and x.get("ok") and x.get("pack_code"):
                codes.append(str(x["pack_code"]))
        for code in codes:
            _label_new_pack(code)
    return r


def v3_pack_status(pack_code: str) -> dict:
    return _wrap(lambda m, c: m["pack"].status(pack_code))


def v3_pack_rename(pack_code: str, display_name: str) -> dict:
    """改数据包显示名（只写 v3_pack.display_name，不动 pack_code 外键）。"""
    return _wrap(lambda m, c: m["pack"].rename(pack_code, display_name))


# ---- 单件包合并（2026-10-24）：175 个「一个 JSON 一个包」并成一个成员包 ----
def v3_merge_single_packs(name: str = "", dry_run: bool = True,
                          pids: list | None = None) -> dict:
    """把单件包合并成一个数据包。**dry_run 默认 True**，只给计划不写库。

    引用式：只写 v3_pack_member，不动 v3_product.pack_code、不复制商品/图片。
    """
    def _do(m, c):
        if not m.get("pack_merge"):
            return {"ok": False, "error": "pack_merge 模块没加载上（看后端是否可以 import）"}
        return m["pack_merge"].merge(c, name=name, dry_run=dry_run, pids=pids)
    return _wrap(_do)


def v3_merge_verify(pack_code: str) -> dict:
    """合并包体检：件数/图数/来源包是否都还在/有无重复。"""
    def _do(m, c):
        if not m.get("pack_merge"):
            return {"ok": False, "error": "pack_merge 模块没加载上"}
        return m["pack_merge"].verify(c, pack_code)
    return _wrap(_do)


def v3_pack_delete(pack_code: str, purge: bool = False, purge_shared: bool = False,
                   cancel_tag: str = "") -> dict:
    """删一个包。**默认只删包与成员关系，不删商品**（2026-10-24 安全接管）。

    purge=True            → 连"没有别处引用"的商品一起删
    purge_shared=True     → 连"仍被别的包引用"的商品也删（需二次确认，界面上要打码确认）
    cancel_tag            → 配合 cancel_begin/cancel_fire；取消即整事务回滚
    """
    return _wrap(lambda m, c: m["pack"].delete_pack(pack_code, purge_products=purge,
                                                    purge_shared=purge_shared,
                                                    cancel_check=_cancel_check(cancel_tag)))


def v3_pack_impact(pack_code: str, purge: bool = False) -> dict:
    """删除前预告：会动到几个包/商品/图片/OCR，多少商品因被别处引用而保留。"""
    def _do(m, c):
        sd = getattr(m, "safedelete", None)
        if sd is None:
            try:
                from server.v3 import safedelete as sd   # noqa: N813
            except Exception:                            # noqa: BLE001
                return {"ok": False, "error": "safedelete 模块没加载上"}
        return sd.impact(c, pack_code, purge_products=purge)
    return _wrap(_do)


def v3_pack_purge_products(pack_code: str, confirm_code: str = "") -> dict:
    """彻底删除一个包的商品（危险操作）。confirm_code 必须等于 pack_code 才执行。"""
    if not pack_code or (confirm_code or "").strip() != pack_code:
        return {"ok": False, "error": "危险操作：确认码与包码不一致，已拒绝执行"}
    return v3_pack_delete(pack_code, purge=True, purge_shared=False)


# ---- 数据等级 / 回收站（2026-10-24 第一阶段）----

def v3_product_grade(product_id: int) -> dict:
    """一件商品的数据等级 L0–L4 + 缺失项 + 进第三步的阻断原因（现算，不看缓存）。"""
    return _wrap(lambda m, c: (m["grade"].evidence(c, int(product_id))
                               if m.get("grade") else {"ok": False, "error": "grade 模块没加载上"}))


def v3_pack_grades(pack_code: str, limit: int = 0) -> dict:
    """一个包逐件等级汇总（给界面「证据就绪 N / 待补 M」用）。"""
    def _do(m, c):
        if not m.get("grade"):
            return {"ok": False, "error": "grade 模块没加载上"}
        r = m["grade"].audit_pack(c, pack_code, limit=limit)
        return {"ok": True, "pack": pack_code, "count": r["count"],
                "by_grade": r["by_grade"], "summary": r["summary"],
                "rows": [{k: x[k] for k in ("product_id", "title", "grade", "missing",
                                            "blocked", "ready")} for x in r["rows"]]}
    return _wrap(_do)


def v3_pack_legacy_impact(pack_code: str) -> dict:
    """历史集合（包表/成员表都没行、只有商品归属）的删除前影响清单。"""
    def _do(m, c):
        if not m.get("trash"):
            return {"ok": False, "error": "trash 模块没加载上"}
        return dict(m["trash"].legacy_impact(c, pack_code), ok=True)
    return _wrap(_do)


def v3_pack_detach(pack_code: str, mode: str = "purge", cancel_tag: str = "") -> dict:
    """处理历史集合：detach 只解除归属 / trash 移入回收站 / purge 彻底删。

    ★ 2026-10-24 主人口径：**默认 purge**（删除就是彻底删除，不再留回收站副本）。
      仍被别的包引用的商品照样只解除归属，不会跟着消失。
    cancel_tag：取消即整事务回滚。
    """
    return _wrap(lambda m, c: (m["trash"].delete_collection(
        c, pack_code, mode=mode, cancel_check=_cancel_check(cancel_tag))
        if m.get("trash") else {"ok": False, "error": "trash 模块没加载上"}))


# ---- 2.5 批量：彻底删除 / 只移出包 / 回收站清空 / 孤儿清理（2026-10-24）----
# 主人原话：「A B C 一起动，然后后端回收站还有那边的都清掉。
#            下回这种清掉的就不要留了，删除就是彻底删除。」

# ---------------- 取消令牌（B-01，2026-10-25）----------------
# 界面在 worker 线程里跑删除，按钮在 GUI 线程里点「停止」——两边只有这个 dict 相连。
# tag 由调用方给（一般是 "purge" / "trash-clear" / "orphan" / "pack"），同一 tag 同时只该有一个任务；
# 重复 begin 会换一个新 Event（旧的作废，避免上一次的取消状态串到这一次）。
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def _cancel_event(tag: str):
    if not tag:
        return None
    with _CANCEL_LOCK:
        return _CANCEL_EVENTS.get(tag)


def cancel_begin(tag: str) -> dict:
    """开一个取消通道；返回 token（就是 tag 本身）。界面先调它，再调删除。"""
    tag = (tag or "").strip()
    if not tag:
        return {"ok": False, "error": "cancel_begin 需要 tag"}
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[tag] = threading.Event()
    return {"ok": True, "token": tag}


def cancel_fire(tag: str) -> dict:
    """请求取消（界面「停止」按钮调它）。**不杀线程**，只是打旗子，由后端在检查点收尾。"""
    ev = _cancel_event((tag or "").strip())
    if ev is None:
        return {"ok": False, "error": "没有这个取消通道（可能已经结束）"}
    ev.set()
    return {"ok": True, "token": tag, "fired": True}


def cancel_done(tag: str) -> dict:
    """收尾：任务结束后清掉通道，防止下次误用旧旗子。"""
    tag = (tag or "").strip()
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.pop(tag, None)
    return {"ok": True, "token": tag, "existed": bool(ev), "was_fired": bool(ev and ev.is_set())}


def _cancel_check(tag: str):
    """给后端用的检查函数；没 tag 就返回 None（=不可取消，行为与老版本一致）。"""
    if not tag:
        return None
    return lambda: bool(_cancel_event(tag) and _cancel_event(tag).is_set())


def v3_purge_impact(pids: list | None = None) -> dict:
    """删除前的影响清单：几件、几图、几条 OCR、几个本地文件、多少 MB、哪些是共享的。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].impact(c, list(pids or []))
    return _wrap(_do)


def v3_purge_products(pids: list | None = None, delete_files: bool = True,
                      force_shared: bool = False, cancel_tag: str = "") -> dict:
    """彻底删除商品（删行 + 删本地文件）。默认动作，不留回收站。

    cancel_tag：界面先用 cancel_begin(tag) 开通道，再传进来；停止按钮调 cancel_fire(tag)。
    """
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].purge_products(c, list(pids or []), delete_files=delete_files,
                                         force_shared=force_shared,
                                         cancel_check=_cancel_check(cancel_tag))
    return _wrap(_do)


def v3_detach_products(pids: list | None = None, cancel_tag: str = "") -> dict:
    """只移出当前包：清 pack_code 与成员行，商品 / 图 / OCR / 文件全留。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].detach_products(c, list(pids or []),
                                          cancel_check=_cancel_check(cancel_tag))
    return _wrap(_do)


def v3_trash_clear(delete_files: bool = True, cancel_tag: str = "") -> dict:
    """清空回收站（不留底）。清了就还原不了了，界面上要二次确认。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].clear_trash(c, actor="2.5 清空回收站", delete_files=delete_files,
                                      cancel_check=_cancel_check(cancel_tag))
    return _wrap(_do)


def v3_orphan_ocr_clean() -> dict:
    """OCR 行对应的图片已经不在了 → 删掉这些孤儿行。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].orphan_ocr(c)
    return _wrap(_do)


def v3_orphan_files_scan() -> dict:
    """扫媒体目录里没人引用的文件（只出清单，不删）。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        return m["purge"].scan_orphan_files(c)
    return _wrap(_do)


def v3_orphan_files_purge(paths: list | None = None, cancel_tag: str = "") -> dict:
    """按清单删孤儿文件；清单为空时先扫一遍再删（删前会重新校验引用）。"""
    def _do(m, c):
        if not m.get("purge"):
            return {"ok": False, "error": "purge 模块没加载上"}
        items = ([{"path": p} for p in paths] if paths
                 else (m["purge"].scan_orphan_files(c).get("items") or []))
        return m["purge"].purge_orphan_files(c, items, cancel_check=_cancel_check(cancel_tag))
    return _wrap(_do)


def v3_trash_list(limit: int = 300) -> dict:
    return _wrap(lambda m, c: ({"ok": True, "items": m["trash"].list_trash(c, limit)}
                               if m.get("trash") else {"ok": False, "error": "trash 模块没加载上"}))


def v3_trash_restore(trash_id: int) -> dict:
    return _wrap(lambda m, c: (m["trash"].restore_one(c, int(trash_id))
                               if m.get("trash") else {"ok": False, "error": "trash 模块没加载上"}))


# ---- 批量删包 / 批量打包（2026-10-24）----
# 主人原话：「这些数据包……要可以批量选择、批量删除、批量打包。现在还是不行，
# 只能一个一个选，然后一个一个删。」—— 所以下面两个入口都是**收一个包码列表**。

def _pack_pids(m, c, code: str) -> list[int]:
    """一个包里的商品 id。

    两种包的取法不同，混用会漏：成员包（v3_pack_member 有行）的商品
    `v3_product.pack_code` 指向源包，按 pack_code 查会得到 0 件。
    """
    try:
        mem = m["pack"].members(c, code)
    except Exception:                                   # noqa: BLE001
        mem = None
    if mem:
        return [int(x) for x in mem]
    return [int(r[0]) for r in c.execute(
        "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]


def v3_packs_delete_many(codes: list | None = None, purge: bool = False,
                         cancel_tag: str = "") -> dict:
    """批量删包。**默认只删包与成员关系，商品/图片/OCR 一律不动**（2026-10-24 安全接管）。

    逐个删、逐个记账：一个包删失败不影响其余（返回里 results 逐条给结果）。

    取消语义（B-01）：**按包为单位提交**，不是整批原子。取消后已删的包不回来，
    剩下的包不再处理 —— 返回里 cancelled=True、deleted/done 数量如实报。
    """
    def _do(m, c):
        todo = [str(x).strip() for x in (codes or []) if str(x).strip()]
        if not todo:
            return {"ok": False, "error": "没有选中数据包"}
        chk = _cancel_check(cancel_tag)
        out = []
        cancelled = False
        for code in todo:
            if chk is not None and chk():
                cancelled = True
                break
            try:
                # 一律走安全删除（含成员包：只摘成员 + 删包行，不碰商品）
                r = m["pack"].delete_pack(code, purge_products=purge)
                r = dict(r or {})
                r["kind"] = "member" if m["pack"].members(c, code) else "import"
            except Exception as e:                      # noqa: BLE001
                r = {"ok": False, "kind": "?", "error": "%s: %s" % (type(e).__name__, e)}
            r.setdefault("pack_code", code)
            out.append(r)
        fail = [x for x in out if not x.get("ok")]
        purged = sum(int(x.get("products_deleted") or 0) for x in out)
        msg = "按选中删了 %d/%d 个包（删掉商品 %d 件）" % (
            len(out) - len(fail), len(todo), purged)
        if cancelled:
            msg = "已取消：删了 %d/%d 个包后停止（按包提交，已删的不回滚），剩下 %d 个没动" % (
                len(out) - len(fail), len(todo), len(todo) - len(out))
        return {"ok": not fail and not cancelled, "total": len(todo), "done": len(out) - len(fail),
                "failed": len(fail), "results": out, "products_deleted": purged,
                "cancelled": cancelled, "processed": len(out),
                "errors": ["%s：%s" % (x.get("pack_code"), x.get("error")) for x in fail],
                "message": msg}
    return _wrap(_do)


def v3_packs_pack(codes: list | None = None, name: str = "",
                  dry_run: bool = True) -> dict:
    """把选中的几个包「打包」成一个新数据包（引用式成员包，不搬商品/不删原包）。

    **跨包重复的商品只算一次**（dedupe）。dry_run=True（默认）只算不写。
    """
    def _do(m, c):
        todo = [str(x).strip() for x in (codes or []) if str(x).strip()]
        if len(todo) < 2:
            return {"ok": False, "error": "打包至少要选 2 个数据包"}
        seen: set[int] = set()
        pids: list[int] = []
        per: list[dict] = []
        for code in todo:
            ps = _pack_pids(m, c, code)
            per.append({"pack_code": code, "products": len(ps)})
            for p in ps:
                if p not in seen:
                    seen.add(p)
                    pids.append(p)
        if not pids:
            return {"ok": False, "error": "选中的包里一个商品都没有",
                    "per": per, "packs": len(todo)}
        if dry_run:
            return {"ok": True, "dry_run": True, "packs": len(todo),
                    "products": len(pids), "per": per,
                    "message": "将把 %d 个包（跨包去重后 %d 件商品）打包成一个新数据包"
                               % (len(todo), len(pids))}
        r = m["pack"].save_members(
            c, name=name, pids=pids, source_pack="",
            only="all", note="打包自 %d 个包" % len(todo))
        r = dict(r or {})
        r["dry_run"] = False
        r["packs"] = len(todo)
        r["per"] = per
        r["dedup"] = {"raw": sum(x["products"] for x in per), "unique": len(pids)}
        if r.get("ok"):
            r["message"] = ("%d 个包 → 数据包「%s」（去重后 %s 件商品 · %s 张图）"
                            % (len(todo), r.get("pack_code"), r.get("count"),
                               r.get("images")))
        return r
    return _wrap(_do)


def v3_pack_to_pig(pack_code: str) -> dict:
    """v3 数据包 → 小猪吃的 19 列 CSV（GBK）。"""
    return _wrap(lambda m, c: m["pack"].export_pig_csv(pack_code))


def v3_fine_screen(pids: list | None = None) -> dict:
    return _wrap(lambda m, c: m["flow"].fine_screen(c, pids))


def v3_pack_pids(pack_code: str) -> dict:
    """取某个数据包里的全部商品 id —— 精筛按包跑，不把别的包一起筛。

    ★ 先看它是不是**成员包**（精筛之后存下来的那批）：是就返回成员列表；
      不是就走原来的逻辑（按 v3_product.pack_code 取）—— 老包行为完全不变。
    """
    def _do(m, c):
        mem = m["pack"].members(c, pack_code)
        if mem is not None:
            return {"ok": True, "count": len(mem), "pids": mem, "from": "members"}
        items = m["store"].list_products(c, pack=pack_code, limit=100000)
        return {"ok": True, "count": len(items),
                "pids": [int(x["product_id"]) for x in items], "from": "pack_code"}
    return _wrap(_do)


def pig_pending_count() -> dict:
    """从 9109 真实读一次「待鉴定件数」—— 链路是否真的通了，只有这一条是硬证据。

    端口在听、进程在跑都不算数：库里没商品时客户端一样空转。
    """
    import json as _json          # noqa: PLC0415
    import urllib.request         # noqa: PLC0415
    try:
        url = "http://127.0.0.1:9109/mock/openapi/jiantu/countPending"
        with urllib.request.urlopen(url, timeout=6) as r:
            d = _json.loads(r.read().decode("utf-8", "replace"))
        return {"ok": True, "total": int(d.get("Total") or 0), "shop": d.get("shopName") or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---- 本机鉴图引擎（免官方客户端，可直接无人跑完） -------------------------
# ★ 鉴图.py 在「本地鉴图」目录里，不是「4.0.5本地化」（那是服务与出表脚本所在）
LOCAL_DETECT = paths.PIG_READER / "鉴图.py"


_DETECT_PROC = None            # 正在跑的鉴图进程（供「停止」用）
_DETECT_STOP = False


def run_local_detect(pack_code: str = "", ids: list | None = None, limit: int = 0,
                     no_ai: bool = True, dry: bool = False,
                     timeout: int = 3600, log=None, words: str = "") -> dict:
    r"""跑本机鉴图引擎：PP-OCRv6 读字 → 词库命中 → 涂白 → 传图床 → 回写新URL。

    和官方客户端 PicDetector 用的是**同一个** 商品.db（本地鉴图\data\商品.db），
    所以这边跑完，官方客户端那边看到的状态也是已处理 —— 两条引擎可混用。

    log=回调：**逐行实时**把引擎输出交给界面（不攒到最后），界面据此推进度条。
    words=合并词库文件：官方 2376 条 + 夸张词 − 已豁免词（见 _detect_words_file）。
    """
    global _DETECT_PROC, _DETECT_STOP
    import json as _json          # noqa: PLC0415
    import re as _re              # noqa: PLC0415
    import subprocess             # noqa: PLC0415

    if not LOCAL_DETECT.exists():
        return {"ok": False, "error": f"找不到鉴图脚本：{LOCAL_DETECT}"}
    if not pack_code and not ids:
        return {"ok": False, "error": "必须指定包码或商品ID（不允许默认全库跑）"}
    if _DETECT_PROC is not None and _DETECT_PROC.poll() is None:
        return {"ok": False, "error": "已经有一个检测在跑，先点「停止」或等它跑完"}

    cmd = [str(paths.PY), str(LOCAL_DETECT)]
    if pack_code:
        cmd += ["--pack", pack_code]
    elif ids:
        cmd += ["--ids", ",".join(str(i) for i in ids)]
    if limit:
        cmd += ["--limit", str(limit)]
    if no_ai:
        cmd += ["--no-ai"]
    if dry:
        cmd += ["--dry"]
    wf = words or _detect_words_file()
    if wf:
        cmd += ["--words", wf]
        # ★ 用「长词优先 + ASCII 整词边界」匹配，与 2.5 / 第 3 步的
        #   v3.words.WordSet.match 逐条一致（2026-10-24 任务一）。
        #   不传这个参数，鉴图.py 会走它默认的「子串包含」——同一张图两边结论就会差。
        cmd += ["--match", "longest"]

    _DETECT_STOP = False
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(paths.PIG_READER), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"起不来：{type(e).__name__}: {e}"}
    _DETECT_PROC = proc
    try:
        for line in proc.stdout:            # 逐行读 → 实时回调
            line = (line or "").rstrip()
            if not line:
                continue
            lines.append(line)
            if log:
                try:
                    log(line)
                except Exception:  # noqa: BLE001
                    pass
            if len(lines) > 4000:
                lines = lines[-2000:]
        code = proc.wait(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        code = -1
        lines.append("中断：%s: %s" % (type(e).__name__, e))
    finally:
        _DETECT_PROC = None

    text = "\n".join(lines)
    stat = {}
    for m in _re.finditer(r'(\{[^{}]*"clean"[^{}]*\})', text):
        try:
            stat = _json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    return {"ok": code == 0 and not _DETECT_STOP, "code": code, "stat": stat,
            "stopped": _DETECT_STOP,
            "tail": "\n".join(text.strip().splitlines()[-8:])}


def detect_stop() -> dict:
    """请求停止正在跑的鉴图（连点安全）。"""
    global _DETECT_STOP
    _DETECT_STOP = True
    p = _DETECT_PROC
    if p is None or p.poll() is not None:
        return {"ok": True, "requested": False, "note": "当前没有在跑的检测"}
    try:
        p.terminate()
        return {"ok": True, "requested": True, "pid": p.pid}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}



def pig_baseline(pack_code: str) -> dict:
    """按包统计小猪库里图片的处理情况（跑前后各取一次，用来证明真的变了）。"""
    import sqlite3                # noqa: PLC0415
    if not paths.PIG_DB.exists():
        return {"ok": False, "error": "商品.db 不存在"}
    con = sqlite3.connect(str(paths.PIG_DB))
    try:
        q = ("SELECT COALESCE(处理状态,'') s, COUNT(*) n FROM 图片 "
             "WHERE goods_id IN (SELECT goods_id FROM 包内商品 WHERE 包码=?) "
             "GROUP BY s")
        rows = dict(con.execute(q, (pack_code,)).fetchall())
        tot = sum(rows.values())
        done = sum(v for k, v in rows.items() if k in ("已处理", "已替换", "不可读"))
        viol = con.execute(
            "SELECT COUNT(*) FROM 图片 WHERE goods_id IN "
            "(SELECT goods_id FROM 包内商品 WHERE 包码=?) AND COALESCE(违规类型,'')<>''",
            (pack_code,)).fetchone()[0]
        newurl = con.execute(
            "SELECT COUNT(*) FROM 图片 WHERE goods_id IN "
            "(SELECT goods_id FROM 包内商品 WHERE 包码=?) AND COALESCE(新URL,'')<>''",
            (pack_code,)).fetchone()[0]
        return {"ok": True, "total": tot, "done": done, "todo": tot - done,
                "violation": viol, "new_url": newurl, "by_state": rows}
    finally:
        con.close()


# ---- 精筛完成标记（按包） -------------------------------------------------
# 存在 data\fine_done.json 里，不碰 v3 库结构：{包码: 完成时间}
FINE_MARK_FILE = paths.DATA / "fine_done.json"


def _fine_marks() -> dict:
    import json as _json          # noqa: PLC0415
    try:
        return _json.loads(FINE_MARK_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def mark_fine_done(pack_code: str, note: str = "", source_pack: str = "") -> None:
    """记一笔「这个包精筛跑完了」。

    source_pack 非空时表示：这个包是**从已精筛的源包里筛出来的成员包**，
    它的完成状态继承自源包（不是自己偷偷改状态装成已精筛）。
    """
    import json as _json          # noqa: PLC0415
    d = _fine_marks()
    rec = {"at": paths.now_str(), "note": note}
    if source_pack:
        rec["source_pack"] = source_pack
    d[pack_code] = rec
    try:
        FINE_MARK_FILE.write_text(_json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _member_source_pack(pack_code: str) -> str:
    """成员包（v3_pack_member 有行）的来源包码；不是成员包就返回空。"""
    if not pack_code:
        return ""
    try:
        m = _v3()
        c = _v3conn(m)
        try:
            rows = [r[0] for r in c.execute(
                "SELECT DISTINCT source_pack FROM v3_pack_member"
                " WHERE pack_code=? AND source_pack IS NOT NULL AND source_pack<>''",
                (pack_code,))]
        finally:
            c.close()
        return rows[0] if rows else ""
    except Exception:  # noqa: BLE001
        return ""


def fine_done_of(pack_code: str) -> str:
    """这个包的「精筛完成时间」——没有就返回空串。

    ★ 成员包（第 3 步「存为数据包」存下来的那批）本身不重跑精筛，
      它的完成状态**继承自来源包**：只要源包确实跑完过，成员包就算跑完了。
      源包没跑过时照样返回空 —— 门禁照常拦住，不会把没精筛的货伪装成已精筛。
    """
    marks = _fine_marks()
    own = (marks.get(pack_code) or {}).get("at") or ""
    if own:
        return own
    src = _member_source_pack(pack_code)
    if src:
        return (marks.get(src) or {}).get("at") or ""
    return ""


def v3_profile_all(pids: list | None = None) -> dict:
    return _wrap(lambda m, c: m["flow"].profile_all(c, pids))


def v3_title_all(pids: list | None = None, store_id: str = "default",
                 count: int = 3, use_ai=None) -> dict:
    return _wrap(lambda m, c: m["flow"].title_all(c, pids, store_id=store_id,
                                                  count=count, use_ai=use_ai))


def v3_products(status: str | None = None, q: str | None = None,
                limit: int = 300) -> dict:
    return _wrap(lambda m, c: {
        "ok": True,
        "items": m["store"].list_products(c, status=status, q=q, limit=limit)})


def v3_product(pid: int) -> dict:
    def _do(m, c):
        p = m["store"].get_product(c, pid)
        if not p:
            return {"ok": False, "error": "没有这个商品"}
        return {"ok": True, "product": p,
                "images": m["images"].list_images(c, pid),
                "variants": m["titles"].list_variants(c, pid)}
    return _wrap(_do)


def v3_confirm_profile(pid: int) -> dict:
    return _wrap(lambda m, c: m["profile"].confirm(c, pid))


def fine_product_detail(pid: int) -> dict:
    """3.0 点商品名弹出的详情窗数据：商品字段 + 图（带本地路径与是否存在）+ 精筛理由 + AI 判词。

    ★ 为什么单独一个函数而不是复用 v3_product：那个返回的图没有「文件在不在」，
      v3_product 用于编辑页；这里要给人**看图核对**，路径取不到就必须显示「无本地文件」，
      不能拿 URL 假装是图（那是「编证据」）。
    """
    def _do(m, c):
        st = m["store"]
        p = st.get_product(c, pid)
        if not p:
            return {"ok": False, "error": "没有这个商品（product_id=%s）" % pid}
        note = st.unj(p.get("note"), {}) or {}
        imgs = []
        try:
            for it in st.list_images(c, pid):
                lp = (it.get("current_local_path") or it.get("local_path")
                      or it.get("original_local_path") or "")
                imgs.append({"image_id": it.get("image_id"),
                             "role": it.get("role") or "",
                             "seq": it.get("seq") or 0,
                             "url": it.get("url") or "",
                             "path": lp,
                             "exists": bool(lp) and os.path.isfile(str(lp)),
                             "note": it.get("note") or ""})
        except Exception as e:                       # noqa: BLE001
            imgs = []
            p["_img_err"] = "%s: %s" % (type(e).__name__, e)
        ai = note.get("ai") or {}
        return {"ok": True, "product": p, "images": imgs,
                "reasons": note.get("fine_screen") or [],
                "ai": ai, "judge": note.get("judge") or "",
                "verdict": note.get("verdict") or "",
                "at": note.get("at") or ""}
    return _wrap(_do)


def v3_core_suggest(pid: int) -> dict:
    return _wrap(lambda m, c: m["profile"].suggest_core(c, pid))


def v3_core_set(pid: int, word: str) -> dict:
    return _wrap(lambda m, c: m["profile"].set_core(c, pid, word))


def v3_generate_titles(pid: int, store_id: str = "default", count: int = 3,
                       use_ai=None) -> dict:
    return _wrap(lambda m, c: m["titles"].generate(
        c, pid, store_id=store_id, count=count, use_ai=use_ai))


def v3_select_variant(vid: int) -> dict:
    return _wrap(lambda m, c: m["titles"].select(c, vid))


def v3_reject_variant(vid: int) -> dict:
    return _wrap(lambda m, c: m["titles"].reject(c, vid))


def v3_copy_title(pid: int, store_id: str = "default") -> dict:
    return _wrap(lambda m, c: m["titles"].copy_text(c, pid, store_id))


def v3_handoff(pid: int, store_id: str = "default") -> dict:
    return _wrap(lambda m, c: m["images"].handoff(c, pid, store_id))


def v3_import_main(pid: int, path: str, note: str = "") -> dict:
    return _wrap(lambda m, c: m["images"].import_new(c, pid, path, note=note))


def v3_approve_main(pid: int, image_id: int) -> dict:
    return _wrap(lambda m, c: m["images"].approve(c, pid, int(image_id)))


def v3_revert_main(pid: int) -> dict:
    return _wrap(lambda m, c: m["images"].revert(c, pid))


# ---- 2026-10-25 第四批：非主图用途（详情图 / SKU 图 / 视频）的导入与版本确认 ----
# 4.1 里用户拿外部软件做好新图/新视频后，必须能"导入 → 确认这一版 → 待上传"，
# 而不是只有主图能换。这三个函数就是那条通道，复用的是 images.py 里同一套 v3_image 行。

def v3_import_media(pid: int, path: str, role: str = "main", note: str = "") -> dict:
    """导入外部加工好的新文件（图片或视频）→ 只生成 candidate，不动当前版本。"""
    return _wrap(lambda m, c: m["images"].import_new(c, pid, path, note=note, role=role))


def v3_approve_media(pid: int, image_id: int) -> dict:
    """把候选确认为该用途的当前版本；清空线上地址 ⇒ 进入待上传。"""
    return _wrap(lambda m, c: m["images"].approve_media(c, pid, int(image_id)))


def v3_revert_media(pid: int, image_id: int) -> dict:
    """退回原始版本（文件不删）。"""
    return _wrap(lambda m, c: m["images"].revert_media(c, pid, int(image_id)))


def v3_media_versions(pid: int, role: str = "") -> dict:
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在", "roles": []}
    try:
        m = _v3()
        if not m:
            return {"ok": False, "error": "v3 模块没加载", "roles": []}
        return m["images"].list_media_versions(c, int(pid), role)
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex), "roles": []}
    finally:
        try:
            c.close()
        except Exception:        # noqa: BLE001
            pass


def v3_export_doudian(pids=None, pack_code: str = "") -> dict:
    """抖店 16 列导出（一 SKU 一行）。

    模板基底 = `taobao-picker\\data\\templates\\抖店模板二.xlsx`（本机真实模板的拷贝）：
    行1 说明、行2 表头原样保留，行3 起写数据，模板自带示例会被清掉。
    模板没有视频列 ⇒ 视频不进表（仍在 v3_image 与本地物料包里，不丢）。
    """
    m = _v3()
    if not m or not m.get("doudian"):
        return {"ok": False, "error": "抖店导出模块没加载（server\\v3\\doudian.py 缺失或报错）"}
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在"}
    try:
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            ids = [int(x["product_id"])
                   for x in m["store"].list_products(c, pack=pack_code, limit=100000)]
        if not ids:
            return {"ok": False, "error": "没有选中的商品"}
        tpl = paths.TAOBAO_DIR / "data" / "templates" / "抖店模板二.xlsx"
        if not tpl.is_file():
            return {"ok": False,
                    "error": "找不到抖店模板：%s（把 抖店模板二.xlsx 放进 data\\templates）" % tpl}
        plan = m["doudian"].collect(c, ids)
        out_dir = paths.TAOBAO_DIR / "data" / "v3" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = paths.now_str().replace("-", "").replace(":", "").replace(" ", "_")
        out = out_dir / ("抖店_16列_%s.xlsx" % stamp)
        res = m["doudian"].export_xlsx(str(tpl), str(out), plan["blocks"])
        if not res.get("ok"):
            return res
        return {"ok": True, "files": [str(out)], "rows": res.get("rows"),
                "cols": res.get("cols"), "header_row": res.get("header_row"),
                "products": plan["products"], "warns": plan["warns"],
                "metas": plan["metas"],
                "note": "模板级结构已通过；抖店后台能否真导入，需你上传后自己确认"}
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        try:
            c.close()
        except Exception:        # noqa: BLE001
            pass


def bundle_build(pids=None, pack_code: str = "", make_xlsx: bool = True,
                 out_root: str = "", include_blocked: bool = False) -> dict:
    """生成本地**最终媒体包**：双平台表格 + 全部确认版媒体 + 三张清单 + 检查报告。

    与 `v3_export_package` / `v3_export_doudian` 的区别：那两个只出表，
    这一个**把当前确认版本的图片和视频真实复制到包里**，并给出一一对应清单。
    """
    m = _v3()
    if not m or not m.get("bundle"):
        return {"ok": False,
                "error": "媒体包模块没加载（server\\v3\\bundle.py 缺失或报错）"}
    db = paths.TAOBAO_DIR / "data" / "v3.db"
    if not db.is_file():
        return {"ok": False, "error": "v3.db 不存在"}
    c = sqlite3.connect(str(db), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            ids = [int(x["product_id"])
                   for x in m["store"].list_products(c, pack=pack_code, limit=100000)]
        return m["bundle"].build(c, pids=(ids or None),
                                 out_root=(out_root or None),
                                 make_xlsx=bool(make_xlsx),
                                 include_blocked=bool(include_blocked))
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        try:
            c.close()
        except Exception:        # noqa: BLE001
            pass


def cat_sources() -> dict:
    """列出「来源类目组」及各平台的映射状态（D-35 的入口数据）。"""
    m = _v3()
    if not m or not m.get("catmap"):
        return {"ok": False, "error": "类目模块没加载（server\\v3\\catmap.py 缺失或报错）"}
    db = paths.TAOBAO_DIR / "data" / "v3.db"
    if not db.is_file():
        return {"ok": False, "error": "v3.db 不存在"}
    c = sqlite3.connect(str(db), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        m["catmap"].ensure_table(c)
        cats = m["catmap"].source_cats(c)
        return {"ok": True, "groups": cats, "group_count": len(cats),
                "products": sum(x["count"] for x in cats),
                "complete": sum(1 for x in cats if x["complete"])}
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        c.close()


def cat_set(source_cat_id: str, platform: str, target_name: str,
            target_id: str = "") -> dict:
    """填/改/清一条类目映射（target_name 传空 = 清除）。"""
    m = _v3()
    if not m or not m.get("catmap"):
        return {"ok": False, "error": "类目模块没加载"}
    db = paths.TAOBAO_DIR / "data" / "v3.db"
    c = sqlite3.connect(str(db), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        return m["catmap"].set_map(c, source_cat_id, platform, target_name, target_id)
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        c.close()


def cat_apply(platform: str = "wechat", dry_run: bool = False) -> dict:
    """把映射写回商品的 category_path（导出读的就是这个字段）。"""
    m = _v3()
    if not m or not m.get("catmap"):
        return {"ok": False, "error": "类目模块没加载"}
    db = paths.TAOBAO_DIR / "data" / "v3.db"
    c = sqlite3.connect(str(db), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        return m["catmap"].apply_map(c, platform, dry_run=dry_run)
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        c.close()


def cat_validate(pids=None) -> dict:
    """导出前校验：哪些商品缺目标平台类目（缺就点名，不给默认值）。"""
    m = _v3()
    if not m or not m.get("catmap"):
        return {"ok": False, "error": "类目模块没加载"}
    db = paths.TAOBAO_DIR / "data" / "v3.db"
    c = sqlite3.connect(str(db), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        return m["catmap"].validate(c, [int(x) for x in (pids or [])] or None)
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
    finally:
        c.close()


def v3_release(pid: int) -> dict:
    def _do(m, c):
        ok = m["store"].set_product_status(c, pid, "imported", force=True)
        c.commit()
        return {"ok": ok, "product_id": pid, "note": "已放行，可继续流转"}
    return _wrap(_do)


def v3_export_package(pids: list | None = None, store_id: str = "default") -> dict:
    return _wrap(lambda m, c: m["packaging"].export_package(
        c, pids, store_id=store_id))


def v3_packages() -> dict:
    return _wrap(lambda m, c: {"ok": True, "items": m["packaging"].list_out_files()})


def v3_record_publish(pid: int, outcome: str = "submitted",
                      store_id: str = "default", listing_id: str = "",
                      note: str = "") -> dict:
    return _wrap(lambda m, c: m["packaging"].record_publish(
        c, pid, store_id, outcome=outcome, external_listing_id=listing_id, note=note))


def v3_out_dir() -> str:
    try:
        m = _v3()
        return m["store"].OUT_DIR
    except Exception:  # noqa: BLE001
        return str(paths.TAOBAO_DIR / "data" / "v3" / "out")


# ============================================================
#  七、第 4 步：检测打码（按包 · 实时进度 · 首图下载 · 改前改后）
# ============================================================

def _pig_conn():
    import sqlite3                # noqa: PLC0415
    con = sqlite3.connect(str(paths.PIG_DB), timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _detect_words_file() -> str:
    r"""返回「第 4 步打码 / 检测瘦身」要用的合并词库**文件路径**。

    ★ 2026-10-24 第二阶段任务一：这里原来自己读四个来源、自己合并，
      与 2.5 / 第 3 步的 images.build_words() 各算一套 —— 实测两张表差
      59 个词（A−B 16 个类目词、B−A 43 个营销词），同一张图给出相反结论。
      现在统一走 v3.words.write_file()：**内容就是 words.build() 那一份**，
      文件名带词表指纹（words-<条数>-<sha8>.txt），谁用哪一版一目了然。

    失败时退回空串（调用方就不传 --words，引擎用自身默认词库）——
      宁可退回旧行为，也不能抛异常把整个检测打掉。
    """
    try:
        m = _v3()
        mod = m.get("words")
        if mod is None:
            return ""
        c = m["flow"].conn()
        try:
            r = mod.write_file(c)
        finally:
            c.close()
        return r.get("path") or ""
    except Exception:  # noqa: BLE001
        return ""


def detect_progress(pack_code: str) -> dict:
    """按包盘点「第 4 步做到哪了」，一行一个商品（含首图/违规图/改后图链接）。

    数据源 = 小猪的 商品.db（本地鉴图\\data\\商品.db）—— 官方客户端与本机引擎共用它，
    所以这里读到的是**两条引擎共同的真状态**，不是我们自己记账。
    """
    import os as _os             # noqa: PLC0415
    if not paths.PIG_DB.exists():
        return {"ok": False, "error": "商品.db 不存在（小猪本地化库没建）"}
    con = _pig_conn()
    try:
        gs = [r["goods_id"] for r in con.execute(
            "SELECT goods_id FROM 包内商品 WHERE 包码=? ORDER BY 序号", (pack_code,))]
        if not gs:
            return {"ok": True, "total": 0, "done": 0, "viol": 0, "fixed": 0,
                    "pending": 0, "rows": [],
                    "note": "这个包在小猪库里没有商品 —— 是不是还没点过「启动本地链路」导入？"}
        ph = ",".join("?" * len(gs))
        imgs = [dict(r) for r in con.execute(
            "SELECT rowid AS image_id, * FROM 图片 WHERE goods_id IN (%s)" % ph, gs)]
        titles = {r["goods_id"]: (r["标题"] or "") for r in con.execute(
            "SELECT goods_id, 标题 FROM 商品 WHERE goods_id IN (%s)" % ph, gs)}
    finally:
        con.close()

    by_goods: dict[str, list[dict]] = {}
    for i in imgs:
        by_goods.setdefault(str(i.get("goods_id") or ""), []).append(i)

    total = done = viol_n = fixed_n = 0
    rows = []
    for g in gs:
        items = by_goods.get(g, [])
        mains = sorted([i for i in items if (i.get("类型") or "") == "主图"],
                       key=lambda x: (x.get("序号") or 0))
        main = mains[0] if mains else None
        vi = [i for i in items if (i.get("违规类型") or "").strip()]
        fxd = [i for i in vi if (i.get("新URL") or "").strip()
               and (i.get("新URL") or "") != (i.get("url") or "")]
        for i in items:
            total += 1
            if (i.get("处理状态") or "") in _DONE_STATES:
                done += 1
        viol_n += len(vi)
        fixed_n += len(fxd)
        if not items:
            status = "无图"
        elif vi and fxd:
            status = "已改 %d 张" % len(fxd)
        elif vi:
            status = "有违规 %d 张，未改" % len(vi)
        elif all((i.get("处理状态") or "") in _DONE_STATES for i in items):
            status = "无违规"
        else:
            status = "待处理"
        rows.append({
            "goods_id": g,
            "product_id": 0,
            "title": titles.get(g) or ("商品 %s" % g),
            "main_url": (main or {}).get("url") or "",
            "main_local": (main or {}).get("本地路径") or "",
            "main_new_url": (main or {}).get("新URL") or "",
            "status_text": status,
            "viol_images": [{
                "image_id": i.get("image_id"),
                "original_url": i.get("url") or "",
                "local_path": i.get("本地路径") or "",
                "new_url": i.get("新URL") or "",
                "hits": [x for x in ((i.get("命中词") or "").replace(",", "、")
                                     .replace(";", "、").split("、")) if x],
                "status": (i.get("处理状态") or "") or "待处理",
            } for i in vi],
        })
    return {"ok": True, "total": total, "done": done, "viol": viol_n,
            "fixed": fixed_n, "pending": max(0, total - done), "rows": rows,
            "note": ""}


def detect_download_heads(pack_code: str, progress=None) -> dict:
    """把这个包里**每个商品的首图**都下载到本地（主图另要做处理，不能只有 URL）。"""
    import hashlib                # noqa: PLC0415
    import os                     # noqa: PLC0415
    import urllib.request         # noqa: PLC0415
    if not paths.PIG_DB.exists():
        return {"ok": False, "error": "商品.db 不存在"}
    con = _pig_conn()
    ok = fail = skip = 0
    tfail: list[str] = []
    try:
        gs = [r["goods_id"] for r in con.execute(
            "SELECT goods_id FROM 包内商品 WHERE 包码=?", (pack_code,))]
        if not gs:
            return {"ok": False, "error": "这个包在库里没有商品"}
        ph = ",".join("?" * len(gs))
        mains = [dict(r) for r in con.execute(
            "SELECT rowid AS image_id, * FROM 图片 WHERE goods_id IN (%s)"
            " AND 类型='主图'" % ph, gs)]
        total = len(mains)
        base = paths.PIG_READER / "data" / "img"
        for n, m in enumerate(mains, 1):
            if progress:
                try:
                    progress(n, total, ok, fail)
                except Exception:  # noqa: BLE001
                    pass
            lp = (m.get("本地路径") or "").strip()
            if lp and os.path.exists(lp):
                skip += 1
                continue
            url = (m.get("url") or "").strip()
            if not url.startswith("http"):
                fail += 1
                if len(tfail) < 20:
                    tfail.append("%s 没有 http 地址" % m.get("goods_id"))
                continue
            gid = str(m.get("goods_id") or "0")
            ext = ".jpg"
            for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                if e in url.lower():
                    ext = e
                    break
            dst = base / gid / (hashlib.md5(url.encode()).hexdigest()[:16] + ext)
            dst.parent.mkdir(parents=True, exist_ok=True)
            got = False
            for _try in range(2):
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0", "Referer": "https://item.taobao.com/"})
                    with urllib.request.urlopen(req, timeout=25) as r:
                        data = r.read()
                    if len(data) < 1024:
                        raise RuntimeError("太小：%d 字节" % len(data))
                    dst.write_bytes(data)
                    got = True
                    break
                except Exception as e:  # noqa: BLE001
                    last = "%s: %s" % (type(e).__name__, e)
            if got:
                ok += 1
                con.execute("UPDATE 图片 SET 本地路径=?, 下载状态='成功' WHERE rowid=?",
                            (str(dst), m.get("image_id")))
            else:
                fail += 1
                if len(tfail) < 20:
                    tfail.append("%s %s" % (gid, locals().get("last", "下载失败")))
                con.execute("UPDATE 图片 SET 下载状态='失败' WHERE rowid=?", (m.get("image_id"),))
        con.commit()
        return {"ok": True, "total": total, "downloaded": ok, "failed": fail,
                "skipped": skip, "dir": str(base), "errors": tfail}
    finally:
        con.close()


# ---- 图床上传（第 5 步：先替换数据库里的链接，才允许出表） -------------------

def upload_status(pack_code: str) -> dict:
    def _do(m, c):
        if not m.get("upload"):
            return {"ok": False, "error": "后端待接入：taobao-picker\\server\\v3\\upload.py 还没生成",
                    "missing": -1, "rows": []}
        pids = [int(x["product_id"]) for x in m["store"].list_products(
            c, pack=pack_code, limit=100000)]
        return m["upload"].pending(c, pack_code, pids=pids)
    return _wrap(_do)


def upload_pending(pack_code: str, progress=None, limit: int = 0) -> dict:
    def _do(m, c):
        if not m.get("upload"):
            return {"ok": False, "error": "后端待接入：upload.py 还没生成"}
        pids = [int(x["product_id"]) for x in m["store"].list_products(
            c, pack=pack_code, limit=100000)]
        return m["upload"].upload_pending(c, pack_code, pids=pids, limit=limit,
                                          progress=progress)
    return _wrap(_do)


# ---------------------------------------------------------------- 换成微信图床地址
# ★ 2026-10-27 主人要求：商品要二次上架到抖音，抖音拿链接去拉图，
#   所以出表里的图片地址要是微信图床 mmecimage 的永久链接，不能是自建图床。

def upload_wx_status(pack_code: str) -> dict:
    """还差多少张图没换成微信地址（只读，不联网）。"""
    def _do(m, c):
        if not m.get("upload"):
            return {"ok": False, "error": "后端待接入：upload.py 还没生成"}
        pids = [int(x["product_id"]) for x in m["store"].list_products(
            c, pack=pack_code, limit=100000)]
        up = m["upload"]
        wx_ok, wx_msg = up.wechat_ready()
        pl = up.pending_wx(c, pack_code, pids=pids)
        if not pl.get("ok"):
            return pl
        return {"ok": True, "wx_ok": bool(wx_ok), "wx_msg": str(wx_msg)[:200],
                "todo": int(pl["total"]), "already_wx": int(pl["already_wx"]),
                "no_local": int(pl["no_local"]),
                "video_skipped": int(pl["video_skipped"]),
                "rows": pl["rows"][:200]}
    return _wrap(_do)


def upload_wx(pack_code: str, limit: int = 0, progress=None, should_stop=None) -> dict:
    """把所有要用的图换成微信图床地址（可限量、可中断、可反复跑）。"""
    def _do(m, c):
        if not m.get("upload"):
            return {"ok": False, "error": "后端待接入：upload.py 还没生成"}
        pids = [int(x["product_id"]) for x in m["store"].list_products(
            c, pack=pack_code, limit=100000)]
        return m["upload"].upload_wx(c, pack_code, pids=pids, limit=limit,
                                     progress=progress, should_stop=should_stop)
    return _wrap(_do)


def v3_image_urls(pack_code: str, write_csv: bool = True) -> dict:
    """把每个商品每张图最终会用哪个地址列出来，并落一份 CSV（给主人核对/给抖音用）。"""
    def _do(m, c):
        if not m.get("upload"):
            return {"ok": False, "error": "后端待接入：upload.py 还没生成"}
        pids = [int(x["product_id"]) for x in m["store"].list_products(
            c, pack=pack_code, limit=100000)]
        rep = m["upload"].url_report(c, pack_code, pids=pids)
        if not write_csv or not rep.get("ok"):
            return rep
        import csv                      # noqa: PLC0415
        import os as _os               # noqa: PLC0415
        import time as _t              # noqa: PLC0415
        out_dir = m["store"].OUT_DIR
        _os.makedirs(out_dir, exist_ok=True)
        name = "图片地址清单_%s_%s.csv" % (pack_code or "全部",
                                          _t.strftime("%Y%m%d_%H%M%S"))
        fp = _os.path.join(out_dir, name)
        cols = ["product_id", "image_id", "role", "seq", "wx",
                "used_url", "other_url", "local_path", "replaced"]
        head = ["商品ID", "图片ID", "角色", "序号", "是否微信图床",
                "出表将用的地址", "其它地址(旧)", "本地文件", "已替换"]
        with open(fp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            for r in rep["rows"]:
                w.writerow([r.get(k, "") for k in cols])
        rep["path"] = fp
        return rep
    return _wrap(_do)


# ---- 词库豁免（第 3 步「这不是违规词」按钮 + 设置面板里的台账） ---------------

def v3_keyword_waivers() -> dict:
    return _wrap(lambda m, c: {"ok": True, "items": m["keywords"].waivers(c)})


def v3_keyword_waive(words: list, reason: str = "", pids: list | None = None,
                     hits: int = 0) -> dict:
    def _do(m, c):
        r = m["keywords"].waive(c, words, reason=reason, actor="人", hits=hits)
        r["rescreen"] = None
        if pids:
            # 判决改了就必须重筛，否则界面上那一行还挂着旧理由
            r["rescreen"] = m["flow"].fine_screen(c, pids, actor="词库豁免后重筛")
        return r
    return _wrap(_do)


def v3_keyword_unwaive(text: str, pids: list | None = None) -> dict:
    def _do(m, c):
        r = m["keywords"].unwaive(c, text)
        if r.get("ok") and pids:
            r["rescreen"] = m["flow"].fine_screen(c, pids, actor="撤销豁免后重筛")
        return r
    return _wrap(_do)


# ---- 品牌词人工裁决（第一步 + 第三步共用同一份文件） --------------------------
#
# ★ 2026-10-24 主人要求：「第二步和第三步要加一个块，随时可以调整这些词 ——
#   能把词从品牌命中里清掉，也能添加（比如石福康就是品牌）」。
#   底层是 taobao-picker\server\brand_lexicon.py，原始词表一个字节不动。

def brand_words_stats() -> dict:
    def _do(m, c):
        import brand_lexicon
        return brand_lexicon.stats()
    return _wrap(_do)


def brand_words_search(q: str, limit: int = 60) -> dict:
    def _do(m, c):
        import brand_lexicon
        return brand_lexicon.search(q, limit=limit)
    return _wrap(_do)


def brand_words_add(words: list, note: str = "") -> dict:
    def _do(m, c):
        import brand_lexicon
        return brand_lexicon.add(words, note=note)
    return _wrap(_do)


def brand_words_remove(words: list, note: str = "") -> dict:
    def _do(m, c):
        import brand_lexicon
        return brand_lexicon.remove(words, note=note)
    return _wrap(_do)


def brand_words_restore(word: str) -> dict:
    def _do(m, c):
        import brand_lexicon
        return brand_lexicon.restore(word)
    return _wrap(_do)


# ---- 敏感词 / 违规词（3.0 右侧竖向面板用，2026-10-25 新增）-------------------
# 与品牌词**分开两套**：品牌词在 brand_lexicon（品牌收录表 + 人工裁决），
# 违规/营销/夸张词在 v3_keyword（kind=violation/marketing/hype）。
# 两块面板各自读写自己那套，不互相覆盖（P0-4 §6.3 要求）。

VIOLATION_KINDS = ("violation", "marketing", "hype")


def v3_word_stats() -> dict:
    """词库总账：按 kind × status 计数（违规词面板顶部那一行）。"""
    def _do(m, c):
        if not m.get("keywords"):
            return {"ok": False, "error": "keywords 模块没加载上"}
        st = m["keywords"].stats(c)
        d = {"ok": True, **st}
        d["violation_total"] = sum(int((st.get(k) or {}).get("active", 0))
                                   + int((st.get(k) or {}).get("disabled", 0))
                                   for k in VIOLATION_KINDS)
        d["waived"] = c.execute("SELECT COUNT(*) FROM v3_keyword_waiver").fetchone()[0]
        return d
    return _wrap(_do)


def v3_word_search(q: str = "", kinds: list | None = None,
                   limit: int = 80, include_disabled: bool = True) -> dict:
    """查违规词 / 营销词 / 夸张词（默认三类一起查）。

    include_disabled=True 时连「已停用/已豁免」的一起列出来 —— 面板要能显示
    「这个词现在是算还是不算」，否则人工没法确认自己刚才那一下到底生效没有。
    """
    def _do(m, c):
        ks = [str(k) for k in (kinds or VIOLATION_KINDS)]
        if not ks:
            return {"ok": False, "error": "要指定词类"}
        ph = ",".join("?" * len(ks))
        w = ["kind IN (%s)" % ph]
        args = list(ks)
        if q:
            w.append("text LIKE ?")
            args.append("%%%s%%" % q)
        if not include_disabled:
            w.append("status<>'disabled'")
        where = " AND ".join(w)
        rows = [dict(r) for r in c.execute(
            "SELECT text, kind, status, COALESCE(source,'') AS source,"
            "       COALESCE(category_scope,'') AS category_scope"
            "  FROM v3_keyword WHERE " + where +
            " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,"
            "          length(text), text LIMIT ?", tuple(args + [int(limit)]))]
        total = c.execute("SELECT COUNT(*) FROM v3_keyword WHERE " + where,
                          tuple(args)).fetchone()[0]
        return {"ok": True, "items": rows, "total": total, "kinds": ks}
    return _wrap(_do)


def v3_word_waive(words: list, reason: str = "") -> dict:
    """把违规词判成「不算违规」（置 disabled + 台账，可撤销）。"""
    def _do(m, c):
        if not m.get("keywords"):
            return {"ok": False, "error": "keywords 模块没加载上"}
        return m["keywords"].waive(c, list(words or []), reason=reason, actor="3.0")
    return _wrap(_do)



# ============================================================
#  八、第 3 步精筛 · 图片层 / 详情层（2026-10-22 新增）
# ============================================================
#  后端：taobao-picker\server\v3\flow.py → fine_screen(with_images=, with_detail=)
#        taobao-picker\server\v3\images.py → ocr_product() / ocr_image() / build_words()
#  引擎：第 4 步那台本机 PP-OCRv6（J:\aixitong\小猪\本地鉴图\鉴图.py），不联网、不新造。

# ---- 兼容垫片：v3 后端现在的正确导入路径是 server.v3 -------------------------
# 实测（2026-10-22 16:40）：`from v3 import flow` 会抛
#   ImportError: attempted relative import beyond top-level package
# 原因：v3\packaging.py 里有一句 `from .. import export`，它要求 v3 必须是**某个包的子包**；
#      而 paths.ensure_backend_path() 只把 server\ 加进 sys.path，于是 v3 成了顶层包，
#      `..` 就越界了 —— 结果**第 3/4/5 步整条 v3 链路全挂**（不只是精筛）。
# 不改 packaging.py（别人在动），这里在末尾补一层别名：
#   先把 server.v3.* 真正导进来，再登记成 v3.*，让已有的 `from v3 import ...` 原样能跑。
def _alias_v3_package() -> str:
    import importlib              # noqa: PLC0415
    import sys as _sys            # noqa: PLC0415
    base, srv = str(paths.TAOBAO_DIR), str(paths.TAOBAO_SERVER)
    for p in (srv, base):         # base 也必须在，server 才是可导入的包
        if p not in _sys.path:
            _sys.path.insert(0, p)
    if not (paths.TAOBAO_DIR / "server").is_dir():
        return "找不到 %s" % (paths.TAOBAO_DIR / "server")
    # 清掉之前失败留下的半截 v3.*（否则 from v3 import X 会拿到空模块）
    for k in [k for k in list(_sys.modules) if k == "v3" or k.startswith("v3.")]:
        _sys.modules.pop(k, None)
    try:
        pkg = importlib.import_module("server.v3")
    except Exception as e:        # noqa: BLE001
        return "server.v3 导不进来：%s: %s" % (type(e).__name__, e)
    err = ""
    for name in ("store", "keywords", "images", "titles", "profile", "packaging",
                 "importer", "flow", "pack", "upload"):
        try:
            m = importlib.import_module("server.v3." + name)
        except Exception as e:    # noqa: BLE001
            if name in ("store", "flow", "images"):
                err = "server.v3.%s 导不进来：%s: %s" % (name, type(e).__name__, e)
            continue
        _sys.modules["v3." + name] = m
        try:
            setattr(pkg, name, m)
        except Exception:         # noqa: BLE001
            pass
    _sys.modules["v3"] = pkg
    return err


_V3_ALIAS_ERR = _alias_v3_package()


def v3_backend_error() -> str:
    """v3 后端到底能不能用 —— 不能就把真实原因给界面，不许只报一个空数字。"""
    return _V3_ALIAS_ERR


def v3_fine_screen_pro(pack_code: str = "", pids: list | None = None,
                       with_images: bool = True, with_detail: bool = True,
                       limit: int = 0, max_images_per_product: int = 0,
                       force_ocr: bool = False, with_ai: bool = True) -> dict:
    r"""精筛（含图片层 / 详情层 / AI 判官）：按包或按 id 跑，可分段可止损。

    limit=N 只处理 N 件（断点续跑：已 OCR 的图吃缓存，第二次不再读也不不再下）。
    with_ai=True 时判定权交 AI（2026-10-27 主人口径），规则只当证据。
    """
    def _do(m, c):
        ids = [int(x) for x in pids] if pids else None
        if ids is None:
            if not pack_code:
                return {"ok": False, "error": "必须指定数据包或商品 id（不允许默认全库跑）"}
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
            if not ids:
                return {"ok": False, "error": "包「%s」里没有商品" % pack_code}
        r = m["flow"].fine_screen(c, ids, actor="上货台精筛",
                                  with_images=with_images, with_detail=with_detail,
                                  limit=int(limit or 0),
                                  max_images_per_product=int(max_images_per_product or 0),
                                  force_ocr=bool(force_ocr),
                                  with_ai=bool(with_ai))
        r["pack_code"] = pack_code
        r["pids"] = len(ids)
        return r
    return _wrap(_do)


def v3_fine_evidence(pack_code: str = "", pids: list | None = None,
                     limit: int = 300) -> dict:
    """取「上次精筛的证据」，**不重跑 OCR** —— 刷新界面看证据用它。

    每条 = 商品 + 判定 + 图证据（图角色#序号 · 命中词 · OCR 片段）+ 全部理由原文。
    """
    def _do(m, c):
        st = m["store"]
        ids = [int(x) for x in pids] if pids else None
        if ids is None:
            if not pack_code:
                return {"ok": False, "error": "必须指定数据包"}
            ids = [int(x["product_id"]) for x in
                   st.list_products(c, pack=pack_code, limit=100000)]
        out = []
        for pid in ids[:max(1, int(limit))]:
            p = st.get_product(c, pid)
            if not p:
                continue
            note = st.unj(p.get("note"), {}) or {}
            reasons = note.get("fine_screen") or []
            layers = note.get("layers") or {}
            ev, why, n_block, n_warn = [], [], 0, 0
            for r in reasons:
                if r.get("level") == "block":
                    n_block += 1
                else:
                    n_warn += 1
                if r.get("layer") == "image":
                    ev.append("%s · %s · %s" % (r.get("image") or "图",
                                                r.get("word") or "",
                                                (r.get("snippet") or "")[:60]))
                elif r.get("code") in ("desc_risk_word", "desc_suspected_brand",
                                       "brand_mismatch", "material_mismatch",
                                       "no_description", "no_detail_image", "thin_info"):
                    ev.append("详情 · %s · %s" % (r.get("word") or "",
                                                 str(r.get("msg") or "")[:60]))
                why.append(str(r.get("msg") or r.get("code") or ""))
            out.append({"product_id": pid,
                        "title": p.get("original_title") or "",
                        "verdict": "blocked" if (p.get("status") == "blocked" or n_block)
                                   else "passed",
                        "status": p.get("status") or "",
                        "n_block": n_block, "n_warn": n_warn,
                        "layers": layers, "evidence": ev[:6],
                        "manual": note.get("manual") if isinstance(note.get("manual"), dict)
                                  else None,
                        "ai_verdict": (note.get("ai") or {}).get("verdict") or "",
                        "ai_comment": (note.get("ai") or {}).get("comment") or "",
                        "ai_model": (note.get("ai") or {}).get("model") or "",
                        "why": "；".join(why)[:220],
                        "at": note.get("at") or ""})
        ocr = {}
        try:
            ocr = m["images"].ocr_stats(c, ids)
        except Exception:              # noqa: BLE001
            ocr = {}
        return {"ok": True, "pack_code": pack_code, "total": len(ids),
                "rows": out, "ocr": ocr}
    return _wrap(_do)


# ============================================================
#  八·五、AI 模型池 / AI 精筛开关（2026-10-27 主人指令）
# ============================================================
#  为什么要有这一节：主人原话「它这个是平台的活动，有的时候免费，有的时候它不免费」
#  —— 所以「配一个模型」不成立。这里只是把 server\ai_providers.py 的池子
#  暴露给设置面板：列池、探活、点选即换、增删。

def _ai():
    if not paths.ensure_backend_path():
        raise RuntimeError("找不到后端目录：%s" % paths.TAOBAO_SERVER)
    import ai_providers                              # noqa: PLC0415
    return ai_providers


def ai_status() -> dict:
    """两个角色的现状 + text 角色的池子（不含密钥）。"""
    try:
        A = _ai()
        st = A.status()
        return {"ok": True, "roles": st["roles"], "pool": A.pool_of("text"),
                "config": st.get("config_file")}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_pool_probe(role: str = "text", timeout: int = 25) -> dict:
    """把池里每个都真跑一句话 —— 只看模型列表不算数（上游会列出来但已下线）。"""
    try:
        rows = _ai().probe_pool(role, timeout=int(timeout))
        return {"ok": True, "rows": rows,
                "usable": sum(1 for r in rows if r["ok"]), "total": len(rows)}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_set_active(idx: int, role: str = "text") -> dict:
    try:
        return _ai().set_active(role, int(idx))
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_pool_add(label: str = "", base: str = "", model: str = "", api_key: str = "",
                max_tokens: int = 2048, role: str = "text") -> dict:
    """往池子里加一个候选（免费模型天天变，必须能现场加）。"""
    try:
        A = _ai()
        cfg = A.load_config(force=True)
        r = (cfg.get("roles") or {}).get(role)
        if not r:
            return {"ok": False, "error": "角色 %s 不存在" % role}
        base, model = (base or "").strip().rstrip("/"), (model or "").strip()
        if not base or not model:
            return {"ok": False, "error": "地址和模型名都必须填"}
        pool = r.setdefault("pool", [])
        if any(str(x.get("base") or "").rstrip("/") == base and x.get("model") == model
               for x in pool) or (str(r.get("base")).rstrip("/") == base
                                 and r.get("model") == model):
            return {"ok": False, "error": "池里已经有这个模型了"}
        pool.append({"label": (label or model).strip(), "base": base, "model": model,
                     "api_key": api_key or r.get("api_key") or "", "kind": "openai",
                     "max_tokens": int(max_tokens or 2048), "temperature": 0.3,
                     "timeout": 90})
        A.save_config(cfg)
        return {"ok": True, "pool": A.pool_of(role)}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_pool_remove(pool_idx: int, role: str = "text") -> dict:
    try:
        A = _ai()
        cfg = A.load_config(force=True)
        r = (cfg.get("roles") or {}).get(role) or {}
        pool = r.get("pool") or []
        i = int(pool_idx)
        if not (0 <= i < len(pool)):
            return {"ok": False, "error": "池序号越界"}
        gone = pool.pop(i)
        # 删掉的正好是 active 下标 → 清掉 active，免得指到别处
        if r.get("active") == i:
            r.pop("active", None)
        elif isinstance(r.get("active"), int) and r["active"] > i:
            r["active"] -= 1
        A.save_config(cfg)
        return {"ok": True, "removed": gone.get("model"), "pool": A.pool_of(role)}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_fine_status() -> dict:
    """3.0 的 AI 判官开没开、用哪个模型、池里几个能打。"""
    try:
        mod = __import__("v3.ai_fine", fromlist=["*"])
        av = mod.available()
        c = mod.cfg()
        return {"ok": True, "on": bool(c.get("enabled")), "available": av,
                "role": c.get("role"), "timeout": c.get("timeout"),
                "extra": c.get("extra") or ""}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_fine_set(on: bool) -> dict:
    """开/关 AI 判官（写 config\\ai_fine.json，热读，不用重启）。"""
    try:
        import json as _json                         # noqa: PLC0415
        p = paths.TAOBAO_DIR / "config" / "ai_fine.json"
        d = {}
        try:
            d = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:                            # noqa: BLE001
            d = {}
        d["enabled"] = bool(on)
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(p))
        return {"ok": True, "on": bool(on)}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 判定提示词（可改）
#  主人原话：「这个提示词后面要改的话，现在就没有地方改」「他出了结果出了点评，
#  我可以点评它的结果，他会根据我的话进一步把之前的提示词进行丰富」

def _ai_fine():
    if not paths.ensure_backend_path():
        raise RuntimeError("找不到后端目录：%s" % paths.TAOBAO_SERVER)
    return __import__("v3.ai_fine", fromlist=["*"])


def ai_rules_get() -> dict:
    """当前判定提示词（AI 按这个判）+ 是否用默认生成 + 上次保存时间。"""
    try:
        m = _ai_fine()
        c = m.cfg()
        return {"ok": True, "text": m.rules_text(),
                "using_default": not str(c.get("rules_text") or "").strip(),
                "default_text": m.default_rules_text(),
                "saved_at": c.get("_rules_saved_at") or "",
                "chars": len(m.rules_text())}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_rules_set(text: str) -> dict:
    try:
        return _ai_fine().set_rules_text(text)
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_rules_reset() -> dict:
    try:
        return _ai_fine().reset_rules_text()
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_feedback_list() -> dict:
    try:
        m = _ai_fine()
        return {"ok": True, "items": m.feedback_list()}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "items": []}


def ai_feedback_add(pid, title: str = "", comment: str = "", ai_verdict: str = "",
                    ai_comment: str = "", verdict_now: str = "") -> dict:
    try:
        return _ai_fine().feedback_add(pid, title, comment, ai_verdict, ai_comment,
                                       verdict_now)
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_feedback_del(idx: int) -> dict:
    try:
        return _ai_fine().feedback_del(idx)
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_feedback_apply(keep: bool = False) -> dict:
    """把点评逐条并入规则（不调模型，可控）。"""
    try:
        return _ai_fine().feedback_apply(keep=keep)
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def ai_feedback_refine(timeout: int = 120) -> dict:
    """让模型把点评归纳成规则建议（**只返回，不自动保存** —— 改口径要人点头）。"""
    try:
        return _ai_fine().refine(timeout=timeout)
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 选品条件（2026-10-28）
#  主人原话：「现在的商品精筛根本就没有相应的条件，相当于提示词都没有，随随便便筛的…
#  把这些因素都做成一个设置页放进去，方便后期变更。这样即便是后期换了 API，
#  它只要通过这道提示词知道我们选品的条件了，也可以很自然地把需要的品合适的品选出来。」
#  真源 = taobao-picker\config\selection_criteria.json（界面改它、AI 读它、闸门用它）

def criteria_status() -> dict:
    """条件摘要 + 生成的提示词前 600 字（界面顶部显示用）。"""
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载（检查 server/v3/criteria.py）"}
        d = {"ok": True, "path": cr.CFG}
        d.update(cr.summary())
        d["prompt_head"] = cr.render_rules()[:600]
        return d
    return _wrap(_do)


def criteria_get() -> dict:
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        return {"ok": True, "data": cr.load(), "path": cr.CFG}
    return _wrap(_do)


def criteria_preview(data: dict | None = None) -> dict:
    """不落盘，只把「如果保存，AI 会看到什么提示词」渲染出来。"""
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        txt = cr.render_rules(data if isinstance(data, dict) and data else cr.load())
        return {"ok": True, "text": txt, "chars": len(txt)}
    return _wrap(_do)


def criteria_save(data: dict, editor: str = "界面") -> dict:
    """保存条件 → 立刻热读生效（不用重启），并返回新的提示词长度。"""
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        r = cr.save(data, editor=editor)
        if r.get("ok"):
            r["summary"] = cr.summary()
        return r
    return _wrap(_do)


def criteria_reset(confirm: bool = False) -> dict:
    """还原成系统默认条件（**先备份现文件**，可回退）。"""
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        if not confirm:
            return {"ok": False, "error": "还原会把当前条件换成默认值；确认请传 confirm=True"}
        r = cr.save(cr.default(), editor="还原默认")
        if r.get("ok"):
            r["summary"] = cr.summary()
        return r
    return _wrap(_do)


def criteria_gate(title: str = "", price=None, desc: str = "", ocr_text: str = "",
                  shop: str = "", category: str = "") -> dict:
    """拿一件真货试判（本地闸门，不花钱）：看条件到底会把它judge成什么样。"""
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        g = cr.gate(title=title, price=price, desc=desc, ocr_text=ocr_text,
                    shop=shop, category=category)
        g["ok"] = True
        g["mask"] = cr.need_mask(ocr_text=ocr_text, title=title)
        return g
    return _wrap(_do)


def criteria_mask(ocr_text: str = "", title: str = "") -> dict:
    def _do(m, c):
        cr = m.get("criteria")
        if cr is None:
            return {"ok": False, "error": "criteria 模块没加载"}
        r = cr.need_mask(ocr_text=ocr_text, title=title)
        r["ok"] = True
        return r
    return _wrap(_do)


def ai_prompt_now() -> dict:
    """AI 判官此刻真正喂给模型的规则段（含店长点评叠加前的原样规则）。"""
    def _do(m, c):
        af = m.get("ai_fine")
        if af is None:
            return {"ok": False, "error": "ai_fine 模块没加载"}
        return {"ok": True, "text": af.rules_text(),
                "using_default": not str((af.cfg() or {}).get("rules_text") or "").strip(),
                "chars": len(af.rules_text())}
    return _wrap(_do)


# ---------------------------------------------------------------- 单件改判定（人工）
#  主人原话：「有些商品他判定是待查还是通过，这个要可以单个单个修改」

_VERDICT2STATUS = {"pass": "fine_screened", "block": "blocked", "pending": "pending"}


def v3_set_verdict(pid: int, verdict: str, note: str = "") -> dict:
    """人工改一件的判定。**保留 AI 判词**，另写 manual 字段，重跑精筛也不会被抹掉。"""
    v = str(verdict or "").lower()
    if v not in _VERDICT2STATUS:
        return {"ok": False, "error": "判定只能是 pass / block / pending"}
    def _do(m, c):
        st = m["store"]
        p = st.get_product(c, pid)
        if not p:
            return {"ok": False, "error": "没有这个商品"}
        note_obj = st.unj(p.get("note"), {}) or {}
        note_obj["manual"] = {"verdict": v, "at": st.now(),
                              "by": "人", "note": str(note or "")[:300],
                              "ai_verdict": (note_obj.get("ai") or {}).get("verdict") or ""}
        note_obj["verdict"] = _VERDICT2STATUS[v].replace("fine_screened", "passed")
        c.execute("UPDATE v3_product SET note=?, status=?, updated_at=? WHERE product_id=?",
                  (st.j(note_obj), _VERDICT2STATUS[v], st.now(), pid))
        c.commit()
        return {"ok": True, "product_id": pid, "verdict": v,
                "status": _VERDICT2STATUS[v]}
    return _wrap(_do)


def v3_set_verdict_many(pids: list | None = None, verdict: str = "pass",
                        note: str = "") -> dict:
    """批量人工改判（2026-10-27 主人要求：勾选一批，一次改判定）。

    与单件同一套语义（写 note.manual，保留 AI 判词，重筛不覆盖）；
    差别只有一条：**一次事务提交**，界面只刷新一遍 —— 勾 100 件不会卡 100 次。
    """
    v = str(verdict or "").lower()
    if v not in _VERDICT2STATUS:
        return {"ok": False, "error": "判定只能是 pass / block / pending"}
    ids = [int(x) for x in (pids or [])]
    if not ids:
        return {"ok": False, "error": "没有勾选任何商品"}

    def _do(m, c):
        st = m["store"]
        ok, fail = [], []
        for pid in ids:
            try:
                p = st.get_product(c, pid)
                if not p:
                    fail.append({"product_id": pid, "error": "没有这个商品"})
                    continue
                note_obj = st.unj(p.get("note"), {}) or {}
                note_obj["manual"] = {"verdict": v, "at": st.now(), "by": "人（批量）",
                                      "note": str(note or "")[:300],
                                      "ai_verdict": (note_obj.get("ai") or {}).get("verdict") or ""}
                note_obj["verdict"] = _VERDICT2STATUS[v].replace("fine_screened", "passed")
                c.execute("UPDATE v3_product SET note=?, status=?, updated_at=?"
                          " WHERE product_id=?",
                          (st.j(note_obj), _VERDICT2STATUS[v], st.now(), pid))
                ok.append(pid)
            except Exception as e:            # noqa: BLE001
                fail.append({"product_id": pid, "error": "%s: %s" % (type(e).__name__, e)})
        c.commit()                            # 一次提交
        return {"ok": True, "verdict": v, "changed": len(ok), "ok_ids": ok,
                "failed": fail[:20], "failed_n": len(fail)}
    return _wrap(_do)


def v3_reset_verdict(pid: int) -> dict:
    """撤销人工改判，回到 AI 当时给的判定（AI 判词一直在 note.ai 里）。"""
    def _do(m, c):
        st = m["store"]
        p = st.get_product(c, pid)
        if not p:
            return {"ok": False, "error": "没有这个商品"}
        note_obj = st.unj(p.get("note"), {}) or {}
        note_obj.pop("manual", None)
        ai = note_obj.get("ai") or {}
        av = ai.get("verdict") if ai.get("ok") else ""
        back = {"block": "blocked", "pass": "fine_screened", "warn": "pending"}.get(av)
        if not back:
            return {"ok": False, "error": "这件没有 AI 判定可回退"}
        note_obj["verdict"] = {"blocked": "blocked", "fine_screened": "passed",
                               "pending": "pending"}[back]
        c.execute("UPDATE v3_product SET note=?, status=?, updated_at=? WHERE product_id=?",
                  (st.j(note_obj), back, st.now(), pid))
        c.commit()
        return {"ok": True, "product_id": pid, "status": back, "ai_verdict": av}
    return _wrap(_do)


def v3_ocr_status(pack_code: str = "", pids: list | None = None) -> dict:
    """图片层自检：引擎在不在（缺什么）、已经读过多少张、多少张有命中。"""
    def _do(m, c):
        ids = [int(x) for x in pids] if pids else None
        if ids is None and pack_code:
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
        st = m["images"].ocr_available()
        try:
            st.update(m["images"].ocr_stats(c, ids))
        except Exception as e:         # noqa: BLE001
            st["stats_error"] = "%s: %s" % (type(e).__name__, e)
        return {"ok": True, **st}
    return _wrap(_do)


# ============================================================
#  九、详情细采回写（2026-10-22 新增）
# ============================================================
#  后端：taobao-picker\server\v3\detail_ingest.py
#  能力：对库里已有商品，用 fine_crawler 逐条打开详情页，把 stock / skus /
#        详情图 / 描述 / 属性 / 视频 补进 v3 库 —— 这是「粗采只有 14 键 payload
#        → 19 列有值」之间缺的那一环（第 3 步精筛只对库里已有的图做 OCR，不抓详情）。
#  边界：只做 Runtime.evaluate 取数 + 页面自身滚动，绝不 bringToFront / 激活窗口。

def _v3_detail_mod():
    """拿到 server.v3.detail_ingest。两条导入路径都试（v3 别名包 / server.v3 真包）。"""
    import importlib              # noqa: PLC0415
    errs = []
    for name in ("v3.detail_ingest", "server.v3.detail_ingest"):
        try:
            return importlib.import_module(name)
        except Exception as e:    # noqa: BLE001
            errs.append("%s → %s: %s" % (name, type(e).__name__, e))
    raise RuntimeError("detail_ingest 导不进来：" + "；".join(errs))


def v3_detail_ingest(pack_code: str = "", pids: list | None = None,
                     limit: int = 0) -> dict:
    r"""详情细采回写：按包或按 id 逐条补 库存 / SKU / 详情图 / 描述 / 属性 / 视频。

    返回 {"ok":..., "stats": {...}, "errors": [...], "items": [...], "notes": [...]}
    stats: products_total / fetched / failed / with_stock / with_sku /
           detail_images_added / sku_images_added / with_description / with_video

    ★ 阻塞调用（要连浏览器 9222、要等页面加载与滚动），必须放后台线程跑。
    ★ 幂等：重复跑不重复加图、不覆盖已有非空字段（标题/价格/已有 SKU 都不动）。
    """
    def _do(m, c):
        mod = _v3_detail_mod()
        ids = [int(x) for x in pids] if pids else None
        r = mod.ingest_pack(c, pack_code=pack_code, pids=ids, limit=int(limit or 0))
        if not isinstance(r, dict):
            return {"ok": False, "error": "detail_ingest 返回了非 dict：%r" % (type(r),)}
        return r
    return _wrap(_do)


# ============================================================
#  十、第 3 步精筛：开跑前仪表盘 / 实时事件流 / 每件图数 / 批量放行
#      （2026-10-22 新增，只追加，不改上面任何已有函数）
# ============================================================
#  背景：主人投诉「开跑后日志毫无反应」「不该有筛选上限」「跑完的提示指向已删除的
#        上货流水线页」「下拉里读不到数据包」。这一段是给界面用的接口层：
#    · v3_fine_preflight()   开跑前：引擎在不在、词表多少条、本包多少件、已读过几张图
#    · v3_image_ocr_counts() 每个商品 图数 / 已 OCR 数（表格那两列，不重跑 OCR）
#    · v3_fine_stream()      精筛 + 逐件事件回调（后端有 progress 就用，没有就退回老调用）
#    · v3_release_many()     批量放行（拦住的勾一批，状态改回 imported）
# ============================================================

def v3_fine_preflight(pack_code: str = "") -> dict:
    """精筛开跑前的仪表盘 —— 全是**静态自检**，不下载、不 OCR、不改库。

    界面用它把「引擎到底能不能用、词表几条、这包几件」先打在日志里，
    免得跑到一半才发现引擎不可用。
    """
    def _do(m, c):
        out: dict = {"ok": True, "pack_code": pack_code}
        ids: list[int] = []
        if pack_code:
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
        out["pids"] = len(ids)
        try:
            st = m["keywords"].stats(c)
            out["keyword_total"] = int(st.get("total") or 0)
            out["keyword_kinds"] = {k: v for k, v in st.items() if isinstance(v, dict)}
        except Exception as e:         # noqa: BLE001
            out["keyword_total"] = 0
            out["keyword_error"] = "%s: %s" % (type(e).__name__, e)
        try:
            ws = m["images"].build_words(c)
            out["ocr_words"] = int(getattr(ws, "size", 0) or 0)
        except Exception as e:         # noqa: BLE001
            out["ocr_words"] = 0
            out["ocr_words_error"] = "%s: %s" % (type(e).__name__, e)
        try:
            av = m["images"].ocr_available()
            out["engine_ok"] = bool(av.get("ok"))
            out["engine"] = av.get("engine") or ""
            out["engine_missing"] = av.get("missing") or []
        except Exception as e:         # noqa: BLE001
            out["engine_ok"] = False
            out["engine"] = ""
            out["engine_missing"] = ["%s: %s" % (type(e).__name__, e)]
        if ids:
            try:
                out.update(m["images"].ocr_stats(c, ids))
            except Exception as e:     # noqa: BLE001
                out["ocr_stats_error"] = "%s: %s" % (type(e).__name__, e)
        else:
            out.setdefault("ocr_images", 0)
            out.setdefault("hit_images", 0)

        # ---------------- 粗包门禁（2026-10-23 主人指令） ----------------
        # 第三步只吃「细包」：多张主图 + 详情图 + SKU 图。
        # 只有链接/标题/店铺、或只有一张主图的包，进来之后程序为了判证据只能
        # 一个个打开商品页去抓 —— 主人明确说这是违规的，本软件不做。
        if pack_code and ids:
            try:
                r = c.execute(
                    "SELECT COUNT(*) AS imgs,"
                    " SUM(CASE WHEN LOWER(i.role) IN ('detail','sku') THEN 1 ELSE 0 END) AS det,"
                    " COUNT(DISTINCT i.product_id) AS prods"
                    " FROM v3_image i WHERE i.product_id IN ("
                    "   SELECT product_id FROM v3_product WHERE pack_code = ?"
                    "   UNION SELECT product_id FROM v3_pack_member WHERE pack_code = ?)",
                    (pack_code, pack_code)).fetchone()
                imgs = int(r["imgs"] or 0)
                det = int(r["det"] or 0)
                out["images_total"] = imgs
                out["images_detail_sku"] = det
                avg = (imgs / max(len(ids), 1))
                out["images_per_product"] = round(avg, 2)
                if det == 0 and avg <= 1.2:
                    out["ok"] = False
                    out["coarse_pack"] = True
                    out["error"] = (
                        "这个包是「粗包」：%d 件商品共 %d 张图（每件约 %.1f 张），"
                        "详情图与 SKU 图都是 0。\n\n"
                        "第三步精筛要靠详情图与 SKU 图判证据；拿这种包进来，"
                        "程序只能一个个打开商品页去抓 —— 那是违规操作，本软件不做。\n\n"
                        "正确做法：这类包走「1 · 采集初筛 → 导入 URL 包初筛」只做第一步；"
                        "要做第三步，请用别的工具先解析出带详情图/SKU 图的细包再导入。"
                        % (len(ids), imgs, avg))
            except Exception as e:         # noqa: BLE001
                out["coarse_check_error"] = "%s: %s" % (type(e).__name__, e)
        out.pop("available", None)
        return out
    return _wrap(_do)


def v3_image_ocr_counts(pack_code: str = "", pids: list | None = None) -> dict:
    """每个商品的**图数 / 已 OCR 数 / 图片命中数** —— 表格「图 / OCR」列用。

    ★ 只读 v3_image + v3_image_ocr 的既有记录，**不下载、不 OCR**。
      所以「OCR 有没有跑」看得见，而不是靠猜。
    """
    def _do(m, c):
        ids = [int(x) for x in pids] if pids else None
        if ids is None:
            if not pack_code:
                return {"ok": False, "error": "必须指定数据包或商品 id"}
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
        out: dict[str, dict] = {}
        if not ids:
            return {"ok": True, "counts": out, "total": 0}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            sql = (
                "SELECT p.product_id AS pid,"
                " COUNT(DISTINCT i.image_id) AS imgs,"
                " COUNT(DISTINCT CASE WHEN o.state='ok' THEN o.image_id END) AS ocr_ok,"
                " COUNT(DISTINCT o.image_id) AS ocr_any,"
                " COUNT(DISTINCT CASE WHEN COALESCE(o.hit_words,'')<>''"
                "        THEN o.image_id END) AS ocr_hit"
                " FROM v3_product p"
                " LEFT JOIN v3_image i ON i.product_id=p.product_id"
                " LEFT JOIN v3_image_ocr o ON o.image_id=i.image_id"
                " WHERE p.product_id IN (%s) GROUP BY p.product_id" % marks)
            try:
                rows = list(c.execute(sql, chunk))
            except Exception:          # noqa: BLE001  v3_image_ocr 还没建表
                rows = list(c.execute(
                    "SELECT p.product_id AS pid, COUNT(DISTINCT i.image_id) AS imgs,"
                    " 0 AS ocr_ok, 0 AS ocr_any, 0 AS ocr_hit"
                    " FROM v3_product p LEFT JOIN v3_image i ON i.product_id=p.product_id"
                    " WHERE p.product_id IN (%s) GROUP BY p.product_id" % marks, chunk))
            for r in rows:
                out[str(int(r["pid"]))] = {"imgs": int(r["imgs"] or 0),
                                           "ocr": int(r["ocr_ok"] or 0),
                                           "ocr_any": int(r["ocr_any"] or 0),
                                           "hit": int(r["ocr_hit"] or 0)}
        return {"ok": True, "counts": out, "total": len(ids)}
    return _wrap(_do)


def v3_fine_stream(pack_code: str = "", pids: list | None = None,
                   with_images: bool = True, with_detail: bool = True,
                   limit: int = 0, on_event=None,
                   max_images_per_product: int = 0,
                   force_ocr: bool = False, with_ai: bool = True) -> dict:
    r"""精筛 + **逐件事件回调**（界面那个「实时滚动日志」就靠它）。

    事件 = 普通 dict，两种固定形状，其余字段原样透传：
        {"kind": "start", "n": 304, "limit": 0, "stream": True/False}
        {"kind": "done", "checked": .., "passed": .., "blocked": .., "log_path": "..."}
      中间每件一条（后端支持时），带 i / n / pid / title / images / downloaded /
      ocr / cached / hits / verdict / seconds 之类的键（键名可能不同，界面按别名取）。

    ★ 存在性兼容：后端 flow.fine_screen 有没有 `progress` 参数**现查现定**——
      有就传，没有就退回老调用（界面退回「等跑完一次性出结果」模式，不假装有进度）。
      TypeError（参数名对不上）也只降级，不让整批白跑。
    """
    import inspect                  # noqa: PLC0415

    def _emit(ev) -> None:
        if not callable(on_event):
            return
        try:
            on_event(ev if isinstance(ev, dict) else {"msg": str(ev)})
        except Exception:           # noqa: BLE001  界面的日志回调不许拖死后端
            pass

    def _do(m, c):
        ids = [int(x) for x in pids] if pids else None
        if ids is None:
            if not pack_code:
                return {"ok": False, "error": "必须指定数据包或商品 id（不允许默认全库跑）"}
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
            if not ids:
                return {"ok": False, "error": "包「%s」里没有商品" % pack_code}
        fs = m["flow"].fine_screen
        kw = dict(actor="上货台精筛", with_images=bool(with_images),
                  with_detail=bool(with_detail), limit=int(limit or 0),
                  max_images_per_product=int(max_images_per_product or 0),
                  force_ocr=bool(force_ocr), with_ai=bool(with_ai))
        supports = False
        try:
            params = inspect.signature(fs).parameters
            supports = ("progress" in params
                        or any(p.kind == p.VAR_KEYWORD for p in params.values()))
        except (TypeError, ValueError):
            supports = False
        _emit({"kind": "start", "n": len(ids), "limit": int(limit or 0),
               "stream": supports,
               "msg": "引擎%s逐件事件" % ("支持" if supports else "不支持")})
        if supports:
            try:
                r = fs(c, ids, progress=_emit, **kw)
            except TypeError as e:
                supports = False
                _emit({"kind": "warn",
                       "msg": "传 progress 调用失败（%s），退回无事件模式" % e})
                r = fs(c, ids, **kw)
        else:
            r = fs(c, ids, **kw)
        if isinstance(r, dict):
            r["stream"] = supports
            r["pack_code"] = pack_code
            r["pids"] = len(ids)
            _emit({"kind": "done", "checked": r.get("checked"),
                   "passed": r.get("passed"), "blocked": r.get("blocked"),
                   "remaining": r.get("remaining"),
                   "log_path": r.get("log_path") or ""})
        return r
    return _wrap(_do)


def v3_release_many(pids: list | None = None) -> dict:
    """批量放行：把拦住的商品状态改回 `imported`（= 可继续往下流转）。

    与单件 v3_release 同一套写法（`store.set_product_status(..., force=True)`），
    区别只是一次开一个连接、逐条改、最后统一 commit。
    """
    def _do(m, c):
        st = m["store"]
        done, fail = [], []
        for pid in [int(x) for x in (pids or [])]:
            try:
                if st.set_product_status(c, pid, "imported", force=True):
                    done.append(pid)
                else:
                    fail.append({"product_id": pid, "error": "状态没变（商品不存在？）"})
            except Exception as e:     # noqa: BLE001  单件失败不许拖死整批
                fail.append({"product_id": pid, "error": "%s: %s" % (type(e).__name__, e)})
        c.commit()
        return {"ok": True, "released": done, "failed": fail,
                "count": len(done), "note": "已放行，可继续流转"}
    return _wrap(_do)


# ---- 数据包传递（精筛 → 检测打码） ----------------------------------------
# 精筛跑完后把「通过的那批」存成一个数据包；存的是引用（v3_pack_member），
# 商品与图片一行都不复制 —— 同一张图不会下载两遍、打码两遍。
def v3_save_screen(pack_code: str = "", name: str = "", only: str = "keep",
                   pids: list | None = None) -> dict:
    """把精筛通过的这批商品存成数据包。

    pack_code  源包码（pids 为空时按它 + only 现取）
    name       包名后缀（缺省按口径取「精筛」或「全部」）
    only       keep = 没被拦下的（默认）| all = 源包全部
    pids       指定商品 id；给了就不再从源包取

    ★ 建成后**把源包的精筛完成状态继承给新包**（写进 fine_done.json，并记 source_pack），
      否则第 4 步的门禁会把「已经精筛过的通过件」当成没精筛过而拒之门外。
      继承的前提是源包确实有完成标记 —— 源包没跑过精筛，新包也不会凭空拿到状态。
    """
    r = _wrap(lambda m, c: m["pack"].save_members(
        c, name=name, pids=pids, source_pack=pack_code, only=only))
    try:
        if isinstance(r, dict) and r.get("ok") and r.get("pack_code"):
            src_at = fine_done_of(pack_code) if pack_code else ""
            if src_at:
                mark_fine_done(
                    r["pack_code"],
                    note="继承自 %s 的精筛完成（%s · 通过 %s 件）"
                         % (pack_code, src_at, r.get("count")),
                    source_pack=pack_code)
                r["fine_done_inherited_from"] = pack_code
    except Exception as e:  # noqa: BLE001
        r["fine_done_warn"] = "%s: %s" % (type(e).__name__, e)
    return r


def _inherit_fine_done(source_pack: str, new_pack: str, n: int) -> None:
    """把源包的精筛完成标记继承给筛选后包（只写 fine_done.json，不碰库连接）。"""
    try:
        src_at = fine_done_of(source_pack) if source_pack else ""
        if src_at and new_pack:
            mark_fine_done(
                new_pack,
                note="继承自 %s 的精筛完成（%s · 通过 %d 件）" % (source_pack, src_at, n),
                source_pack=source_pack)
    except Exception:            # noqa: BLE001
        pass


def v3_fine_release_pack(pack_code: str = "", name: str = "") -> dict:
    """精筛收口：把「通过件」落成 / 复用一个独立的筛选后数据包（引用式，不删任何商品）。

    ★ 2026-11-09 主人：「3.0 精筛之后加一个功能，把没通过的商品删掉，后续只该看到筛选后商品」。
      取舍 = 不静默硬删 223 件真实数据：原包与全部商品一行不动，只产出一个「只含通过件」的
      成员包；4.0 检测打码 / 出表默认选它 ⇒ 未通过的不再进后续环节，效果等价且随时可回滚。

    幂等：已存在「来源=本包 且 成员恰好=当前通过件集合」的筛选后包时直接复用，不重复造包。
    通过件口径 = v3_product.status='fine_screened'（与 flow.fine_screen 两档合成一致，
    也与 pack.KEEP_EXCLUDED 排除 blocked/rejected/pending 一致）；pending 不算通过。
    """
    code = (pack_code or "").strip()
    if not code:
        return {"ok": False, "error": "没有给源包码"}

    def _do(m, c):
        pack = m["pack"]
        # ① 源包全部商品（老包按 pack_code，成员包按成员表，两路 UNION 去重）
        src_ids = set(_pids_of(c, code))
        if not src_ids:
            return {"ok": False, "error": "源包 %s 里没有商品" % code}
        ph = ",".join("?" * len(src_ids))
        # ② 只留「通过件」
        passed = [int(r[0]) for r in c.execute(
            "SELECT product_id FROM v3_product WHERE product_id IN (%s)"
            " AND status='fine_screened' ORDER BY product_id" % ph, list(src_ids))]
        if not passed:
            return {"ok": False, "error":
                    "源包 %s 还没有任何通过件（先跑一次「▶ 筛选全部」）" % code}
        passed_set = set(passed)

        # ③ 幂等：找已存在的筛选后包（source_file='screen:<code>' 且成员集合正好相等）
        for r in c.execute(
                "SELECT pack_code FROM v3_pack WHERE source_file=?"
                " ORDER BY created_at DESC", ("screen:%s" % code,)):
            old = str(r[0])
            mem = {int(x[0]) for x in c.execute(
                "SELECT product_id FROM v3_pack_member WHERE pack_code=?", (old,))}
            if mem == passed_set:
                _inherit_fine_done(code, old, len(passed))
                return {"ok": True, "pack_code": old, "count": len(passed),
                        "reused": True, "source_pack": code,
                        "skipped": len(src_ids) - len(passed),
                        "message": "复用已存在的筛选后包「%s」（%d 件通过）"
                                   % (old, len(passed))}

        # ④ 没有就造一个（引用式：只写 v3_pack_member，不复制商品/图片、不删原包）
        # ★ 8.0 统一命名：筛选后包继承源包批次（如 11-13 第1批 · 筛选后），
        #   名字通过 pack.save_members 的 name 落到 source_name，再由下面的
        #   display_name 写成所有页面统一读的那一个字段。
        label = (name or "").strip() or packlabel.fine_label_of(c, code)
        rep = pack.save_members(c, name=label, pids=passed,
                                source_pack=code, only="keep")
        if not rep.get("ok"):
            return {"ok": False, "error": str(rep.get("error") or "save_members 没做成")}
        new_code = str(rep.get("pack_code") or "")
        # 只写展示层：已有名字（复用/用户改过）不动，新包写继承来的友好名
        try:
            c.execute("UPDATE v3_pack SET display_name=?"
                       " WHERE pack_code=? AND (display_name IS NULL OR display_name='')",
                       (label, new_code))
            c.commit()
        except Exception:                                # noqa: BLE001
            pass
        _inherit_fine_done(code, new_code, len(passed))
        return {"ok": True, "pack_code": new_code, "count": len(passed),
                "reused": False, "source_pack": code,
                "skipped": len(src_ids) - len(passed),
                "message": "已落成筛选后包「%s」（%d 件通过，跳过 %d 件未通过）"
                           % (new_code, len(passed), len(src_ids) - len(passed))}

    return _wrap(_do)


def v3_screen_pack_choices() -> dict:
    """给 4.0 / 出表下拉用：列出「筛选后包」及其来源包，并给出默认选中的那个。

    筛选后包 = v3_pack.source_file 以 'screen:' 开头、有成员、且成员里
    没有 blocked / pending / rejected 的包（全是通过件）。默认取最新的一个。
    返回 {ok, choices:[{pack_code, source_pack, count}], default}。
    """
    def _do(m, c):
        choices = []
        for r in c.execute(
                "SELECT pack_code, source_file FROM v3_pack"
                " WHERE source_file LIKE 'screen:%'"
                " ORDER BY COALESCE(converted_at, created_at) DESC, pack_code DESC"):
            code = str(r[0] or "")
            src = str(r[1] or "")[7:]
            if not code:
                continue
            rows = [int(x[0]) for x in c.execute(
                "SELECT m.product_id FROM v3_pack_member m"
                " JOIN v3_product p ON p.product_id=m.product_id"
                " WHERE m.pack_code=?", (code,))]
            if not rows:
                continue
            bad = c.execute(
                "SELECT COUNT(*) FROM v3_product WHERE product_id IN (%s)"
                " AND COALESCE(status,'') IN ('blocked','pending','rejected')"
                % ",".join("?" * len(rows)), rows).fetchone()[0]
            if int(bad or 0):
                continue
            choices.append({"pack_code": code, "source_pack": src,
                            "count": len(rows)})
        return {"ok": True, "choices": choices,
                # 与 v3_packs() 共用同一「最新」口径：有筛选后包就取它，
                # 一个都没有时退回全包里最新的那个（不再返回空串让页面乱选）。
                "default": (choices[0]["pack_code"] if choices else
                            packlabel.choose_default(v3_packs().get("items") or []))}
    return _wrap(_do)


# ============================================================
#  七·补、第 4 步瘦身：只处理第 3 步 OCR 判定命中的图
#  说明：第 3 步已经把每张图 OCR 过（v3_image_ocr），第 4 步再全量重扫一遍是浪费。
#        命中图仍要跑 det+rec（涂白要知道涂哪里，OCR 引擎删不掉），
#        已判干净的图直接标成「已处理·跳过」，引擎就不会挑到它们。
#        真正干活的仍然是 鉴图.py，一行未改。
# ============================================================
def _detect_slim():
    """拿到 v3.detect_slim 模块（纯函数，不碰 PyQt）。"""
    if not paths.ensure_backend_path():
        raise RuntimeError(f"找不到后端目录：{paths.TAOBAO_SERVER}")
    from v3 import detect_slim as _mod   # noqa: PLC0415
    return _mod


def detect_slim_plan(pack_code: str = "", pids: list | None = None,
                     limit: int = 0, recheck_words: bool = True) -> dict:
    """算「这一步该处理多少张」—— **只读**，不动任何库，可以随便调。

    `recheck_words=True`（默认）会用**当前合并词库**对第 3 步存下的 OCR 原文重新匹配一遍，
    避免「第 3 步当时的词库」与「现在引擎用的词库」不一致导致漏涂（实测这批数据偏差 11:6）。

    返回里 mode 三种：
        slim           = 只处理命中图（+ 未判定的），已判干净的走 skipped_clean
        fallback-full  = 本包没有第 3 步 OCR 记录 → 回落全量
        empty          = 这批商品没有图
    """
    def _do(m, c):
        ids = [int(x) for x in pids] if pids else None
        if ids is None:
            if not pack_code:
                return {"ok": False, "error": "必须指定数据包或商品 id"}
            ids = [int(x["product_id"]) for x in
                   m["store"].list_products(c, pack=pack_code, limit=100000)]
        mod = _detect_slim()
        words = None
        if recheck_words:
            try:
                words = mod.load_words(_detect_words_file())
            except Exception:        # noqa: BLE001  词库读不出来就退回只看旧判定
                words = None
        r = mod.plan(c, str(paths.PIG_DB), pids=ids, limit=limit, words=words)
        r["recheck_words"] = bool(words)
        r["pack_code"] = pack_code
        return r
    return _wrap(_do)


def run_local_detect_slim(pack_code: str = "", ids: list | None = None, limit: int = 0,
                          only_hit: bool = True, download: bool = True, dry: bool = False,
                          timeout: int = 3600, log=None, words: str = "",
                          on_plan=None, pids: list | None = None) -> dict:
    r"""第 4 步瘦身版：先算计划 → 标记已判干净 → 只下载/只涂白命中图 → 调 鉴图.py。

    · `ids`   是小猪的 goods_id（鉴图.py 吃的是这个），`pids` 是 v3 的 product_id（算计划用）；
      只给 `ids` 时会自动换算 —— 两者不是一套编号，混用会算错计划。
    · `only_hit=False` 或计划表明本包没跑过第 3 步 OCR → **回落全量**，行为与原来一致；
    · `download=True` 时只给「命中 + 未判定」的图补下载（干净的图一张都不下载）；
    · 返回里带 `slim` 一段：命中图 / 已判干净跳过 / 本次实际处理 / 下载计数 / 耗时。
    `on_plan(plan_dict)` 在真正开跑**之前**回调一次，界面据此先把三项数字写上去。
    """
    t0 = time.time()
    plan_ids = [int(x) for x in pids] if pids else None
    if plan_ids is None and ids and not pack_code:
        # ids 是小猪 goods_id：先换成 v3 product_id，否则计划会算成「商品不存在」
        conv = _wrap(lambda m, c: {"ok": True,
                                   "pids": _detect_slim().pids_for_goods(c, ids)})
        plan_ids = conv.get("pids") or []
    plan_r = detect_slim_plan(pack_code=pack_code, pids=plan_ids, limit=limit)
    if on_plan:
        try:
            on_plan(plan_r)
        except Exception:            # noqa: BLE001  界面回调不许拖死检测
            pass

    slim = {"mode": "plan-failed", "skipped_clean": 0, "already_clean": 0,
            "clean_unmatched": 0, "hit_images": 0, "unknown_images": 0,
            "will_process": 0, "images_total": 0, "downloaded": 0,
            "download_skipped": 0, "download_failed": 0, "plan_note": ""}

    if not plan_r.get("ok"):
        # 计划算不出来就退回老路：瘦身失败不许把第 4 步弄停
        r = run_local_detect(pack_code=pack_code, ids=ids, limit=limit, dry=dry,
                             timeout=timeout, log=log, words=words)
        slim["plan_note"] = str(plan_r.get("error") or "计划计算失败")
        r["slim"] = slim
        r["seconds"] = round(time.time() - t0, 1)
        return r

    mod = _detect_slim()
    mode = plan_r.get("mode")
    slim.update({"mode": mode, "hit_images": plan_r.get("hit_images", 0),
                 "unknown_images": plan_r.get("unknown_images", 0),
                 "will_process": plan_r.get("will_process", 0),
                 "images_total": plan_r.get("images_total", 0),
                 "plan_note": plan_r.get("note", "")})

    def _mark_engine_bug(res: dict) -> dict:
        """把「引擎自己崩了」这件事点名说清，而不是只丢一段 traceback 让人猜。"""
        tail = (res or {}).get("tail") or ""
        if "'list' object has no attribute 'words'" in tail or "args.words" in tail:
            res["engine_bug"] = ("鉴图.py 里 `Keywords(args.words)` 用的是 SQL 参数列表 args"
                                 "（应为 `a.words`）—— 一个字符的错，本次一张图都没处理")
            if log:
                try:
                    log("✗ 引擎自身报错：鉴图.py 第 317 行 `args.words` 应为 `a.words`"
                        "（args 是 SQL 参数列表）—— 修 1 个字符，检测才能往下跑")
                except Exception:    # noqa: BLE001
                    pass
        return res

    need_fallback = (mode == mod.MODE_FALLBACK) or (not only_hit) or (mode == mod.MODE_EMPTY)
    if need_fallback:
        if mode == mod.MODE_EMPTY:
            r = {"ok": True, "code": 0, "stat": {}, "stopped": False, "tail": ""}
        else:
            r = run_local_detect(pack_code=pack_code or "", ids=ids, limit=limit, dry=dry,
                                 timeout=timeout, log=log, words=words)
        _mark_engine_bug(r)
        r["slim"] = slim
        r["seconds"] = round(time.time() - t0, 1)
        return r

    # ---- 瘦身路径 ----
    pig_db = str(paths.PIG_DB)
    clean_urls = [x.get("url") for x in (plan_r.get("clean") or [])]
    try:
        mk = mod.mark_clean(pig_db, clean_urls)
        slim["skipped_clean"] = int(mk.get("marked") or 0)
        slim["already_clean"] = int(mk.get("already") or 0)
        slim["clean_unmatched"] = int(mk.get("missing") or 0)
    except Exception as e:           # noqa: BLE001  标记失败 → 照常处理命中图
        slim["plan_note"] += "｜跳过标记失败：%s" % e

    todo = (plan_r.get("hit") or []) + (plan_r.get("unknown") or [])
    goods = [str(g) for g in (plan_r.get("hit_products") or []) if g]
    if download and todo:
        try:
            dl = mod.download_missing(pig_db, [x.get("url") for x in todo],
                                      paths.PIG_READER / "data" / "img", log=log)
            slim["downloaded"] = int(dl.get("downloaded") or 0)
            slim["download_skipped"] = int(dl.get("skipped") or 0)
            slim["download_failed"] = int(dl.get("failed") or 0)
            for e in (dl.get("errors") or [])[:3]:
                if log:
                    try:
                        log("  ⚠ 下载：" + str(e)[:120])
                    except Exception:   # noqa: BLE001
                        pass
        except Exception as e:       # noqa: BLE001
            slim["plan_note"] += "｜下载失败：%s" % e

    if not goods:
        r = {"ok": True, "code": 0, "stat": {"ok": 0, "viol": 0, "clean": 0, "fail": 0, "up": 0},
             "stopped": False, "tail": ""}
        if log:
            try:
                log("第 3 步已判干净 %d 张 —— 没有命中图，本次不处理任何图"
                    % slim["skipped_clean"])
            except Exception:       # noqa: BLE001
                pass
        r["slim"] = slim
        r["seconds"] = round(time.time() - t0, 1)
        return r

    if log:
        try:
            log("瘦身模式：命中 %d 张 + 未判定 %d 张 = 处理 %d 张；已判干净 %d 张跳过"
                "（涉及 %d 件商品）"
                % (slim["hit_images"], slim["unknown_images"],
                   slim["hit_images"] + slim["unknown_images"],
                   slim["skipped_clean"], len(goods)))
        except Exception:           # noqa: BLE001
            pass

    r = run_local_detect(ids=goods, limit=limit, dry=dry, timeout=timeout,
                         log=log, words=words)
    _mark_engine_bug(r)
    r["slim"] = slim
    r["seconds"] = round(time.time() - t0, 1)
    return r


# ============================================================
#  一·补、URL 包（粗包）导入 → 初筛 → 导出
#  主人 2026-10-23：流程一也要能吃 URL 包；筛完剩下的要能导出；
#                 第三步只认细包，粗包必须拒（否则会去逐个开页面抓，违规）。
# ============================================================
def _urlpack():
    if not paths.ensure_backend_path():
        raise RuntimeError(f"找不到后端目录：{paths.TAOBAO_SERVER}")
    import url_pack as _m      # noqa: PLC0415
    return _m


def urlpack_classify(path: str) -> dict:
    """只读判包类型：detail = 细包（第三步能吃）· url = 粗包（第三步必须拒）。

    顺带报「来源」：wechat = 微信采集包 · taobao = 淘宝 URL 包 · unknown = 认不出。
    """
    try:
        m = _urlpack()
        rows, header = m.read_table(path)
        info = m.classify(rows, header)
        info["ok"] = True
        info["header"] = header
        info["file"] = str(path)
        return info
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "kind": "unknown", "error": f"{type(e).__name__}: {e}"}


def urlpack_run(path: str, keyword: str = "", use_ai: bool = False) -> dict:
    """跑初筛（prescreen + shop_kind，与第三步同一份规则表）。"""
    try:
        m = _urlpack()
        r = m.run(path, keyword=keyword, use_ai=bool(use_ai))
        return r
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def urlpack_save(result: dict) -> dict:
    """存成「采集初筛」的页签缓存，界面可回看。"""
    try:
        return _urlpack().save_session(result)
    except Exception as e:      # noqa: BLE001
        return {"id": "", "error": f"{type(e).__name__}: {e}"}


def urlpack_export(result: dict, out_dir: str = "", open_txt: bool = True) -> dict:
    """导出 通过 / 剔除 / 待复核 三份 CSV + 「可上货商品ID」TXT（默认生成完直接打开）。"""
    try:
        return _urlpack().export(result, out_dir=out_dir or "", open_txt=bool(open_txt))
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def urlpack_ids(result: dict) -> dict:
    """只算「可上货」那批的商品 ID（不落盘，界面预览用）。"""
    try:
        m = _urlpack()
        ids = m.keep_ids(result)
        return {"ok": True, "ids": ids, "count": len(ids)}
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "ids": [], "count": 0,
                "error": f"{type(e).__name__}: {e}"}


def urlpack_ids_export(results=None, result=None, out_dir: str = "",
                       tag: str = "", open_txt: bool = True) -> dict:
    """把「可上货商品ID」写成 TXT 并用记事本打开（可合并多个页签/多个包）。

    ★ 2026-10-26 主人要求：筛完要把所有商品 ID 合并在一起，用 TXT 打开。
      原来只有「导入 URL 包」那条路会出 TXT，「导出当前页签」那条不出。
    """
    try:
        return _urlpack().export_ids_only(results=results or [], result=result or {},
                                          out_dir=out_dir or "", tag=tag,
                                          open_txt=bool(open_txt))
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def open_path(p: str) -> dict:
    """用系统默认程序打开一个文件/文件夹（TXT → 记事本）。"""
    try:
        import os as _os
        p = str(p or "")
        if p and _os.path.exists(p):
            _os.startfile(p)                        # noqa: S606
            return {"ok": True, "path": p}
        return {"ok": False, "error": "路径不存在：%s" % p}
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================
#  一·补二、目录簿 —— 记住上次导入的文件夹（多个驱动共用同一份簿子）
# ============================================================
def dirbook_recent(kind: str = "pack", limit: int = 10) -> list[dict]:
    try:
        import dirbook       # noqa: PLC0415
        return dirbook.recent(kind, limit)
    except Exception:        # noqa: BLE001
        return []


def dirbook_start(kind: str = "pack", fallback: str = "") -> str:
    try:
        import dirbook       # noqa: PLC0415
        return dirbook.start_dir(kind, fallback)
    except Exception:        # noqa: BLE001
        return fallback


def dirbook_remember(path: str, kind: str = "pack") -> dict:
    try:
        import dirbook       # noqa: PLC0415
        dirbook.remember(path, kind)
        return {"ok": True, "recent": dirbook.recent(kind, 10)}
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================
#  4.1 · 主图 AI 重做（复用 taobao-picker\image_lab，不复制一行引擎代码）
#
#  为什么单开一页而不是塞进第 4 步：
#      第 4 步改的是「同一张图上违规字/字块」——产物仍是那张图的内容修补；
#      这一页改的是「整张主图重做」（换底/换景/重绘）——产物是**另一张候选图**。
#      两者的审计口径、失败回退、费用台账都不一样，混一页会把记录搅在一起。
#
#  三条硬约束（照 v3.5 任务书）：
#      ① 原图永不覆盖 —— AI 产物是 image_assets 里新的一条，is_original=0
#      ② 未确认不进发布 —— 出表读 v3_image.current_local_path，只有确认后才改它
#      ③ 每次替换都留旧值 —— 改指针前把旧路径写进 lab_writeback，可一键退回
# ============================================================
_LAB = {"mod": None, "err": "", "ready": False}


def _lab():
    """导入 image_lab（engine/providers/schema），失败把原因记下来给界面看。"""
    if _LAB["mod"] is not None:
        return _LAB["mod"]
    if _LAB["err"]:
        return None
    import importlib       # noqa: PLC0415
    import sys as _sys     # noqa: PLC0415
    base = str(paths.TAOBAO_DIR)
    # 必须排在 sys.path 最前：打包后 _internal 里也可能有一份 image_lab（为收依赖而打进去的），
    # 若用了那一份，schema.BASE 会指向 _internal、库变成空的新库 —— 悄悄丢数据那种错。
    try:
        while base in _sys.path:
            _sys.path.remove(base)
    except Exception:          # noqa: BLE001
        pass
    _sys.path.insert(0, base)
    for k in [k for k in list(_sys.modules) if k == "image_lab" or k.startswith("image_lab.")]:
        _sys.modules.pop(k, None)
    if not (paths.TAOBAO_DIR / "image_lab").is_dir():
        _LAB["err"] = "找不到 %s" % (paths.TAOBAO_DIR / "image_lab")
        return None
    try:
        eng = importlib.import_module("image_lab.engine")
        prov = importlib.import_module("image_lab.providers")
        sch = importlib.import_module("image_lab.schema")
        # 路径断言：库必须指向 taobao-picker\data\image_lab.db，不是别的空库
        want = os.path.normcase(str(paths.IMAGE_LAB_DB))
        got = os.path.normcase(str(sch.DB_PATH))
        if want != got:
            _LAB["err"] = "image_lab 库路径不对（实际 %s，应为 %s）" % (sch.DB_PATH,
                                                                      paths.IMAGE_LAB_DB)
            return None
        sch.init(sch.connect())            # 幂等建表，已有库不动
        _LAB["mod"] = {"engine": eng, "providers": prov, "schema": sch}
        _LAB["ready"] = True
        _ensure_wb_table(sch)
        return _LAB["mod"]
    except Exception as ex:    # noqa: BLE001
        _LAB["err"] = "%s: %s" % (type(ex).__name__, ex)
        return None


def _ensure_wb_table(sch) -> None:
    """替换台账（4.1 自有表，不动 v3 结构）：谁被换过、旧值是什么、退没退。

    ★ 2026-10-25 阶段二（第一批）：加 old_remote_url / new_remote_url 两列。
      版本管理的规矩是**记旧值、不动历史**：
        · v3_image.original_url 永远保留（原始版本可追溯）
        · 当前版本的线上地址存在 current_remote_url
        · 换成新文件时 current_remote_url 必须清空（新文件没有线上地址），
          但旧值先写进 old_remote_url —— 所以「清空」是移指针，不是删历史。
      老库用 ALTER 补列，幂等、可回滚（列留着不影响旧代码）。
    """
    try:
        c = sch.connect()
        c.execute(
            "CREATE TABLE IF NOT EXISTS lab_writeback ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " image_id INTEGER NOT NULL, product_id INTEGER, role TEXT, sequence INTEGER,"
            " old_path TEXT, new_path TEXT, asset_id TEXT, job_id TEXT,"
            " operator TEXT DEFAULT '上货台4.1', created_at TEXT, reverted_at TEXT)")
        have = {r[1] for r in c.execute("PRAGMA table_info(lab_writeback)")}
        for col in ("old_remote_url", "new_remote_url"):
            if col not in have:
                c.execute("ALTER TABLE lab_writeback ADD COLUMN %s TEXT" % col)
        c.execute("CREATE INDEX IF NOT EXISTS ix_wb_image ON lab_writeback(image_id)")
        c.commit()
        c.close()
    except Exception:          # noqa: BLE001
        pass


def lab_backend_error() -> str:
    """image_lab 到底能不能用 —— 不能就把真实原因给界面，不许只报一个空表。"""
    _lab()
    return _LAB["err"]


def _v3_ro():
    p = str(paths.TAOBAO_DIR / "data" / "v3.db")
    if not os.path.exists(p):
        return None
    c = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def lab_status() -> dict:
    """就绪度：模块 / 库 / 付费接口配置 / 已花费用 / 可用模式。"""
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载"}
    try:
        c = m["schema"].connect()
        try:
            prov = m["providers"].provider_status(c)
        except Exception as ex:   # noqa: BLE001
            prov = {"error": "%s: %s" % (type(ex).__name__, ex)}
        cost, jobs = 0.0, 0
        try:
            cost = float(c.execute(
                "SELECT COALESCE(SUM(cost_cny),0) FROM image_cost_ledger").fetchone()[0] or 0)
        except Exception:         # noqa: BLE001
            pass
        try:
            jobs = int(c.execute("SELECT COUNT(*) FROM image_jobs").fetchone()[0] or 0)
        except Exception:         # noqa: BLE001
            pass
        c.close()
        return {"ok": True, "providers": prov, "cost_total": round(cost, 4), "jobs": jobs,
                "db": m["schema"].DB_PATH, "modes": list(m["engine"].MODES),
                "policies": list(m["engine"].POLICIES)}
    except Exception as ex:       # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}


def lab_scopes(role: str = "main") -> dict:
    """范围选项：全部 + 各原包（按该用途媒体数降序）。实测新包可能一张主图都没有。

    ★ 2026-10-25：加 role 参数，让 4.1 能在「主图 / 详情图 / SKU 图 / 视频」之间切换；
      默认 'main' 保持老行为。
    """
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在", "items": []}
    try:
        items = []
        tot = c.execute("SELECT COUNT(*) FROM v3_image WHERE role=?", (role,)).fetchone()[0]
        loc = c.execute("SELECT COUNT(*) FROM v3_image WHERE role=? AND"
                        " current_local_path IS NOT NULL AND current_local_path<>''",
                        (role,)).fetchone()[0]
        label = {"main": "全部主图", "detail": "全部详情图", "sku": "全部 SKU 图",
                 "video": "全部视频"}.get(role, "全部 %s" % role)
        items.append({"scope": "all", "label": label, "n": int(tot), "local": int(loc)})
        for r in c.execute(
                "SELECT COALESCE(p.pack_code,'(无包)') pk, COUNT(*) n,"
                " SUM(CASE WHEN i.current_local_path IS NOT NULL AND i.current_local_path<>''"
                "     THEN 1 ELSE 0 END) loc"
                " FROM v3_image i LEFT JOIN v3_product p ON p.product_id=i.product_id"
                " WHERE i.role=? GROUP BY pk ORDER BY n DESC", (role,)):
            items.append({"scope": r["pk"], "label": r["pk"],
                          "n": int(r["n"] or 0), "local": int(r["loc"] or 0)})
        return {"ok": True, "items": items, "role": role}
    finally:
        c.close()


def lab_mains(scope: str = "all", only_local: bool = True, limit: int = 300,
              q: str = "", role: str = "main") -> dict:
    """列出某个用途的媒体（只读 v3.db）。exists=本地文件真的在，不是「有路径」就算。

    ★ 2026-10-25 阶段二（第三批）：加 role 参数。原来写死 role='main'，
      所以 4.1 里**只能改主图**，轮播/详情/SKU 图/视频在界面上根本选不到、
      也就改不了。默认仍是 'main'，老调用行为不变。
    """
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在", "rows": []}
    try:
        sql = ("SELECT i.image_id,i.product_id,i.role,i.sequence,i.sku_id,"
               " i.current_local_path,i.current_remote_url,i.status,"
               " p.original_title,p.pack_code,p.main_image_id"
               " FROM v3_image i LEFT JOIN v3_product p ON p.product_id=i.product_id"
               " WHERE i.role=?")
        args: list = [role]
        if scope and scope != "all":
            sql += " AND p.pack_code=?"
            args.append(scope)
        if only_local:
            sql += " AND i.current_local_path IS NOT NULL AND i.current_local_path<>''"
        if q:
            sql += " AND (p.original_title LIKE ? OR CAST(i.image_id AS TEXT) LIKE ?)"
            args += ["%" + q + "%", "%" + q + "%"]
        sql += " ORDER BY i.product_id, i.sequence LIMIT ?"
        args.append(int(limit))
        rows = []
        for r in c.execute(sql, args):
            d = dict(r)
            d["exists"] = bool(d.get("current_local_path")) and os.path.exists(
                d["current_local_path"])
            d["is_main"] = (d.get("main_image_id") == d["image_id"])
            rows.append(d)
        return {"ok": True, "rows": rows, "n": len(rows), "role": role,
                "local": sum(1 for x in rows if x["exists"])}
    except Exception as ex:       # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex), "rows": []}
    finally:
        c.close()


def lab_image(image_id: int) -> dict:
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在"}
    try:
        r = c.execute("SELECT i.*, p.original_title, p.pack_code FROM v3_image i"
                      " LEFT JOIN v3_product p ON p.product_id=i.product_id"
                      " WHERE i.image_id=?", (int(image_id),)).fetchone()
        if not r:
            return {"ok": False, "error": "没有 image_id=%s" % image_id}
        d = dict(r)
        d["ok"] = True
        d["exists"] = bool(d.get("current_local_path")) and os.path.exists(
            d["current_local_path"])
        return d
    finally:
        c.close()


def lab_modes() -> list[dict]:
    """给界面用的模式说明 —— 与 image_lab.engine.MODES 一一对应，不多不少。"""
    return [
        {"mode": "audit_only", "label": "只审计（不改图）", "paid": False,
         "why": "只做元素识别与差异核对，不产生新图，不花钱"},
        {"mode": "white_bg", "label": "白底化（抠主体铺白底）", "paid": True,
         "why": "本地优先；去背景差异最稳，风险最低"},
        {"mode": "remove_promo", "label": "去促销字与贴片", "paid": True,
         "why": "依赖元素识别坐标；识别不出就不动，不乱删"},
        {"mode": "replace_background", "label": "换背景（保商品本体）", "paid": True,
         "why": "只换背景，不改商品；需付费生成，先试 5 张"},
        {"mode": "clean_and_replace", "label": "清促销 + 换背景", "paid": True,
         "why": "两步叠加，费用最高；建议先分别验过再合并"},
        {"mode": "smart_auto", "label": "智能（按规则自己决定）", "paid": True,
         "why": "拿不准一律进人工复核；不会自作主张换景"},
    ]


def lab_preview(image_id: int, mode: str = "white_bg", policy: str = "auto",
                dry_run: bool = True, budget: float = 1.0,
                keep_identity: bool = True, keep_style: bool = True,
                preserve_text: bool = True) -> dict:
    """预演/真跑一张主图。dry_run=True 不发任何付费请求、不需要 key。"""
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载"}
    row = lab_image(image_id)
    if not row.get("ok"):
        return {"ok": False, "error": row.get("error") or "取图失败"}
    src = row.get("current_local_path") or ""
    if not src or not os.path.exists(src):
        return {"ok": False,
                "error": "这张主图没有本地文件（第 2 步没下载媒体）——先下载再来做"}
    try:
        j = m["engine"].create_job(
            source_path=src, product_id=row.get("product_id"), image_id=int(image_id),
            image_role="main", mode=mode, provider_policy=policy,
            output_count=1, budget_cny=float(budget),
            keep_product_identity=bool(keep_identity),
            keep_original_style=bool(keep_style),
            preserve_verified_text=bool(preserve_text))
        if not j.get("ok"):
            return {"ok": False, "error": "建任务失败", "raw": j}
        jid = j["job_id"]
        res = m["engine"].run_job(jid, dry_run=bool(dry_run))
        job = m["engine"].get_job(jid) or {}
        return {"ok": True, "job_id": jid, "dry_run": bool(dry_run),
                "status": job.get("status"), "cost": job.get("cost_cny"),
                "quality": job.get("quality"), "requires_review": job.get("requires_review"),
                "review_reason": job.get("review_reason"), "error": job.get("error"),
                "result_asset_id": job.get("result_asset_id"),
                "assets": [a for a in (job.get("assets") or []) if not a.get("is_original")],
                "steps": job.get("steps") or [], "raw_result": res}
    except Exception as ex:       # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}


def lab_confirm(image_id: int, asset_id: str, job_id: str = "") -> dict:
    """把候选图确认为这张主图的当前版本。

    顺序：校验候选 → 记旧值 → 改 v3 指针 → 写台账（失败则把指针改回去）。
    """
    import hashlib        # noqa: PLC0415
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载"}
    try:
        lc = m["schema"].connect()
        lc.row_factory = sqlite3.Row
        a = lc.execute("SELECT * FROM image_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if not a:
            lc.close()
            return {"ok": False, "error": "找不到候选资产 %s" % asset_id}
        a = dict(a)
        lc.close()
        if int(a.get("image_id") or 0) != int(image_id):
            return {"ok": False, "error": "这个候选不属于 image_id=%s（属于 %s）"
                    % (image_id, a.get("image_id"))}
        if a.get("is_original"):
            return {"ok": False, "error": "这是原图记录，不能当新版本确认"}
        new = a.get("path") or ""
        if not new or not os.path.exists(new):
            return {"ok": False, "error": "候选图文件不存在：%s" % new}
        try:
            h = hashlib.sha256(open(new, "rb").read()).hexdigest()
            if a.get("sha256") and h != a["sha256"]:
                return {"ok": False, "error": "候选图被改过（sha256 不符），拒绝确认"}
        except Exception:         # noqa: BLE001
            pass

        cur = lab_image(image_id)
        if not cur.get("ok"):
            return {"ok": False, "error": cur.get("error") or "取当前图失败"}
        old = cur.get("current_local_path") or ""
        if old == new:
            return {"ok": False, "error": "候选与当前版本是同一个文件，无需替换"}
        old_remote = (cur.get("current_remote_url") or "").strip()

        p = str(paths.TAOBAO_DIR / "data" / "v3.db")
        c = sqlite3.connect(p, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            # ★ 2026-10-25 阶段二：换成新文件的同时必须把当前版本的线上地址清空
            #   —— 新文件还没上传，那个旧 URL 指向的是旧图。旧值已记进台账
            #   （old_remote_url），original_url 一个字节不动，所以历史没丢。
            c.execute("UPDATE v3_image SET current_local_path=?, current_remote_url=NULL,"
                      " status='replaced', updated_at=? WHERE image_id=?",
                      (new, paths.now_str(), int(image_id)))
            c.commit()
        except Exception:
            c.rollback()
            c.close()
            raise
        c.close()

        row = {"ok": True, "image_id": int(image_id), "old": old, "new": new,
               "old_remote_url": old_remote,
               "asset_id": asset_id, "job_id": job_id, "role": cur.get("role"),
               "sequence": cur.get("sequence"),
               "need_upload": True}
        try:
            lc = m["schema"].connect()
            cur2 = lc.execute("INSERT INTO lab_writeback(image_id,product_id,role,sequence,"
                              " old_path,new_path,asset_id,job_id,created_at,"
                              " old_remote_url,new_remote_url)"
                              " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (int(image_id), cur.get("product_id"), cur.get("role"),
                               cur.get("sequence"), old, new, asset_id, job_id,
                               paths.now_str(), old_remote, None))
            wb_id = cur2.lastrowid
            lc.commit()
            lc.close()
            row["wb_id"] = wb_id
        except Exception as ex:    # noqa: BLE001
            # 台账写不进去就把指针改回去 —— 宁可这次白做，也不留无记录的替换
            c = sqlite3.connect(p, timeout=15)
            try:
                c.execute("UPDATE v3_image SET current_local_path=?, updated_at=?"
                          " WHERE image_id=?", (old, paths.now_str(), int(image_id)))
                c.commit()
            finally:
                c.close()
            return {"ok": False, "error": "写替换台账失败，已回滚指针：%s: %s"
                    % (type(ex).__name__, ex)}
        return row
    except Exception as ex:        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}


def lab_revert(wb_id: int) -> dict:
    """按台账退回上一版（只改指针，文件一个都不删）。"""
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载"}
    try:
        lc = m["schema"].connect()
        lc.row_factory = sqlite3.Row
        w = lc.execute("SELECT * FROM lab_writeback WHERE id=?", (int(wb_id),)).fetchone()
        lc.close()
        if not w:
            return {"ok": False, "error": "没有台账 id=%s" % wb_id}
        w = dict(w)
        if w.get("reverted_at"):
            return {"ok": False, "error": "这条已经在 %s 退回过" % w["reverted_at"]}
        old = w.get("old_path") or ""
        if not old or not os.path.exists(old):
            return {"ok": False, "error": "旧文件已不在：%s" % old}
        old_remote = (w.get("old_remote_url") or "").strip()
        p = str(paths.TAOBAO_DIR / "data" / "v3.db")
        c = sqlite3.connect(p, timeout=15)
        try:
            # ★ 2026-10-25 阶段二：退回时把当时的线上地址一并还原，否则退回原图后
            #   current_remote_url 还是空的，导出会把这张原图判成「待上传」。
            c.execute("UPDATE v3_image SET current_local_path=?, current_remote_url=?,"
                      " status='original', updated_at=? WHERE image_id=?",
                      (old, old_remote or None, paths.now_str(), int(w["image_id"])))
            c.commit()
        finally:
            c.close()
        lc = m["schema"].connect()
        lc.execute("UPDATE lab_writeback SET reverted_at=? WHERE id=?",
                   (paths.now_str(), int(wb_id)))
        lc.commit()
        lc.close()
        return {"ok": True, "image_id": w["image_id"], "restored": old,
                "restored_remote_url": old_remote}
    except Exception as ex:        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}


def lab_writebacks(image_id: int = 0, limit: int = 100) -> dict:
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载", "rows": []}
    try:
        lc = m["schema"].connect()
        lc.row_factory = sqlite3.Row
        if image_id:
            rows = [dict(x) for x in lc.execute(
                "SELECT * FROM lab_writeback WHERE image_id=? ORDER BY id DESC LIMIT ?",
                (int(image_id), int(limit)))]
        else:
            rows = [dict(x) for x in lc.execute(
                "SELECT * FROM lab_writeback ORDER BY id DESC LIMIT ?", (int(limit),))]
        lc.close()
        return {"ok": True, "rows": rows, "n": len(rows)}
    except Exception as ex:        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex), "rows": []}


def lab_cost() -> dict:
    """费用台账：成功与失败都记，便于对账（image_lab 的 image_cost_ledger）。"""
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载", "rows": []}
    try:
        lc = m["schema"].connect()
        lc.row_factory = sqlite3.Row
        rows = [dict(x) for x in lc.execute(
            "SELECT * FROM image_cost_ledger ORDER BY id DESC LIMIT 50")]
        tot = float(lc.execute(
            "SELECT COALESCE(SUM(cost_cny),0) FROM image_cost_ledger").fetchone()[0] or 0)
        lc.close()
        return {"ok": True, "rows": rows, "total": round(tot, 4)}
    except Exception as ex:        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex), "rows": []}


def lab_asset(asset_id: str) -> dict:
    """取一条候选资产（给界面显示新图、确认前核对路径与大小）。"""
    m = _lab()
    if not m:
        return {"ok": False, "error": _LAB["err"] or "image_lab 模块没加载"}
    try:
        lc = m["schema"].connect()
        lc.row_factory = sqlite3.Row
        a = lc.execute("SELECT * FROM image_assets WHERE asset_id=?", (asset_id,)).fetchone()
        lc.close()
        if not a:
            return {"ok": False, "error": "没有 asset_id=%s" % asset_id}
        d = dict(a)
        d["ok"] = True
        d["exists"] = bool(d.get("path")) and os.path.exists(d["path"])
        return d
    except Exception as ex:        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}


# ============================================================
#  2026-10-24 · 商品卡文件夹导入 / 媒体下载 / 视频补链
#  为什么单开一组：老的 v3_pack_convert 是「一个文件一个包」，
#  而主人的口径是「一个文件夹一个包」。这组函数不动老路径，只加新入口。
# ============================================================
def v3_cards_import(folder: str, pack_name: str = "", download: bool = True,
                    ocr: bool = True, workers: int = 4) -> dict:
    """选一个文件夹 → 一个数据包 → 立刻下载（图+视频）→ 下载成功的图立刻 OCR。

    来源自动识别（微店/淘宝/拼多多），认不出来就整份拒收并说明看到哪些键。
    """
    def _do(m, c):
        mod = m.get("cards")
        if not mod:
            return {"ok": False, "error": "后端 cards 模块没加载（检查 server\\v3\\cards.py）"}
        return mod.import_folder(folder, pack_name=pack_name, download=download,
                                 ocr=ocr, workers=workers)
    return _wrap(_do)


def v3_media_download(pack_code: str = "", pids: list | None = None,
                      roles: list | None = None, workers: int = 4,
                      ocr_after: bool = True) -> dict:
    """按包或按商品批量下载媒体。roles=None 全部；['video'] 只下视频。"""
    def _do(m, c):
        mod = m.get("media")
        if not mod:
            return {"ok": False, "error": "后端 media 模块没加载（检查 server\\v3\\media.py）"}
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            ids = [int(r[0]) for r in c.execute(
                "SELECT product_id FROM v3_product WHERE pack_code=?", (pack_code,))]
        if not ids:
            return {"ok": False, "error": "没指定商品，包里也没有商品"}
        st = mod.run(c, ids, roles=roles, workers=workers, ocr_after=ocr_after)
        return {"ok": True, "products": len(ids), **st}
    return _wrap(_do)


def v3_media_stats(pack_code: str = "", pids: list | None = None) -> dict:
    """给界面看：引用数 / 唯一 URL / 已下载 / 失败 / 按角色分布。"""
    def _do(m, c):
        mod = m.get("media")
        if not mod:
            return {"ok": False, "error": "后端 media 模块没加载"}
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            ids = [int(r[0]) for r in c.execute(
                "SELECT product_id FROM v3_product WHERE pack_code=?", (pack_code,))]
        if not ids:
            return {"ok": False, "error": "没有商品"}
        return {"ok": True, "products": len(ids),
                "stats": mod.stats(c, ids), "dedupe": mod.dedupe_report(c, ids)}
    return _wrap(_do)


def v3_video_update(product_id: int, urls: list | None = None,
                    card_path: str = "", download: bool = True) -> dict:
    """视频链接过期后换新地址：可直接给 urls，也可给"重新采集到的那张卡"的路径。

    设计口径：卡路径 → 自动抽视频地址（不用手工复制几十个 URL）→ 换地址 → 立刻下载。
    """
    def _do(m, c):
        media_mod = m.get("media")
        cards_mod = m.get("cards")
        if not media_mod:
            return {"ok": False, "error": "后端 media 模块没加载"}
        u = [x for x in (urls or []) if isinstance(x, str) and x.strip()]
        got = {}
        if not u and card_path:
            if not cards_mod:
                return {"ok": False, "error": "后端 cards 模块没加载，读不了卡"}
            got = cards_mod.fetch_video_urls(card_path)
            if not got.get("ok"):
                return {"ok": False, "error": got.get("error") or "读卡失败"}
            u = got.get("urls") or []
        if not u:
            return {"ok": False, "error": "没有拿到新视频地址（urls 空、卡里也没有视频）"}
        r = media_mod.update_video_urls(c, int(product_id), u, download=download)
        r["from_card"] = got or None
        return r
    return _wrap(_do)

# ============================================================
#  2026-10-24 · 下载任务中心（P0-1）
#  为什么单开一组：老 v3_media_download 是"一次调用跑完才返回"，
#  界面只能等结果、看不见单张图进度、中途停不下来。任务书要求任务中心：
#  每文件一行、真实字节/速度/ETA、可暂停可继续可停止、重启可恢复。
#  这一组只做"建任务 / 起线程 / 读快照 / 控制"，下载逻辑在 server\v3\dlcenter.py。
# ============================================================
def _pids_of(c, pack_code: str = "", pids=None) -> list:
    """商品范围：显式 pids 优先；否则按包取（★ pack_code 可能为空，
    真实归属只在 v3_pack_member —— 只按 pack_code 会查成 0 件，这是实测过的坑）。"""
    ids = [int(x) for x in (pids or [])]
    if ids:
        return ids
    code = (pack_code or "").strip()
    if not code:
        return []
    out = {int(r[0]) for r in c.execute(
        "SELECT product_id FROM v3_product WHERE pack_code=?", (code,))}
    try:
        # ★ 2026-11-09 修：真库 v3_pack / v3_pack_member 都没有 pack_id 列，
        #   原来那句 JOIN ... ON p.pack_id=m.pack_id 必抛 OperationalError，
        #   又被 except 吞掉 ⇒ 成员包（精筛后落的那批）永远取到 0 件，
        #   4.0 检测打码/出表于是回落去读未筛原包。按 pack_code 直接取，口径与
        #   pack._pick_pids / pack.members 一致；历史「以商品表分组为主」的修复不动。
        out |= {int(r[0]) for r in c.execute(
            "SELECT product_id FROM v3_pack_member WHERE pack_code=?", (code,))}
    except Exception:            # noqa: BLE001
        pass
    return sorted(out)


def v3_dl_start(pack_code: str = "", pids: list | None = None, roles: list | None = None,
                workers: int = 4, ocr: bool = True, title: str = "",
                skip_done: bool = True, ocr_only: bool = False) -> dict:
    """建一个下载任务并立刻起后台线程。返回 job_id（界面拿它开任务窗口）。"""
    def _do(m, c):
        mod = m.get("dlcenter")
        if not mod:
            return {"ok": False, "error": "后端 dlcenter 模块没加载（检查 server\\v3\\dlcenter.py）"}
        ids = _pids_of(c, pack_code, pids)
        if not ids:
            return {"ok": False, "error": "没有商品可选：先选一个包，或先勾选几件"}
        r = mod.create_job(c, ids, roles=roles, workers=workers, title=title,
                           pack_code=pack_code, ocr=ocr, skip_done=skip_done,
                           ocr_only=ocr_only)
        if not r.get("ok"):
            return r
        s = mod.start(mod.db_path_of(c), r["job_id"], conn=c)
        r.update({k: v for k, v in s.items() if k not in ("ok",)})
        r["ok"] = True
        return r
    return _wrap(_do)


def v3_dl_snapshot(job_id: int = 0, task_limit: int = 600, log_limit: int = 200,
                   f_pid: int = 0, f_role: str = "", f_state: str = "", q: str = "") -> dict:
    """界面读快照：头部统计 + 任务表 + 日志。只读库，不参与下载。"""
    def _do(m, c):
        mod = m.get("dlcenter")
        if not mod:
            return {"ok": False, "error": "后端 dlcenter 模块没加载"}
        return mod.snapshot(c, job_id=job_id, task_limit=task_limit, log_limit=log_limit,
                            f_pid=f_pid, f_role=f_role, f_state=f_state, q=q)
    return _wrap(_do)


def v3_dl_pause(job_id: int) -> dict:
    def _do(m, c):
        return m["dlcenter"].pause(c, int(job_id))
    return _wrap(_do)


def v3_dl_resume(job_id: int) -> dict:
    def _do(m, c):
        return m["dlcenter"].resume(c, int(job_id))
    return _wrap(_do)


def v3_dl_stop(job_id: int) -> dict:
    def _do(m, c):
        return m["dlcenter"].stop(c, int(job_id))
    return _wrap(_do)


def v3_dl_retry_failed(job_id: int) -> dict:
    """把失败项放回队列并继续（不重复插入媒体/OCR 记录 —— 只是状态复位）。"""
    def _do(m, c):
        mod = m["dlcenter"]
        n = c.execute("UPDATE v3_dl_task SET state=?, tries=0, error='', updated_at=?"
                      " WHERE job_id=? AND state IN (?,?)",
                      (mod.T_WAIT, __import__("v3.store", fromlist=["x"]).now(),
                       int(job_id), mod.T_FAIL, mod.T_CANCEL)).rowcount
        c.commit()
        out = mod.resume(c, int(job_id))
        out["requeued"] = n
        return out
    return _wrap(_do)


def v3_dl_jobs(limit: int = 20) -> dict:
    def _do(m, c):
        return {"ok": True, "jobs": m["dlcenter"].jobs(c, limit)}
    return _wrap(_do)


def v3_dl_recover() -> dict:
    """程序启动时调一次：把上次没跑完的任务收成"已暂停"，可一键继续。"""
    def _do(m, c):
        mod = m["dlcenter"]
        return mod.recover(mod.db_path_of(c))
    return _wrap(_do)


def v3_dl_task_file(task_id: int) -> dict:
    def _do(m, c):
        return m["dlcenter"].task_file(c, int(task_id))
    return _wrap(_do)

# ============================================================
# 4 · 按坐标消除（2026-10-24 新增）
#   第 4 步原来只会「贴色块」（均值色 / 纯白）。实测 8 张真实命中图：
#   贴白块的平均色差 107.9，消除（inpaint）20.96，自动择优选 17.34。
#   这里只做「按第 3 步已存坐标出产物」——不覆盖原图、不传图床、不回写 URL。
# ============================================================
def _mask_target_ids(c, m, pack_code="", pids=None, only_hit=True, limit=0):
    where = ["i.dl_state='ok'", "i.role<>'video'"]
    args = []
    if pids:
        ids = [int(x) for x in pids]
        where.append("i.product_id IN (%s)" % ",".join("?" * len(ids)))
        args.extend(ids)
    elif pack_code:
        # ★ 2026-11-10 修「产物 0」：下拉里的精筛后成员包只在 v3_pack_member 有商品，
        #   v3_product.pack_code 里一行都没有 —— 原裸子查询取到 0 件，消除跑完 0 张。
        #   与图片墙 detect_wall 统一走 _pids_of（同时解 product.pack_code + pack_member）。
        ids = _pids_of(c, pack_code)
        if not ids:
            return []
        where.append("i.product_id IN (%s)" % ",".join("?" * len(ids)))
        args.extend(ids)
    else:
        raise ValueError("必须指定数据包或商品 id（不允许默认全库跑）")
    if only_hit:
        where.append("EXISTS (SELECT 1 FROM v3_image_ocr o WHERE o.image_id=i.image_id"
                     " AND o.hit_words IS NOT NULL AND trim(o.hit_words)<>'')")
    # ★ 2026-11-04：已经覆盖过的图不再进队列。
    #   覆盖后 current_local_path 指向 <id>_masked.jpg —— 重跑会拿**打码图**当输入、
    #   按原坐标再消一遍，产生二次伪影。用文件名后缀判，不依赖目录名。
    where.append("(i.current_local_path IS NULL"
                 " OR instr(i.current_local_path,'_masked.')=0)")
    sql = "SELECT i.image_id FROM v3_image i WHERE " + " AND ".join(where) + " ORDER BY i.image_id"
    got = [int(r[0]) for r in c.execute(sql, args)]
    if limit:
        got = got[:int(limit)]
    return got


def v3_mask_plan(pack_code: str = "", pids: list | None = None,
                  only_hit: bool = True) -> dict:
    """消除前盘点：多少张图要补、其中多少张已有坐标（纯只读）。"""
    def _do(m, c):
        mod = m.get("mask")
        if mod is None:
            return {"ok": False, "error": "后端缺 v3.mask 模块"}
        ids = _mask_target_ids(c, m, pack_code, pids, only_hit)
        with_boxes = no_boxes = 0
        for iid in ids:
            bo = m["images"].boxes_of(c, iid) if m.get("images") else None
            if bo:
                with_boxes += 1
            else:
                no_boxes += 1
        return {"ok": True, "images": len(ids), "with_boxes": with_boxes,
                "no_boxes": no_boxes, "only_hit": bool(only_hit),
                "fills": ["lama 消除（推荐·与 4.1 同一套）", "auto（自动择优）",
                          "inpaint 消除", "mean 均值色", "white 纯白"]}
    return _wrap(_do)


def _mask_ledger(c, results, pack_code: str = "", actor: str = "4.0") -> dict:
    """把消除产物登记进 `v3_image_edit`（status='draft'，不动版本链）。

    ★ 2026-11-03 为什么加这一条：4.0 原来只把图写到 `data\\v3\\masked\\<id>_masked.jpg`，
    库里一个字都不记 —— 于是 4.1 人工审核看不到这批产物，出表也只认 `v3_image` 的旧指针，
    等于「打码白打」。这里按 image_id 维护一条 draft 台账（有则更新、无则新增），
    把**产物路径 / 算法 / 半径 / 命中框 / 词**一起落库，4.1 与出表都能按库读，不靠文件名猜。

    只写 `v3_image_edit`，**不建版本行、不改 current_local_path、不动原图**。
    """
    import json as _json            # noqa: PLC0415
    wrote = upd = 0
    try:
        has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                        " AND name='v3_image_edit'").fetchone()
        if not has:
            return {"ok": False, "error": "库里没有 v3_image_edit 表（先跑一次 4.1 或 edits.ensure_table）"}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for x in (results or []):
            if not x.get("masked"):
                continue
            iid = int(x.get("image_id") or 0)
            dst = x.get("out_path") or ""
            if not iid or not dst:
                continue
            try:
                if not os.path.isfile(dst):
                    continue
            except Exception:      # noqa: BLE001
                continue
            pid = c.execute("SELECT product_id FROM v3_image WHERE image_id=?",
                            (iid,)).fetchone()
            pid = int(pid[0]) if pid else 0
            regions = [{"kind": "rect", "points": r} for r in (x.get("rects") or [])]
            note = "4.0 自动消除 · %s · 命中词 %s" % (
                x.get("picked") or x.get("fill") or "", "、".join(x.get("hit_texts") or [])[:200])
            row = c.execute("SELECT edit_id FROM v3_image_edit WHERE image_id=?"
                            " AND actor=? AND status='draft'", (iid, actor)).fetchone()
            if row:
                c.execute("UPDATE v3_image_edit SET mask_path=?, regions_json=?, method=?,"
                          " radius=?, pack_code=?, note=? WHERE edit_id=?",
                          (dst, _json.dumps(regions, ensure_ascii=False),
                           str(x.get("picked") or x.get("fill") or ""),
                           int(x.get("radius") or 0), pack_code, note, int(row[0])))
                upd += 1
            else:
                c.execute("INSERT INTO v3_image_edit"
                          " (image_id, product_id, pack_code, mask_path, regions_json, method,"
                          "  radius, quality, status, note, actor, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (iid, pid, pack_code, dst,
                           _json.dumps(regions, ensure_ascii=False),
                           str(x.get("picked") or x.get("fill") or ""),
                           int(x.get("radius") or 0), "review", "draft", note, actor, now))
                wrote += 1
        c.commit()
        return {"ok": True, "inserted": wrote, "updated": upd}
    except Exception as e:         # noqa: BLE001
        try:
            c.rollback()
        except Exception:          # noqa: BLE001
            pass
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _mask_to_versions(c, m, results, pack_code: str = "", actor: str = "4.0",
                      overwrite: bool = True) -> dict:
    r"""把消除产物接上发布链路。**两种模式，默认单套数据**。

    `overwrite=True`（默认 · 2026-11-04 主人口径「不要生成两套数据」）：
      产物路径**直接写进原图那一行**的 `current_local_path`；原值搬进
      `original_local_path`（只在它为空时写，保住最初那张）；清空 `current_remote_url`
      （强制重新传图床）；status='replaced'。**不新增版本行** ——
      出表链路 `genplan.py` 读的正是 `current_local_path`，所以写完立刻生效。

    `overwrite=False`（旧行为）：建 candidate 版本，等 4.1 点「确认合格」。
      保留它是因为 4.1 的人工审核仍要「候选→确认」这条路；
      但**批量消除不该走它** —— 2026-11-03 建的 15926/15927 两个候选
      至今 `confirmed_at` 为空，说明这条路在批量场景下等于白做。

    幂等：当前路径已经是这张产物 → 跳过，不二次覆盖（避免把已消干净的字再糊一遍）。
    """
    images = m.get("images")
    store = m.get("store")
    if images is None or store is None:
        return {"ok": False, "error": "后端缺 v3.images / v3.store 模块"}

    if overwrite:
        applied = skip = failed = 0
        errs: list[str] = []
        for x in (results or []):
            if not x.get("masked"):
                continue
            iid = int(x.get("image_id") or 0)
            dst = x.get("out_path") or ""
            if not iid or not dst or not os.path.isfile(dst):
                skip += 1
                continue
            try:
                row = c.execute(
                    "SELECT product_id, role, current_local_path,"
                    " original_local_path FROM v3_image WHERE image_id=?",
                    (iid,)).fetchone()
                if not row:
                    skip += 1
                    continue
                old_cur = (row["current_local_path"] or "").strip()
                if old_cur and os.path.normcase(old_cur) == os.path.normcase(dst):
                    skip += 1                  # 已经是这张产物
                    continue
                # 与 images.approve_media 同一套语句：先记旧 → 再换新 → 清线上地址
                c.execute(
                    "UPDATE v3_image SET"
                    " original_local_path=CASE WHEN COALESCE(original_local_path,'')=''"
                    "   THEN current_local_path ELSE original_local_path END,"
                    " current_local_path=?, current_remote_url=NULL,"
                    " status='replaced', updated_at=? WHERE image_id=?",
                    (dst, store.now(), iid))
                cur = c.execute(
                    "SELECT edit_id FROM v3_image_edit WHERE image_id=?"
                    " AND actor=? ORDER BY edit_id DESC LIMIT 1",
                    (iid, actor)).fetchone()
                if cur:
                    c.execute(
                        "UPDATE v3_image_edit SET mask_path=?, status='applied',"
                        " confirmed_at=?,"
                        " note=COALESCE(note,'') || ' · 已直接覆盖当前版本'"
                        " WHERE edit_id=?", (dst, store.now(), int(cur["edit_id"])))
                else:
                    c.execute(
                        "INSERT INTO v3_image_edit (image_id, product_id, pack_code,"
                        " mask_path, method, status, note, actor, created_at, confirmed_at)"
                        " VALUES (?,?,?,?,?,'applied',?,?,?,?)",
                        (iid, int(row["product_id"]), pack_code, dst,
                         str(x.get("picked") or x.get("fill") or ""),
                         "4.0 自动消除 → 已覆盖当前版本", actor, store.now(), store.now()))
                applied += 1
            except Exception as e:             # noqa: BLE001
                failed += 1
                errs.append("%s: %s" % (type(e).__name__, e))
        c.commit()
        return {"ok": True, "mode": "overwrite", "applied": applied,
                "skipped": skip, "failed": failed, "errors": errs[:5],
                "need_upload": applied,
                "note": "产物已直接替换为当前版本（原图留底 original_local_path）；"
                        "线上地址已清空，出表前要重新上传图床"}

    made = existing = skip = failed = 0
    errs = []
    for x in (results or []):
        if not x.get("masked"):
            continue
        iid = int(x.get("image_id") or 0)
        dst = x.get("out_path") or ""
        if not iid or not dst or not os.path.isfile(dst):
            skip += 1
            continue
        row = c.execute("SELECT product_id, role FROM v3_image WHERE image_id=?",
                        (iid,)).fetchone()
        if not row:
            skip += 1
            continue
        pid = int(row["product_id"])
        role = (row["role"] or "main")
        cur = c.execute("SELECT edit_id, version_image_id FROM v3_image_edit"
                        " WHERE image_id=? AND actor=? ORDER BY edit_id DESC LIMIT 1",
                        (iid, actor)).fetchone()
        vid = int(cur["version_image_id"] or 0) if cur else 0
        try:
            if not vid:
                r = images.import_new(c, pid, dst, note="4.0 自动消除 → 候选版本",
                                      role=role, actor=actor, keep_status=True)
                if r.get("ok"):
                    vid = int(r["image_id"])
                    made += 1
                elif r.get("duplicate_of"):
                    vid = int(r["duplicate_of"])
                    existing += 1
                else:
                    failed += 1
                    errs.append(str(r.get("error") or "import_new 失败")[:100])
                    continue
                c.execute("UPDATE v3_image SET parent_image_id=?, updated_at=?"
                          " WHERE image_id=?", (iid, store.now(), vid))
            if cur:
                c.execute("UPDATE v3_image_edit SET version_image_id=?, status='saved'"
                          " WHERE edit_id=?", (vid, int(cur["edit_id"])))
            else:
                c.execute("INSERT INTO v3_image_edit (image_id, product_id, version_image_id,"
                          " pack_code, mask_path, method, status, note, actor, created_at)"
                          " VALUES (?,?,?,?,?,?,'saved',?,?,?)",
                          (iid, pid, vid, pack_code, dst, str(x.get("fill") or ""),
                           "4.0 自动消除（无 draft 台账，补建）", actor, store.now()))
        except Exception as e:     # noqa: BLE001
            failed += 1
            errs.append("%s: %s" % (type(e).__name__, e))
    c.commit()
    return {"ok": True, "mode": "candidate", "versions_new": made,
            "versions_existing": existing, "skipped": skip, "failed": failed,
            "errors": errs[:5], "need_upload": made,
            "note": "产物已进候选版本链；到 4.1 点「确认合格」才会成为发布图"}


def _lama_ipc_install(mod) -> dict:
    """把 4.0 的 LaMa 推理接到**子进程**上（app/lama_ipc.py，与 4.1 同一套）。

    为什么必须走子进程（两条都是现场证据，不是推测）：
      ① 进程内 InferenceSession 把 GIL 长占 → 界面卡死；
      ② 2026-10-27 现场：1440×1440 连跑第 7 张时进程被系统直接终止，
         Python 的 try/except 抓不到，日志停在框换算那行（原生 OOM）。
    默认开启；`SHT_LAMA_IPC=0` 时回退进程内（排查用）。任何一步失败都只是回退，不阻断。
    """
    try:
        from . import lama_ipc                     # noqa: PLC0415
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "error": "取不到 lama_ipc：%s: %s" % (type(e).__name__, e)}
    if not lama_ipc.enabled():
        return {"ok": False, "mode": "inproc", "error": "SHT_LAMA_IPC=0 已关闭子进程模式"}
    if mod is None or not hasattr(mod, "set_inpaint_impl"):
        return {"ok": False, "mode": "inproc", "error": "后端 mask 模块没有注入点（版本过旧）"}
    try:
        mod.set_inpaint_impl(lama_ipc.inpaint)
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "mode": "inproc", "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "mode": "ipc"}


def mask_lama_status() -> dict:
    """只读：子进程在不在、模型热没热、最近一次错误。**不会启动子进程**。"""
    out: dict = {"ok": True}
    try:
        from . import lama_ipc                     # noqa: PLC0415
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    p = getattr(lama_ipc, "_PROC", None)
    running = p is not None and p.poll() is None
    st = dict(getattr(lama_ipc, "STATE", {}) or {})
    out.update({"enabled": lama_ipc.enabled(), "running": running,
                "warm": bool(st.get("warm")), "pid": st.get("pid") or 0,
                "last_error": str(st.get("last_error") or "")[:200],
                "mode": "ipc" if lama_ipc.enabled() else "inproc"})
    if running:
        try:
            out["ping"] = lama_ipc.available().get("ping") or {}
        except Exception:                          # noqa: BLE001
            pass
    return out


def mask_warmup() -> dict:
    """在**子进程**里加载 LaMa 模型（首次约 28 秒）。界面全程可操作，不卡。

    为什么要有它：4.0 以前第一张消除要干等 28 秒，人以为卡死了。
    """
    def _do(m, c):
        try:
            from . import lama_ipc                 # noqa: PLC0415
        except Exception as e:                      # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        _lama_ipc_install(m.get("mask"))
        r = lama_ipc.warmup()
        return {"ok": bool(r.get("ok")), "warmup": r}
    return _wrap(_do)


def v3_mask_apply(pack_code: str = "", pids: list | None = None,
                   fill: str = "lama", method: str = "telea", radius: int = 3,
                   only_hit: bool = True, limit: int = 0, log=None,
                   write_ledger: bool = True, region: str = "adapt",
                   to_version: bool = True, overwrite: bool = True) -> dict:
    r"""按已存坐标出图（默认 lama：LaMa 生成式消除，与 4.1 人工审核同一套）。

    产物写到 `data\v3\masked\<image_id>_masked.jpg`，随后**直接覆盖当前版本**
    （`overwrite=True`，默认）：路径写进原行的 `current_local_path`、原值留底到
    `original_local_path`、清空 `current_remote_url` —— 出表/上架立刻就是打码图，
    **库里只有一套数据**。要回到「建候选版本、等 4.1 点确认」的老路，传 `overwrite=False`。
    `write_ledger=True`（默认）把产物登记进 `v3_image_edit`（status 由 overwrite 决定：
    覆盖=applied，候选=saved）。

    ★ 2026-11-03 `region` 默认 **adapt**（core 框按字形外扩、夹在 unclip 框内）：
    实测 `tight`（引擎 core 框）只有字形中间那一横条高，按它消完**字的上半截和下半截还在**；
    `loose`（unclip 8~12 倍面积）又会**把隔壁一行字一起抹掉**（#9487 实测抹掉了「让出行更有秩序」）。
    `adapt` 上下各外扩 55% 框高、左右 8% 框宽，实测整字消除干净、邻居零改动、框外 0 像素变化。
    """
    def _do(m, c):
        mod = m.get("mask")
        if mod is None:
            return {"ok": False, "error": "后端缺 v3.mask 模块"}
        _lama_ipc_install(mod)
        ids = _mask_target_ids(c, m, pack_code, pids, only_hit)
        if limit:
            ids = ids[:int(limit)]
        if not ids:
            return {"ok": True, "total": 0, "note": "没有需要消除的图（%s）"
                    % ("只处理命中图" if only_hit else "全量")}
        r = mod.apply(c, ids, fill=fill, method=method, radius=int(radius),
                      out_dir=mod.mask_dir(), log=log, region=region)
        r["fill"] = fill
        r["method"] = method
        r["radius"] = int(radius)
        r["out_dir"] = mod.mask_dir()
        r["region"] = region
        r["picked"] = {}
        for x in (r.get("results") or []):
            k = x.get("picked") or ""
            if k:
                r["picked"][k] = r["picked"].get(k, 0) + 1
        if write_ledger:
            r["ledger"] = _mask_ledger(c, r.get("results") or [], pack_code=pack_code)
            if to_version:
                r["versions"] = _mask_to_versions(c, m, r.get("results") or [],
                                                  pack_code=pack_code,
                                                  overwrite=overwrite)
        return r
    return _wrap(_do)


def v3_mask_one(image_id: int, fill: str = "lama", method: str = "telea",
                radius: int = 3, pack_code: str = "", write_ledger: bool = True,
                region: str = "adapt", to_version: bool = True,
                overwrite: bool = True, force: bool = False) -> dict:
    """只消除**这一张**（4.0 右侧工作台的「消除这一张」）。产物与台账口径同 v3_mask_apply：
    `overwrite=True`（默认）= 直接覆盖当前版本，库里只留一套。

    `force=False`（默认）时，当前版本已经是打码产物就跳过 —— 否则会拿打码图当输入、
    按原坐标再消一遍，字周围被二次糊掉。真要重打传 `force=True`。
    """
    def _do(m, c):
        mod = m.get("mask")
        if mod is None:
            return {"ok": False, "error": "后端缺 v3.mask 模块"}
        if not force:
            row = c.execute("SELECT current_local_path FROM v3_image WHERE image_id=?",
                            (int(image_id),)).fetchone()
            p = (row["current_local_path"] or "") if row else ""
            if "_masked." in p:
                return {"ok": True, "fill": fill, "already": True,
                        "one": {"image_id": int(image_id), "masked": False,
                                "note": "这张已经是打码后的图，跳过（要重打传 force=True）"}}
        _lama_ipc_install(mod)
        r = mod.mask_image(c, int(image_id), fill=fill, method=method,
                           radius=int(radius), out_dir=mod.mask_dir(), region=region)
        out = {"ok": bool(r.get("ok")), "one": r, "fill": fill}
        if r.get("ok") and r.get("masked") and write_ledger:
            out["ledger"] = _mask_ledger(c, [r], pack_code=pack_code)
            if to_version:
                out["versions"] = _mask_to_versions(c, m, [r], pack_code=pack_code,
                                                    overwrite=overwrite)
        return out
    return _wrap(_do)


# ============================================================
# 4.1 · 主图审核进度（2026-10-24 新增）—— 2.5 与 4.1 共用一份口径
# ============================================================
def v3_review_progress(pack_code: str = "") -> dict:
    """主图审核进度：审核口径 = v3_image_edit 里该图有一条 status='confirmed'。

    「已确认原图」与「修补后确认」都算审过（4.1 两条路都写这一张表）。
    只读，不写库。
    """
    def _do(m, c):
        where = ["i.role='main'"]
        args = []
        if pack_code:
            where.append("i.product_id IN (SELECT product_id FROM v3_product WHERE pack_code=?)")
            args.append(pack_code)
        w = " AND ".join(where)
        total = c.execute("SELECT count(*) FROM v3_image i WHERE " + w, args).fetchone()[0]
        done = c.execute(
            "SELECT count(DISTINCT i.image_id) FROM v3_image i"
            " WHERE " + w + " AND EXISTS (SELECT 1 FROM v3_image_edit e"
            "   WHERE e.image_id=i.image_id AND e.status='confirmed')", args).fetchone()[0]
        saved = c.execute(
            "SELECT count(*) FROM v3_image_edit e"
            " WHERE e.status IN ('saved','confirmed') AND e.method IS NOT NULL"
            "   AND e.method<>'' AND e.version_image_id IS NOT NULL").fetchone()[0]
        drafts = c.execute("SELECT count(*) FROM v3_image_edit WHERE status='draft'").fetchone()[0]
        last = c.execute("SELECT max(confirmed_at) FROM v3_image_edit"
                         " WHERE status='confirmed'").fetchone()[0]
        pending = max(0, int(total) - int(done))
        return {"ok": True, "pack_code": pack_code,
                "main_total": int(total), "reviewed": int(done), "pending": pending,
                "pct": (round(done * 100.0 / total, 1) if total else 0.0),
                "versions_saved": int(saved), "drafts": int(drafts),
                "last_confirmed_at": last or "",
                "note": ("主图 %d 张 · 已审 %d · 待审 %d" % (total, done, pending))
                        if total else "本包还没有主图"}
    return _wrap(_do)


# ============================================================
# D · 属性/规格补齐（2026-10-24 新增，见 server\v3\enrich.py）
# ============================================================
def v3_enrich_apply(pids: list | None = None) -> dict:
    def _do(m, c):
        mod = m.get("enrich")
        if mod is None:
            return {"ok": False, "error": "后端缺 v3.enrich 模块"}
        return mod.apply(c, pids)
    return _wrap(_do)


def v3_enrich_summary() -> dict:
    def _do(m, c):
        mod = m.get("enrich")
        if mod is None:
            return {"ok": False, "error": "后端缺 v3.enrich 模块"}
        return dict(mod.summary(c), ok=True)
    return _wrap(_do)


# ============================================================
#  十一、4.0 检测打码 · 图片墙（2026-10-26 追加；上面任何函数一行未改）
#
#  为什么新开一组：4.0 原来整套读的是**小猪的 商品.db**（包内商品 + 图片），
#  用「包码」和「图片 url」对齐；而 2.5 / 3.0 的商品与媒体活在 **v3.db**
#  （v3_product / v3_image / v3_image_ocr）。两个库的包码不是一套编号，
#  于是「2.5 有 576 张图的包，4.0 里一张都读不到 → 违规 0」。
#  这一组直接按 v3.db 取图，和小猪库零依赖；小猪库只用来说明「引擎能不能碰它」。
# ============================================================
def detect_mask_dir() -> str:
    """按坐标消除的产物目录（与 v3.mask.masked_path 同一个落点）。"""
    return str(paths.TAOBAO_DIR / "data" / "v3" / "masked")


def _masked_of(image_id: int) -> str:
    import os as _os               # noqa: PLC0415
    p = _os.path.join(detect_mask_dir(), "%d_masked.jpg" % int(image_id))
    return p if _os.path.exists(p) else ""


def _fine_block_n(note: str) -> int:
    """note 里精筛被拦下的条数（block 级理由几条）。读不出来就当 0。"""
    if not note:
        return 0
    try:
        d = json.loads(note)
    except Exception:              # noqa: BLE001
        return 0
    return sum(1 for x in (d.get("fine_screen") or []) if x.get("level") == "block")


def detect_align(pack_code: str = "") -> dict:
    """这个包在 **v3.db 有图** vs **小猪 商品.db 对得上** 各多少 —— 决定引擎能不能碰它。

    鉴图.py 按 小猪库 的 goods_id 取图；对不上就是「跑完 0 张」。这条数字必须让用户看见，
    否则界面只会显示「违规 0」而看不出是「一张都没参与」。
    """
    def _do0():
        c = _v3_ro()
        if c is None:
            return {"ok": False, "error": "v3.db 不存在"}
        try:
            pids = _pids_of(c, pack_code)
            if not pids:
                return {"ok": True, "products": 0, "v3_images": 0,
                        "pig_images": 0, "pig_products": 0, "pig_pack_rows": 0,
                        "note": "这个包在 v3.db 里没有商品"}
            ph = ",".join("?" * len(pids))
            n_img = c.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)" % ph,
                              pids).fetchone()[0]
            goods = [str(r[0] or "") for r in c.execute(
                "SELECT source_product_id FROM v3_product WHERE product_id IN (%s)" % ph, pids)]
        finally:
            c.close()
        goods = [g for g in goods if g]
        pig_img = pig_prod = pig_pack = 0
        if paths.PIG_DB.exists() and goods:
            con = sqlite3.connect("file:%s?mode=ro" % str(paths.PIG_DB).replace("\\", "/"),
                                  uri=True, timeout=5)
            try:
                mq = ",".join("?" * len(goods))
                pig_img = con.execute("SELECT COUNT(*) FROM 图片 WHERE goods_id IN (%s)" % mq,
                                      goods).fetchone()[0]
                pig_prod = con.execute("SELECT COUNT(*) FROM 商品 WHERE goods_id IN (%s)" % mq,
                                       goods).fetchone()[0]
                pig_pack = con.execute("SELECT COUNT(*) FROM 包内商品 WHERE 包码=?",
                                       (pack_code,)).fetchone()[0]
            except sqlite3.Error:
                pass
            finally:
                con.close()
        ok_engine = bool(pig_img)
        note = ("引擎可跑：小猪库能对上 %d 张图 / %d 件商品" % (pig_img, pig_prod)
                if ok_engine else
                "⚠ 小猪 商品.db 里对不上这个包（goods %d 件 · 图 %d 张 · 包内商品 %d 行）——"
                " 鉴图.py 按小猪库的 goods_id 取图，所以「本机引擎检测」会跑完 0 张、"
                "违规显示 0；那不是打码结论，是一张图都没参与。" % (pig_prod, pig_img, pig_pack))
        return {"ok": True, "products": len(pids), "v3_images": int(n_img),
                "pig_images": int(pig_img), "pig_products": int(pig_prod),
                "pig_pack_rows": int(pig_pack), "engine_can_run": ok_engine, "note": note}
    try:
        return _do0()
    except Exception as e:         # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def detect_wall(pack_code: str = "", role: str = "main", limit: int = 2000) -> dict:
    """图片墙数据：**一张图一条**，全部字段都在这里，界面不再自己查库。

    数据源只有 v3.db（2.5 商品库）。本地文件、OCR 判定、消除产物三样分别给状态，
    所以「未检测 / 干净 / 命中 N 处 / 已涂白 / 待下载」是**算出来的**，不是猜的。
    """
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在", "items": []}
    try:
        pids = _pids_of(c, pack_code)
        if not pids:
            return {"ok": True, "items": [], "total": 0, "products": 0,
                    "note": "这个包在 v3.db 里没有商品"}
        ph = ",".join("?" * len(pids))
        where = ["i.product_id IN (%s)" % ph]
        args: list = list(pids)
        if role:
            where.append("i.role=?")
            args.append(role)
        try:
            has_edit = bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                                      " AND name='v3_image_edit'").fetchone())
        except Exception:          # noqa: BLE001
            has_edit = False
        sql = ("SELECT i.image_id, i.product_id, i.role, i.sequence, i.original_url,"
               "       i.current_local_path, i.current_remote_url, COALESCE(i.dl_state,'') st,"
               "       COALESCE(p.original_title,'') title, COALESCE(p.status,'') pst,"
               "       COALESCE(p.note,'') pnote,"
               "       COALESCE(o.state,'') ocr_state, COALESCE(o.hit_words,'') hw,"
               "       COALESCE(o.seconds,0) secs,"
               "       COALESCE(ed.mask_path,'') mpath, COALESCE(ed.method,'') mmethod"
               "  FROM v3_image i"
               "  LEFT JOIN v3_product p ON p.product_id=i.product_id"
               "  LEFT JOIN v3_image_ocr o ON o.image_id=i.image_id"
               + ("  LEFT JOIN (SELECT image_id, max(edit_id) eid FROM v3_image_edit"
                  " WHERE actor='4.0' GROUP BY image_id) em ON em.image_id=i.image_id"
                  "  LEFT JOIN v3_image_edit ed ON ed.edit_id=em.eid" if has_edit else "") +
               " WHERE " + " AND ".join(where) +
               " ORDER BY i.product_id, i.sequence, i.image_id LIMIT ?")
        args.append(int(limit or 2000))
        rows = [dict(r) for r in c.execute(sql, args)]
    except Exception as e:         # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "items": []}
    finally:
        try:
            c.close()
        except Exception:          # noqa: BLE001
            pass

    block_cache: dict[int, int] = {}
    items: list[dict] = []
    for r in rows:
        iid = int(r["image_id"])
        pid = int(r["product_id"])
        if pid not in block_cache:
            block_cache[pid] = _fine_block_n(r["pnote"])
        lp = (r["current_local_path"] or "").strip()
        local_ok = bool(lp) and os.path.exists(lp)
        # ★ 2026-11-03：先认库里的台账（v3_image_edit.mask_path），磁盘兜底 —— 
        #   这样「已消除」是**查出来的**，不是「碰巧有这个文件名」。
        mpath = (r["mpath"] or "").strip()
        if mpath and not os.path.exists(mpath):
            mpath = ""
        masked = mpath or _masked_of(iid)
        hw = [x.strip() for x in (r["hw"] or "").replace("，", ",").split(",") if x.strip()]
        ostate = (r["ocr_state"] or "").strip()
        is_video = (r["role"] or "") == "video"
        if is_video:
            code, text = "video", "视频（不做文字检测）"
        elif not local_ok:
            code, text = "missing", "待下载（本地没有这张图）"
        elif masked:
            code, text = "masked", "已消除%s%s" % (
                ("（命中 %d 处）" % len(hw)) if hw else "",
                ("· %s" % r["mmethod"]) if r["mmethod"] else "")
        elif not ostate:
            code, text = "no_ocr", "未检测（没有 OCR 记录）"
        elif ostate != "ok":
            code, text = "ocr_fail", "检测失败（OCR state=%s）" % ostate
        elif hw:
            code, text = "hit", "已检测 · 命中 %d 处" % len(hw)
        else:
            code, text = "clean", "已检测 · 干净"
        items.append({
            "image_id": iid, "product_id": pid, "role": r["role"] or "",
            "sequence": r["sequence"] or 0,
            "title": r["title"] or ("商品 %s" % pid),
            "url": r["original_url"] or "",
            "local_path": lp if local_ok else "",
            "local_exists": local_ok,
            "masked_path": masked,
            "mask_method": r["mmethod"] or "",
            "mask_in_db": bool(r["mpath"]),
            "remote_url": r["current_remote_url"] or "",
            "dl_state": r["st"] or "",
            "ocr_state": ostate,
            "hit_words": hw, "hit_n": len(hw),
            "seconds": float(r["secs"] or 0),
            "product_status": r["pst"] or "",
            "fine_block": block_cache[pid],
            "status_code": code, "status_text": text,
        })
    s = _summarize(items)
    s.update({"ok": True, "items": items, "role": role, "pack_code": pack_code})
    return s


def _summarize(items: list[dict]) -> dict:
    from collections import Counter      # noqa: PLC0415
    by = Counter(x["status_code"] for x in items)
    prods = len({x["product_id"] for x in items})
    return {"total": len(items), "products": prods, "by_status": dict(by),
            "local_ok": sum(1 for x in items if x["local_exists"]),
            "missing": by.get("missing", 0),
            "masked": by.get("masked", 0),
            "hit": by.get("hit", 0),
            "clean": by.get("clean", 0),
            "no_ocr": by.get("no_ocr", 0),
            "ocr_fail": by.get("ocr_fail", 0),
            "video": by.get("video", 0)}


def detect_summary(pack_code: str = "", role: str = "main") -> dict:
    """只算计数（不返回 items），刷新状态行用。"""
    r = detect_wall(pack_code, role=role, limit=200000)
    if not r.get("ok"):
        return r
    out = {k: v for k, v in r.items() if k not in ("items",)}
    return out


def detect_ocr_measure(pack_code: str = "", role: str = "", top: int = 12) -> dict:
    """用**真正的**词表匹配器复核这个包：真命中 / 仅碎片命中 / 有字未匹配 / 无文字 / 无记录。

    ★ 为什么必须用 v3.words.WordSet 而不是子串：WordSet 对纯 ASCII 词要求整词边界；
      `detect_slim.classify()` 的复核用的是 `w in text` 子串 —— 实测同一个包
      子串给出 298 张命中，真匹配器只有 175 张真命中 + 11 张仅碎片，
      **虚高 123 张**，多出来的就是 GW/MI/TS/TION/ION 这种被 OCR 切断的碎片。
    """
    from collections import Counter      # noqa: PLC0415
    c = _v3_ro()
    if c is None:
        return {"ok": False, "error": "v3.db 不存在"}
    try:
        pids = _pids_of(c, pack_code)
        if not pids:
            return {"ok": True, "total": 0, "note": "这个包在 v3.db 里没有商品"}
        ph = ",".join("?" * len(pids))
        where = ["i.product_id IN (%s)" % ph]
        args: list = list(pids)
        if role:
            where.append("i.role=?")
            args.append(role)
        rows = [dict(r) for r in c.execute(
            "SELECT i.image_id, i.role, COALESCE(o.state,'') st,"
            "       COALESCE(o.hit_words,'') hw, COALESCE(o.full_text,'') ft,"
            "       COALESCE(o.seconds,0) secs"
            "  FROM v3_image i LEFT JOIN v3_image_ocr o ON o.image_id=i.image_id"
            " WHERE " + " AND ".join(where), args)]
        m = _v3()
        wmod = (m or {}).get("words")
        ws = wmod.build(c) if wmod else None
    except Exception as e:         # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        try:
            c.close()
        except Exception:          # noqa: BLE001
            pass

    by = Counter()
    real_words: Counter = Counter()
    frag_words: Counter = Counter()
    secs = 0.0
    for r in rows:
        st = (r["st"] or "").strip()
        ft = r["ft"] or ""
        old = [x.strip() for x in (r["hw"] or "").replace("，", ",").split(",") if x.strip()]
        got = [x[0] for x in ws.match(ft)] if (ws and ft) else []
        if not got and old:
            got = old
        real = [w for w in got if not (w.isascii() and len(w) <= 4)]
        frag = [w for w in got if (w.isascii() and len(w) <= 4)]
        secs += float(r["secs"] or 0)
        if not st:
            by["无OCR记录"] += 1
        elif st != "ok":
            by["OCR失败"] += 1
        elif not ft.strip():
            by["OCR有记录但无文字"] += 1
        elif real:
            by["真命中"] += 1
            for w in real:
                real_words[w] += 1
        elif frag:
            by["仅碎片命中"] += 1
            for w in frag:
                frag_words[w] += 1
        else:
            by["有字但词表未匹配"] += 1
    total = sum(by.values())
    return {"ok": True, "total": total, "by": dict(by),
            "hit": by["真命中"] + by["仅碎片命中"], "real_hit": by["真命中"],
            "frag_hit": by["仅碎片命中"],
            "no_text": by["OCR有记录但无文字"], "no_ocr": by["无OCR记录"],
            "ocr_fail": by["OCR失败"], "unmatched": by["有字但词表未匹配"],
            "miss_total": (by["OCR有记录但无文字"] + by["无OCR记录"]
                           + by["OCR失败"] + by["有字但词表未匹配"]),
            "words_n": (ws.size if ws else 0),
            "ocr_seconds": round(secs, 1),
            "top_real": real_words.most_common(top),
            "top_frag": frag_words.most_common(top)}


def detect_fill_missing(pack_code: str = "", pids: list | None = None,
                        role: str = "main") -> dict:
    """**只补下缺的**：调 2.5 的 media.run（它自己跳过已有本地文件的图），不重下整批。

    界面上的按钮不是「下载全部首图」，这就是它唯一的用途 —— 单件或补缺。
    """
    def _do(m, c):
        mod = m.get("media")
        if mod is None:
            return {"ok": False, "error": "后端 media 模块没加载"}
        ids = _pids_of(c, pack_code, pids)
        if not ids:
            return {"ok": False, "error": "没有商品可选"}
        st = mod.run(c, ids, roles=[role], workers=4, ocr_after=False)
        return {"ok": True, "products": len(ids), "role": role,
                "entries": st.get("entries"), "todo": st.get("todo"),
                "downloaded": st.get("downloaded"), "failed": st.get("failed"),
                "unique_urls": st.get("unique_urls"),
                "message": "补缺 %s 件（%s）：要下 %s 个 URL · 成功 %s · 失败 %s"
                           "（本地已有的一律跳过，不重下）"
                           % (len(ids), role, st.get("todo"),
                              st.get("downloaded"), st.get("failed"))}
    return _wrap(_do)


# ============================================================
# E · 下载记录删除（2026-10-26 新增 · 2.5.1 下载监控页）
#   主人口径：「所有的信息都要能可以自己控制处理」。
#
#   删的只是 v3_dl_task 的**记录行**：
#     · 磁盘上的图片/视频文件一个不动（主人要删的是记录，不是已下载的图）
#     · v3_dl_log 不动（那是时间线，留着才能对上刚才发生过什么）
#     · job 本身不删（清空后仍能看到这个任务的头，只是行数归零）
#
#   任务真在跑（job.state = running / stopping）时，进行中的行一律跳过：
#   下载线程下一轮还会 `UPDATE v3_dl_task ... WHERE task_id=?`，
#   行被删了那边就是空写，容易写出一堆对不上的孤儿状态。
#   任务已暂停/已停止时则可以随便删 —— 那时没有线程在写行。
# ============================================================
DL_TASK_LIVE = ("waiting", "downloading", "verifying", "retry_wait")
DL_TASK_FINISHED = ("done", "failed", "cancelled", "skipped")
_DL_RUNNING_JOB = ("running", "stopping")


def _dl_running_jobs(c, job_ids) -> set:
    """这些 job 里，哪些还在跑（只有它们的进行中行需要保护）。"""
    ids = []
    for x in (job_ids or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    out = set()
    for r in c.execute("SELECT job_id, state FROM v3_dl_job WHERE job_id IN (%s)" % marks, ids):
        if str(r["state"] or "") in _DL_RUNNING_JOB:
            out.add(int(r["job_id"]))
    return out


def v3_dl_delete_tasks(task_ids: list | None = None) -> dict:
    """按 task_id 批量删下载记录。进行中（属于在跑的任务）的行跳过并报数。"""
    def _do(m, c):
        mod = m.get("dlcenter")
        if mod is None:
            return {"ok": False, "error": "后端 dlcenter 模块没加载（检查 server\\v3\\dlcenter.py）"}
        mod.ensure(c)
        ids = []
        for x in (task_ids or []):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        if not ids:
            return {"ok": False, "error": "没有选中任何记录"}
        marks = ",".join("?" * len(ids))
        rows = c.execute(
            "SELECT task_id, job_id, state, path FROM v3_dl_task WHERE task_id IN (%s)"
            % marks, ids).fetchall()
        have = {int(r["task_id"]): r for r in rows}
        running = _dl_running_jobs(c, list({int(r["job_id"] or 0) for r in rows}))
        del_ids, skip_ids = [], []
        for tid, r in have.items():
            st = str(r["state"] or "")
            if st in DL_TASK_LIVE and int(r["job_id"] or 0) in running:
                skip_ids.append(tid)
            else:
                del_ids.append(tid)
        n = 0
        if del_ids:
            n = c.execute(
                "DELETE FROM v3_dl_task WHERE task_id IN (%s)"
                % ",".join("?" * len(del_ids)), del_ids).rowcount
            c.commit()
        return {"ok": True, "deleted": int(n), "skipped_live": len(skip_ids),
                "skipped_ids": skip_ids[:80],
                "missing": len([t for t in ids if t not in have]),
                "files_deleted": 0,
                "note": "只删了记录行；磁盘上的文件一个没动"}
    return _wrap(_do)


def v3_dl_clear_finished(job_id: int = 0, states: list | None = None) -> dict:
    """清空某任务里「已完成/失败/已取消/已跳过」的记录；进行中的一条都不动。

    job_id=0 → 取最近一个任务（与 dlcenter.snapshot 同口径），不会跨任务全库清。
    """
    def _do(m, c):
        mod = m.get("dlcenter")
        if mod is None:
            return {"ok": False, "error": "后端 dlcenter 模块没加载（检查 server\\v3\\dlcenter.py）"}
        mod.ensure(c)
        jid = int(job_id or 0)
        if not jid:
            row = c.execute("SELECT job_id FROM v3_dl_job ORDER BY job_id DESC LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "还没有任何下载任务"}
            jid = int(row["job_id"])
        st = [str(s) for s in (states or DL_TASK_FINISHED)]
        marks = ",".join("?" * len(st))
        cnt = {}
        for r in c.execute("SELECT state, COUNT(*) n FROM v3_dl_task WHERE job_id=? AND state IN (%s)"
                           " GROUP BY state" % marks, [jid] + st):
            cnt[str(r["state"])] = int(r["n"])
        n = c.execute("DELETE FROM v3_dl_task WHERE job_id=? AND state IN (%s)" % marks,
                      [jid] + st).rowcount
        c.commit()
        left = c.execute("SELECT state, COUNT(*) n FROM v3_dl_task WHERE job_id=?"
                         " GROUP BY state", (jid,)).fetchall()
        left_d = {str(r["state"]): int(r["n"]) for r in left}
        live = sum(left_d.get(k, 0) for k in DL_TASK_LIVE)
        return {"ok": True, "job_id": jid, "deleted": int(n), "by_state": cnt,
                "kept_live": live, "kept_paused": left_d.get("paused", 0),
                "left_total": sum(left_d.values()),
                "files_deleted": 0,
                "note": "只删了记录行；进行中/暂停的一条没动，磁盘文件一个没动"}
    return _wrap(_do)


# ============================================================
# 4.0 · 纯 v3 库检测（2026-10-26 新增）
# ★ 替代「调小猪库 / 鉴图.py」那条断链：全程只用 v3.db，
#   拿【已存 OCR 文本 + 当前词表】重算命中，不重跑 det、不下载、不碰小猪库。
# ============================================================

def v3_detect_v3(pack_code: str = "", pids: list | None = None,
                 roles: list | None = None, redet_missing: bool = False,
                 write: bool = True, log=None) -> dict:
    r"""纯 v3 库的「检测」：把图上文字跟当前词表重算一遍，写下命中词（不动图、不涂）。

    为什么要有它：老按钮走 `run_local_detect_slim` → 小猪 `商品.db`，而 v3 的包码
    在小猪库里一行都没有 ⇒ 跑完 0 张、界面显示「违规 0」。那不是结论，是没开门。

    但 `v3_image_ocr.boxes_json` 里 **每段文字和坐标早就存着了**
    （真库实测 236 张有坐标，只是 `word`/`kind` 空着，因为那批 OCR 没匹配词表）。
    所以这里只需要「文本 + 当前词表」重算 —— 零成本，秒级。

    参数：
      pack_code / pids  二选一，必须给（不许默认全库跑）
      roles             只查这些角色（省略 = 全部非 video）
      redet_missing     True = 对「没坐标」的图补跑一次 OCR（慢，但能补齐）
      write             False = 只算不写库（预览用）

    返回：{ok, images, with_boxes, no_boxes, hit_images, hit_boxes, words,
           redet, redet_fail, changed, words_version, details}
    """
    def _do(m, c):
        imgs_mod = m.get("images")
        if imgs_mod is None:
            return {"ok": False, "error": "后端缺 v3.images 模块"}

        where = ["i.dl_state='ok'"]
        args: list = []
        if pids:
            ids = [int(x) for x in pids]
            where.append("i.product_id IN (%s)" % ",".join("?" * len(ids)))
            args.extend(ids)
        elif pack_code:
            # ★ 2026-11-10 修「本机引擎检测产物 0」：与图片墙 detect_wall / 消除同一口径，
            #   成员包（精筛后落的那批）只在 v3_pack_member，裸查 v3_product.pack_code 得 0 件。
            ids = _pids_of(c, pack_code)
            if not ids:
                return {"ok": True, "images": 0, "with_boxes": 0, "no_boxes": 0,
                        "hit_images": 0, "hit_boxes": 0, "words": [], "redet": 0,
                        "redet_fail": 0, "changed": 0,
                        "note": "这个包在 v3.db 里没有商品（product.pack_code 与 pack_member 都没有）"}
            where.append("i.product_id IN (%s)" % ",".join("?" * len(ids)))
            args.extend(ids)
        else:
            return {"ok": False, "error": "必须指定数据包或商品 id（不允许默认全库跑）"}

        rl = [str(x) for x in (roles or []) if str(x).strip()]
        if rl:
            where.append("i.role IN (%s)" % ",".join("?" * len(rl)))
            args.extend(rl)
        else:
            where.append("i.role<>'video'")

        sql = ("SELECT i.image_id FROM v3_image i WHERE " + " AND ".join(where)
               + " ORDER BY i.image_id")
        ids = [int(r["image_id"]) for r in c.execute(sql, args)]
        if not ids:
            return {"ok": True, "images": 0, "with_boxes": 0, "no_boxes": 0,
                    "hit_images": 0, "hit_boxes": 0, "words": [], "redet": 0,
                    "redet_fail": 0, "changed": 0,
                    "note": "这个范围里没有已下载的图（dl_state 全部不是 ok）"}

        r = imgs_mod.rematch(c, ids, write=write)

        redone = redet_fail = 0
        if redet_missing and r.get("no_boxes"):
            todo = [iid for iid in ids if imgs_mod.boxes_of(c, iid) is None]
            for iid in todo[:200]:
                row = c.execute("SELECT * FROM v3_image WHERE image_id=?", (iid,)).fetchone()
                if row is None:
                    continue
                try:
                    rr = imgs_mod.ocr_image(c, dict(row))
                    if rr.get("ok"):
                        redone += 1
                    else:
                        redet_fail += 1
                except Exception as exc:              # noqa: BLE001
                    redet_fail += 1
                    if log:
                        try:
                            log("补跑 OCR 失败 img%s：%s" % (iid, str(exc)[:80]))
                        except Exception:             # noqa: BLE001
                            pass
            if redone:
                r2 = imgs_mod.rematch(c, ids, write=write)
                for k in ("hit_images", "hit_boxes", "words", "with_boxes",
                          "no_boxes", "details", "changed"):
                    r[k] = r2.get(k)

        r.update({"ok": True, "redet": redone, "redet_fail": redet_fail,
                  "redet_missing": bool(redet_missing), "write": bool(write),
                  "source": "v3.db（纯本地，未碰小猪库）"})
        return r
    return _wrap(_do)

# ================================================================ 微信小店直连上货（5.0）
# 2026-10-26 新增：不走图床、不出表，直接调官方接口把商品建到店里。
# 全流程实测过一件真货（product_id 10001791233769）。
# ★ 图床在这一路上是**多余**的：微信只认它自己的 mmecimage.cn 地址，
#   第三方图床的 URL 传进去会报 10020061「图片URL非法」。

def v3_wx_status() -> dict:
    """连通性自检：凭据在不在、token 能不能取、店铺多少件商品。"""
    def _do(m, c):
        wx = m.get("wxshop")
        if wx is None:
            return {"ok": False, "error": "后端没有 wxshop 模块"}
        return wx.status(c)
    return _wrap(_do)


def v3_wx_preview(pids=None, template_id=10001773856919, price_markup=1.25,
                  stock_per_sku=7, sku_limit=0, multi_dim=True,
                  only_screened=False, pack_code="") -> dict:
    """**只组装不联网**：看每件商品会变成什么样（图数/规格数/价格区间/问题）。

    ★ 2026-10-26 修：`pids` 原来写成必填位置参数，而界面 `tab_publish._params()`
      只传 pack_code / template_id / price_markup / sku_limit，不传 pids ——
      于是点「预览」直接 `TypeError: missing 1 required positional argument: 'pids'`。
      函数体里本来就有「pids 为空则按 pack_code 从包里推导」的逻辑，
      所以这里只需放宽签名，行为不变。

    ★ 2026-10-26 语义纠正（用户原话「我说的是每个 SKU 七件货，不是上七个 SKU」）：
      stock_per_sku = **每个 SKU 的库存件数**，默认 7
      sku_limit     = 规格**数量**上限，默认 0 = 全部上，不截断
      multi_dim     = True 时按 spec 里的 `/` 拆多维（尺码/颜色…），不是全拍平成一个

    only_screened = **默认 False（不生效）**：只有显式打勾才只建 status='fine_screened' 的；
                    False 时全建（含 imported 未精筛的）。
    """
    def _do(m, c):
        wx = m.get("wxshop")
        if wx is None:
            return {"ok": False, "error": "后端没有 wxshop 模块"}
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            mem = m["pack"].members(c, pack_code)
            if mem is not None:
                ids = [int(x) for x in mem]
            else:
                ids = [int(x["product_id"]) for x in
                       m["store"].list_products(c, pack=pack_code, limit=100000)]
        skipped = []
        if only_screened and ids:
            qs = ",".join("?" * len(ids))
            ok_ids = {int(r[0]) for r in c.execute(
                "SELECT product_id FROM v3_product WHERE status='fine_screened'"
                " AND product_id IN (%s)" % qs, ids)}
            skipped = [x for x in ids if x not in ok_ids]
            ids = [x for x in ids if x in ok_ids]
        out = []
        for pid in ids:
            r = wx.build_from_db(c, pid, int(template_id), upload=False,
                                 price_markup=float(price_markup),
                                 stock_per_sku=int(stock_per_sku),
                                 sku_limit=int(sku_limit),
                                 multi_dim=bool(multi_dim))
            row = {"product_id": pid, "ok": bool(r.get("ok")),
                   "problems": r.get("problems") or [],
                   "warns": r.get("warns") or [],
                   "error": r.get("error", "")}
            row.update(r.get("stats") or {})
            # ★ 2026-10-26 修：上面 update(stats) 会把 stats.head/desc 覆盖进来，
            #   而 stats.head 是「已上传成功的 URL 数」—— 预览不上传，恒为 0，
            #   于是界面主图/详情永远显示 0（SKU 不依赖上传所以正常）。
            #   这里用 build_from_db 的顶层口径覆盖回去：预览=本地张数，真建=已传张数。
            for k in ("head", "desc", "sku", "sku_total",
                      "head_local", "desc_local", "sku_dims",
                      # ★ 2026-10-29：SKU 图口径（预览时 sku_img 恒 0，看 sku_img_local）
                      "sku_img", "sku_img_local"):
                if k in r and r[k] is not None:
                    row[k] = r[k]
            pl = r.get("payload") or {}
            row["title"] = (pl.get("title") or "")[:40]
            sk = pl.get("skus") or []
            if sk:
                ps = [x["sale_price"] / 100.0 for x in sk]
                row["price_low"] = round(min(ps), 2)
                row["price_high"] = round(max(ps), 2)
                row["stock_each"] = (sk[0].get("stock_num") if sk else None)
                # 维度名（尺码/颜色…），界面显示成「尺码 × 颜色」这种
                keys = []
                for x in sk:
                    for a in (x.get("sku_attrs") or []):
                        kk = a.get("attr_key")
                        if kk and kk not in keys:
                            keys.append(kk)
                row["attr_keys"] = keys
            out.append(row)
        return {"ok": True, "count": len(out), "items": out,
                "skipped_unscreened": len(skipped),
                "skipped_ids": skipped[:20],
                "only_screened": bool(only_screened),
                "price_markup": float(price_markup),
                "stock_per_sku": int(stock_per_sku),
                "sku_limit": int(sku_limit),
                "multi_dim": bool(multi_dim)}
    return _wrap(_do)


# ============ 5.0 · 多店账号（与 6.0 账号管理共用 shops.json） ============
def _wx_shops_file():
    return paths.ROOT.parent / "微信小店自建上货台" / "shops.json"


def v3_wx_shops() -> dict:
    """列出可上架的店铺；**密钥不下发到界面控件**，发布时由 bridge 自己解析。"""
    try:
        p = _wx_shops_file()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            shops = [{"appid": x.get("appid") or "",
                      "name": x.get("name") or x.get("appid") or "",
                      "kind": x.get("kind") or "wx_api",
                      "enabled": x.get("enabled", True) is not False}
                     for x in (d.get("shops") or []) if x.get("appid")]
            if shops:
                return {"ok": True, "current": d.get("current") or shops[0]["appid"],
                        "shops": shops, "source": str(p)}
    except Exception as e:                          # noqa: BLE001
        pass
    aid = os.environ.get("WX_SHOP_APPID") or ""
    if aid:
        return {"ok": True, "current": aid, "source": "env",
                "shops": [{"appid": aid, "name": "默认店铺（环境变量）",
                           "kind": "wx_api", "enabled": True}]}
    return {"ok": True, "current": "", "shops": [], "source": "env"}


def v3_wx_shop_add(appid: str, secret: str, name: str) -> dict:
    """在软件端直接添加店铺（写 6.0 同一份 shops.json，不改 current、不重复建）。"""
    aid = (appid or "").strip()
    if not aid:
        return {"ok": False, "error": "AppID 不能为空"}
    try:
        p = _wx_shops_file()
        d = {"current": aid, "port": 9191, "shops": []}
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                d = old
        d.setdefault("shops", [])
        hit = next((x for x in d["shops"] if x.get("appid") == aid), None)
        rec = {"appid": aid, "secret": (secret or "").strip(),
               "name": (name or "").strip() or aid[:8], "kind": "wx_api", "enabled": True}
        if hit is None:
            d["shops"].append(rec)
            if not d.get("current"):
                d["current"] = aid
        else:
            hit.update(rec)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return {"ok": True, "appid": aid, "name": rec["name"],
                "count": len(d["shops"]), "file": str(p)}
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _wx_shop_targets(shop_ids) -> list[dict]:
    """appid 列表 → [{appid,secret,name}]；只认 shops.json 里真实存在的账号。"""
    ids = [str(x) for x in (shop_ids or []) if x]
    p = _wx_shops_file()
    d = {}
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                               # noqa: BLE001
        d = {}
    out = []
    for aid in ids:
        x = next((y for y in (d.get("shops") or []) if y.get("appid") == aid), None)
        if x and x.get("secret") and x.get("enabled", True) is not False:
            out.append({"appid": aid, "secret": x["secret"],
                        "name": x.get("name") or aid[:8]})
    return out


def v3_wx_publish(pids=None, template_id=10001773856919, price_markup=1.25,
                  stock_per_sku=7, sku_limit=0, multi_dim=True,
                  only_screened=False, pack_code="", titles=None,
                  progress=None, shop_ids=None) -> dict:
    """**真建商品**：上传图片 → product/add，返回 product_id 列表。

    titles 可以给 {pid: 新标题}，不给就用库里原标题。
    progress 是可选的逐件回调，每件报一次 {i,total,pid,stage,...}，用于界面进度条与日志。

    stock_per_sku  每个 SKU 的库存件数（默认 7）
    sku_limit      规格数量上限（默认 0 = 全上，不截断）
    multi_dim      按 spec 的 `/` 拆多维 sku_attrs（默认 True）
    only_screened  **默认 False（不生效）**；只有显式打勾才只建 status='fine_screened'

    ★ 2026-10-26 同上：`pids` 改为可选。界面在「没勾只建预览通过的」时
      根本不会往 kwargs 里塞 pids，原来那样必抛 TypeError。
    """
    _CUR_SHOP = [""]                # 多店逐店跑时，进度消息带上店名
    def _emit(d):
        if progress is None:
            return
        try:
            d = dict(d or {})
            if _CUR_SHOP[0]:
                d["shop"] = _CUR_SHOP[0]
            progress(d)
        except Exception:  # noqa: BLE001
            pass          # 进度回调坏了不能影响建商品本身

    def _do(m, c):
        wx = m.get("wxshop")
        if wx is None:
            return {"ok": False, "error": "后端没有 wxshop 模块"}
        ids = [int(x) for x in (pids or [])]
        if not ids and pack_code:
            mem = m["pack"].members(c, pack_code)
            if mem is not None:
                ids = [int(x) for x in mem]
            else:
                ids = [int(x["product_id"]) for x in
                       m["store"].list_products(c, pack=pack_code, limit=100000)]
        skipped = []
        if only_screened and ids:
            qs = ",".join("?" * len(ids))
            ok_ids = {int(r[0]) for r in c.execute(
                "SELECT product_id FROM v3_product WHERE status='fine_screened'"
                " AND product_id IN (%s)" % qs, ids)}
            skipped = [x for x in ids if x not in ok_ids]
            ids = [x for x in ids if x in ok_ids]
        tmap = {int(k): v for k, v in (titles or {}).items()}
        done, failed, skipped_dup = [], [], []
        total = len(ids)
        # ★ 2026-10-27 幂等护栏（主人问：中断后重跑会不会同一件货上架两次）
        #   ① 拉一次微信侧现有商品索引 → 建过的跳过
        #   ② 每件建之前先落「意图」行（creating），崩了也能对账
        #   ③ 图片上传走 v3_wx_img_cache，已传的 0 次网络
        pg = m.get("publish_guard")
        # ★★ 2026-10-28 修真 bug：这里原来叫 idx，而下面的循环是 `for idx, pid in enumerate(ids,1)`
        #    —— 循环变量把这份索引**整个覆盖成整数**，于是 dedup_check 收到 int，
        #    每件都抛 AttributeError: 'int' object has no attribute 'get' → 记「护栏降级」，
        #    等于**查重全程没生效**（2026-10-28 01:0x 那批 142 件日志里每件都有这一行）。
        #    为什么昨天 23/23 全过：那是直接喂 dict 打桩，没走真实调用点。
        #    改名 idx_d 后与循环变量彻底分开。
        idx_d = None
        if pg is not None:
            try:
                ix = pg.wx_index(c)
                if ix.get("ok"):
                    idx_d = ix
                    cov = ix.get("coverage") or {}
                    _emit({"i": 0, "total": total, "stage": "查重就绪",
                           "detail": "微信侧现有 %d 件商品；已建过的会自动跳过%s"
                                     % (int(ix.get("count") or 0),
                                        ("（标题索引只覆盖 %d/%d，还差 %d 件 —— "
                                         "差的部分只能靠本地记录拦）"
                                         % (cov.get("indexed") or 0,
                                            cov.get("store_ids") or 0,
                                            cov.get("pending") or 0))
                                        if (cov.get("pending") or 0) > 0 else
                                        "（标题索引覆盖全店 %d 件）"
                                        % (cov.get("indexed") or 0))})
                    if (cov.get("pending") or 0) > 0:
                        _emit({"i": 0, "total": total, "stage": "⚠ 查重覆盖不全",
                               "detail": "微信侧还有 %d 件没建标题索引。"
                                         "先跑一次「建微信商品索引」再来（约 2–4 分钟），"
                                         "否则可能与这些商品重复"
                                         % int(cov.get("pending") or 0)})
                else:
                    _emit({"i": 0, "total": total, "stage": "查重降级",
                           "detail": "微信列表取不到（%s）→ 本次未做微信侧查重，"
                                     "但本地已建记录仍会拦" % (ix.get("err") or "未知")})
            except Exception as e:  # noqa: BLE001
                _emit({"i": 0, "total": total, "stage": "查重降级",
                       "detail": "%s: %s" % (type(e).__name__, e)})
        _emit({"i": 0, "total": total, "stage": "开始",
               "detail": "%d 件 · 系数 ×%.2f · 每SKU %d 件 · 规格%s%s"
                         % (total, price_markup, int(stock_per_sku),
                            ("≤%d" % sku_limit) if sku_limit else "全部",
                            (" · 只建精筛通过（跳过 %d 件）" % len(skipped))
                            if only_screened else "")})
        for idx, pid in enumerate(ids, 1):
            # —— 建前查重 + 意图先行（崩溃后能对账的唯一依据）——
            title_i = tmap.get(pid, "") or ""
            fp, rid = "", 0
            if pg is not None:
                try:
                    fp = pg.fingerprint(c, pid, title_i, float(price_markup),
                                        int(sku_limit), int(stock_per_sku))
                    d = pg.dedup_check(c, pid, title_i, fp, idx_d)
                    if d.get("action") == "skip":
                        skipped_dup.append({"product_id": pid,
                                            "wx_product_id": d.get("wx") or "",
                                            "reason": d.get("reason") or ""})
                        _emit({"i": idx, "total": total, "pid": pid,
                               "stage": "⏭ 跳过（已建过）",
                               "detail": d.get("reason") or ""})
                        continue
                    if d.get("warn"):
                        _emit({"i": idx, "total": total, "pid": pid, "stage": "提示",
                               "detail": d["warn"]})
                    rid = pg.begin(c, pid, pack_code, title_i or fp, fp)
                except Exception as e:  # noqa: BLE001
                    rid = 0
                    _emit({"i": idx, "total": total, "pid": pid, "stage": "护栏降级",
                           "detail": "%s: %s（本件没有查重保护）"
                                     % (type(e).__name__, e)})
            _emit({"i": idx, "total": total, "pid": pid, "stage": "上传图片中",
                   "detail": "（每件 20-60 秒；已传过的图不重传）"})
            try:
                r = wx.build_from_db(c, pid, int(template_id),
                                     title=tmap.get(pid, ""),
                                     price_markup=float(price_markup),
                                     stock_per_sku=int(stock_per_sku),
                                     sku_limit=int(sku_limit),
                                     multi_dim=bool(multi_dim))
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if not r.get("ok"):
                msg = "; ".join(r.get("problems") or []) or r.get("error") or "组装失败"
                failed.append({"product_id": pid, "error": msg})
                if pg is not None and rid:
                    try:
                        pg.finish(c, rid, "failed", errmsg=msg, note="组装阶段失败，可安全重试")
                    except Exception:  # noqa: BLE001
                        pass
                _emit({"i": idx, "total": total, "pid": pid, "stage": "组装失败",
                       "detail": msg})
                continue
            try:
                a = wx.add_product(r["payload"])
            except Exception as e:  # noqa: BLE001
                a = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            n_head = r.get("head") or 0
            n_desc = r.get("desc") or 0
            n_sku = r.get("sku") or 0
            n_sku_all = r.get("sku_total") or n_sku
            if a.get("ok"):
                done.append({"product_id": pid,
                             "wx_product_id": a.get("product_id"),
                             "title": (r["payload"].get("title") or "")[:40],
                             "head": n_head, "desc": n_desc,
                             "sku": n_sku, "sku_total": n_sku_all})
                if pg is not None and rid:
                    try:
                        pg.finish(c, rid, "created", a.get("product_id"))
                    except Exception:  # noqa: BLE001
                        pass
                _emit({"i": idx, "total": total, "pid": pid, "stage": "✓ 已建",
                       "wx": a.get("product_id"), "head": n_head, "desc": n_desc,
                       "sku": n_sku})
            else:
                msg = a.get("errmsg") or a.get("error") or "建商品失败"
                failed.append({"product_id": pid, "error": msg,
                               "errcode": a.get("errcode")})
                if pg is not None and rid:
                    try:
                        pg.finish(c, rid, "failed", errcode=a.get("errcode") or 0,
                                  errmsg=msg, note="建商品接口返回失败，可重试")
                    except Exception:  # noqa: BLE001
                        pass
                _emit({"i": idx, "total": total, "pid": pid, "stage": "✗ 建失败",
                       "detail": msg, "head": n_head, "desc": n_desc, "sku": n_sku})
        _emit({"i": total, "total": total, "stage": "结束",
               "detail": "成功 %d · 跳过(已建过) %d · 失败 %d"
                         % (len(done), len(skipped_dup), len(failed))})
        return {"ok": True, "total": total, "created": len(done),
                "failed": len(failed), "items": done, "errors": failed,
                "skipped_dup": len(skipped_dup), "dups": skipped_dup}
    if not shop_ids:
        return _wrap(_do)                       # 老行为：环境变量单店，一字节不变
    targets = _wx_shop_targets(shop_ids)
    if not targets:
        return {"ok": False, "error": "选中的店铺没有可用凭据，请到 5.0「＋ 添加店铺」核对 AppSecret"}
    out = {"ok": True, "total": 0, "created": 0, "failed": 0,
           "items": [], "errors": [], "skipped_dup": 0, "dups": [],
           "shops": [], "multi_shop": True}
    for t in targets:
        _CUR_SHOP[0] = t["name"]

        def _scoped(m, c, _t=t):
            wx = m.get("wxshop")
            if wx is None or not hasattr(wx, "shop_context"):
                return {"ok": False, "error": "后端 wxshop 不支持多店（请重开软件加载新代码）"}
            with wx.shop_context(_t["appid"], _t["secret"], _t["name"]):
                return _do(m, c)

        r = _wrap(_scoped)
        if not isinstance(r, dict) or not r.get("ok"):
            msg = (r or {}).get("error") or "未知错误"
            out["shops"].append({"appid": t["appid"], "name": t["name"],
                                 "ok": False, "error": msg, "created": 0, "failed": 0})
            _emit({"i": 0, "total": 0, "stage": "✗ 该店失败", "detail": msg})
            continue
        for k in ("total", "created", "failed", "skipped_dup"):
            out[k] += int(r.get(k) or 0)
        out["items"] += (r.get("items") or [])
        out["errors"] += (r.get("errors") or [])
        out["dups"] += (r.get("dups") or [])
        out["shops"].append({"appid": t["appid"], "name": t["name"], "ok": True,
                             "created": int(r.get("created") or 0),
                             "failed": int(r.get("failed") or 0),
                             "skipped_dup": int(r.get("skipped_dup") or 0)})
        _emit({"i": 0, "total": 0, "stage": "✓ 该店完成",
               "detail": "%s：成功 %d · 失败 %d" % (t["name"], r.get("created") or 0,
                                                  r.get("failed") or 0)})
    _CUR_SHOP[0] = ""
    return out

# ============ 5.0 · 上架批次台账（2026-10-27 主人要求） ============
# 主人原话：「每一批产品它需要保存…上完以后要新建一个 tab 页，名称就是今天的名称，
#   我要知道哪天上架哪个产品又有记录，下一次录入商品上架以后又换了新的。」
# 落库在 v3.db 的 v3_upload_batch（见 server/v3/batches.py），这里只做壳。

def _v3_wr():
    """v3.db 写连接（用完关）。调用方负责 close。"""
    p = str(paths.TAOBAO_DIR / "data" / "v3.db")
    c = sqlite3.connect(p, timeout=8)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    return c


def v3_batch_save(pack_code: str = "", template_id: str = "", markup: float = 1.0,
                  profit_pct: float = 0.0, stock_per_sku: int = 0,
                  items: list | None = None, created: int = 0, failed: int = 0,
                  note: str = "") -> dict:
    """把这一批上架的明细存成一条批次记录，返回 {batch_id, name}。"""
    def _do(m, c):
        mod = m.get("batches")
        if mod is None:
            return {"ok": False, "error": "batches 模块没加载（server/v3/batches.py）"}
        return mod.save(c, pack_code=pack_code, template_id=str(template_id or ""),
                        markup=markup, profit_pct=profit_pct,
                        stock_per_sku=stock_per_sku, items=items,
                        created=created, failed=failed, note=note)
    return _wrap(_do)


def v3_batch_list(limit: int = 60) -> dict:
    """批次列表（新的在前，不含明细）。"""
    def _do(m, c):
        mod = m.get("batches")
        if mod is None:
            return {"ok": False, "error": "batches 模块没加载", "items": []}
        return mod.lst(c, limit=limit)
    return _wrap(_do)


def v3_batch_get(batch_id: int) -> dict:
    """某批的完整明细（含每件的 product_id / 标题 / 微信商品ID / 成败）。"""
    def _do(m, c):
        mod = m.get("batches")
        if mod is None:
            return {"ok": False, "error": "batches 模块没加载"}
        return mod.get(c, int(batch_id))
    return _wrap(_do)


# ============ 5.0 · 防重复铺货（幂等护栏，2026-10-27 主人追问） ============
# 主人原话：「图片已经上传过一部分了，你这边停掉了，那我再上传，它会是什么样的情形？
#   会不会因为这种中断，一件货同时上架两次，导致后台出现了重复的铺货？」
# 实现见 server/v3/publish_guard.py：图片缓存 + 意图先行 + 建前查重 + 对账。

def v3_publish_state(limit: int = 40) -> dict:
    """看发布台账：待对账(creating) / 已建(created) / 失败(failed) / 图片缓存条数。"""
    def _do(m, c):
        mod = m.get("publish_guard")
        if mod is None:
            return {"ok": False, "error": "publish_guard 模块没加载"}
        return mod.state(c, limit=limit)
    return _wrap(_do)


def v3_publish_reconcile(force: bool = True) -> dict:
    """对账：把上次中断留下的 creating 行拿去微信侧认领 —— 认领到就不重建。

    force=True 强制刷新微信侧索引（对账必须看最新，不能用缓存）。
    """
    def _do(m, c):
        mod = m.get("publish_guard")
        if mod is None:
            return {"ok": False, "error": "publish_guard 模块没加载"}
        return mod.reconcile(c, force_index=bool(force))
    return _wrap(_do)


def v3_publish_failed(limit: int = 5000) -> dict:
    """「上次没建成」的商品号清单 —— 供界面「只重试失败」用。"""
    def _do(m, c):
        mod = m.get("publish_guard")
        if mod is None:
            return {"ok": False, "error": "publish_guard 模块没加载", "pids": []}
        pids = mod.failed_pids(c, limit=limit)
        return {"ok": True, "count": len(pids), "pids": pids}
    return _wrap(_do)


# --------------------------------------------------------------- Excel 路线（一键出表）
# 走 app/excel_route.py：抖音采集计划 plan-*.json → 小猪 16 列标准模板。
# 不依赖 v3 模块（图床缓存直接读 v3.db，补传才 lazy import wxshop），所以不走 _wrap。
def excel_route_export(src: str = None, out: str = None, min_sku: int = 7,
                       max_sku: int = 0, fill_images: bool = False,
                       progress=None, log=None) -> dict:
    """一键把 plan-*.json 全部导成可直接上传小猪的 xlsx。"""
    try:
        from . import excel_route  # noqa: PLC0415
        return excel_route.export_all(src=src, out=out, min_sku=int(min_sku or 1),
                                      max_sku=int(max_sku or 0),
                                      fill_images=bool(fill_images),
                                      progress=progress, log=log)
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex),
                "items": [], "count": 0, "ok_count": 0, "fail_count": 0,
                "rows_total": 0, "img_miss_unique": 0, "img_miss_sample": []}


def excel_route_defaults() -> dict:
    """给界面显示默认路径用。"""
    try:
        from . import excel_route  # noqa: PLC0415
        return {"ok": True, "src": excel_route.CRAWL_DIR, "out": excel_route.OUT_DIR}
    except Exception as ex:      # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}

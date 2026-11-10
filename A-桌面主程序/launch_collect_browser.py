#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动「序列一 · 淘宝 URL 采集」用的受控浏览器。

设计要点（都是踩过坑之后定的）：
  1) 端口不写死：先扫已有 CDP，能复用就复用；否则从 9223 起挑一个真空闲的。
  2) 不抢日常浏览器：用【专用配置目录】(%LOCALAPPDATA%\\dsh-collect-browser)，
     与日常 Edge / ego 完全隔离 —— 两者可以同时开，无需退出日常浏览器。
     这也是 Chrome/Edge 136+ 之后的硬要求：默认配置目录不接受 --remote-debugging-port。
  3) 全部中文输出在 Python 侧完成，.cmd 里不许出现任何非 ASCII 字节。
  4) 只写配置、不改任何业务代码：把实际端口回写进 settings.json，
     程序的 find_cdp_port(settings["cdp_port"]) 会优先用它 ⇒ 等于动态端口。

用法：
  python launch_collect_browser.py                 # 自动选浏览器 + 自动端口 + 校验 + 回写配置
  python launch_collect_browser.py --browser edge  # 指定 Edge
  python launch_collect_browser.py --browser ego   # 指定 ego lite
  python launch_collect_browser.py --port 9230     # 指定端口（默认 auto）
  python launch_collect_browser.py --profile default  # 用你日常配置（会要求先退出浏览器）
  python launch_collect_browser.py --no-save       # 只启动校验，不改 settings.json
  python launch_collect_browser.py --dry-run       # 只看会做什么，不启动
  python launch_collect_browser.py --print-port    # 只打印当前可用的采集端口（供程序调用）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

# Chromium 系（含 ego lite / Tabbit）都用同一套 CDP
BROWSERS = {
    "edge": {
        "label": "Microsoft Edge",
        "exes": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "procs": ["msedge.exe"],
        "default_ud": r"C:\Users\Administrator\AppData\Local\Microsoft\Edge\User Data",
    },
    "ego": {
        "label": "ego lite",
        "exes": [r"C:\Program Files\Citro Labs\ego lite\Application\ego.exe"],
        "procs": ["ego.exe"],
        "default_ud": r"F:\Users\Administrator\AppData\Local\Citro Labs\ego lite\User Data",
    },
}
AUTO_ORDER = ["ego", "edge"]          # 主浏览器 = ego lite；Edge 只作备选替代
PORT_FROM, PORT_TO = 9223, 9299       # 采集端口候选区（避开 Tabbit 的 9222）
SCAN_FROM, SCAN_TO = 9222, 9299
DEDICATED_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local"),
                              "dsh-collect-browser")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- 基础探测
def tcp_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def cdp_info(port: int, timeout: float = 1.5) -> dict | None:
    """是 CDP 端点就返回 /json/version 的内容，否则 None。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def scan_live_cdp() -> list:
    out = []
    for p in range(SCAN_FROM, SCAN_TO + 1):
        if not tcp_open(p):
            continue
        v = cdp_info(p)
        if v:
            out.append((p, str(v.get("Browser", "?"))))
    return out


def pick_free_port(prefer=None) -> int:
    cand = ([prefer] if prefer else []) + list(range(PORT_FROM, PORT_TO + 1))
    for p in cand:
        if tcp_open(p):
            continue
        try:
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
    raise SystemExit("[ERR] %d-%d 全部被占用，没有可用端口" % (PORT_FROM, PORT_TO))


def _cim(ud, fields):
    """列出命令行里带这个配置目录的浏览器进程，返回 [(pid, 端口)]。"""
    if not ud or os.name != "nt":
        return []
    pat = ud.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='ego.exe'\" "
        "| Where-Object { $_.CommandLine -like '*" + pat + "*' } "
        "| Select-Object ProcessId,CommandLine "
        "| ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
    )
    out = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=20,
                           creationflags=NO_WINDOW)
    except Exception:
        return out
    import re
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        if not pid_s.strip().isdigit():
            continue
        m = re.search(r"--remote-debugging-port=(\d+)", cmd)
        out.append((int(pid_s), int(m.group(1)) if m else None))
    return out


def procs_using_ud(ud):
    """有没有进程正在用这个配置目录（有的话单实例机制会让新端口参数失效）。"""
    return [pid for pid, _ in _cim(ud, None)]


def find_our_instance(ud):
    """在【我们自己的配置目录】上已经起来的实例，且端口活着 → 返回端口。

    这一步必须按命令行的 --user-data-dir 判定，不能只看 /json/version：
    ego 和 Chrome 报的都是 "Chrome/xxx"，Browser 字段区分不出来；
    Tabbit 的 9222 也活着，但它不是我们的采集实例，绝不能复用。
    """
    for pid, port in _cim(ud, None):
        if port and cdp_info(port, 1.0):
            return port
    return None


def resolve_browser(name):
    """返回 (实际 exe 路径, 规范名)。"""
    order = AUTO_ORDER if name == "auto" else [name]
    for key in order:
        cfg = BROWSERS.get(key)
        if not cfg:
            raise SystemExit("[ERR] 不认识的浏览器代号：%s（可用：auto / edge / ego）" % name)
        for exe in cfg["exes"]:
            if os.path.isfile(exe):
                return exe, key
    raise SystemExit("[ERR] 没找到浏览器可执行文件（%s）。检查安装路径。" % name)


def ensure_page_target(port, url="about:blank", timeout=12.0):
    """确保存在 type=page 的目标。

    ★ ego lite 冷启动会停在 chrome://onboarding/（"欢迎使用 ego(𝘭𝘪𝘵𝘦)"），
      此时 /json/list 里【只有 browser_ui，没有 page】——
      看起来像"这个浏览器不支持标签页"，其实只是引导页占着、没有真实网页目标。
      用浏览器级 Target.createTarget 开一个网页后，page 目标立刻出现（实测 Chrome/154）。
    """
    def pages():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
                return [t for t in json.loads(r.read().decode("utf-8", "replace"))
                        if t.get("type") == "page"]
        except Exception:
            return []

    got = pages()
    if got:
        return got, "已有 %d 个 page 目标" % len(got)

    v = cdp_info(port, 2.0) or {}
    wsurl = v.get("webSocketDebuggerUrl")
    if not wsurl:
        return [], "拿不到浏览器级 webSocketDebuggerUrl，无法建标签"
    try:
        import websocket
        w = websocket.create_connection(wsurl, timeout=8, suppress_origin=True)
        try:
            w.send(json.dumps({"id": 1, "method": "Target.createTarget",
                               "params": {"url": url}}))
            w.recv()
        finally:
            w.close()
    except Exception as e:
        return pages(), "建标签失败：%r" % e

    t0 = time.time()
    while time.time() - t0 < timeout:
        got = pages()
        if got:
            return got, "已新建 page 目标（%s）" % url
        time.sleep(0.5)
    return [], "建了标签但没等到 page 目标（可能是引导页拦截了新开页）"


# ---------------------------------------------------------------- CDP 校验
def verify(port, verbose=True):
    ok = True

    def chk(no, name, passed, detail=""):
        nonlocal ok
        mark = "PASS" if passed else "FAIL"
        if verbose:
            print("  [%d] %-22s %s  %s" % (no, name, mark, detail))
        if not passed:
            ok = False
        return passed

    v = cdp_info(port, 2.0)
    chk(1, "/json/version", bool(v),
        ("%s · protocol %s" % (v.get("Browser"), v.get("Protocol-Version"))) if v else "无响应")

    pages = []
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
            targets = json.loads(r.read().decode("utf-8", "replace"))
        pages = [t for t in targets if t.get("type") == "page"]
        kinds = {}
        for t in targets:
            kinds[t.get("type")] = kinds.get(t.get("type"), 0) + 1
        chk(2, "/json/list 标签页", len(pages) > 0,
            "page=%d 全部类型=%s" % (len(pages), kinds))
    except Exception as e:
        chk(2, "/json/list 标签页", False, repr(e))

    wsmod = None
    try:
        import websocket  # noqa
        wsmod = getattr(websocket, "__version__", "?")
        chk(3, "websocket 模块", True, "版本 %s" % wsmod)
    except Exception as e:
        chk(3, "websocket 模块", False, "%s —— 采集会在这里必挂，先补依赖" % e)

    if not pages:
        chk(4, "Runtime.evaluate", False, "没有 page 目标，跳过")
        chk(5, "Input.dispatchMouseEvent", False, "没有 page 目标，跳过")
        return ok

    if wsmod is None:
        return ok

    import websocket  # noqa
    url = pages[0].get("webSocketDebuggerUrl", "")
    try:
        ws = websocket.create_connection(url, timeout=8, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": "1+1", "returnByValue": True}}))
        got = json.loads(ws.recv())
        val = got.get("result", {}).get("result", {}).get("value")
        chk(4, "Runtime.evaluate", val == 2, "1+1=%s" % val)
        ws.send(json.dumps({"id": 2, "method": "Input.dispatchMouseEvent",
                            "params": {"type": "mouseMoved", "x": 2, "y": 2}}))
        got2 = json.loads(ws.recv())
        chk(5, "Input.dispatchMouseEvent", got2.get("id") == 2 and "error" not in got2,
            str(got2.get("error", "ok")))
        ws.close()
    except Exception as e:
        chk(4, "Runtime.evaluate", False, repr(e))
    return ok


# ---------------------------------------------------------------- 写配置
def settings_files():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = [
        os.path.join(here, "data", "settings.json"),
        os.path.join(here, "dist", "上货台", "data", "settings.json"),
    ]
    return [p for p in cand if os.path.isfile(p)]


def save_port(port):
    ts = time.strftime("%Y%m%d-%H%M%S")
    for p in settings_files():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            old = data.get("cdp_port")
            if old == port:
                print("       %s  已是 %s，跳过" % (p, port))
                continue
            bak = "%s.bak-portauto-%s" % (p, ts)
            shutil.copy2(p, bak)
            data["cdp_port"] = port
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("       %s  %s -> %s   备份 %s" % (p, old, port, os.path.basename(bak)))
        except Exception as e:
            print("       [WARN] 写 %s 失败：%s" % (p, e))


def hoguang_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 上货台.exe"],
                           capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
        return "上货台.exe" in r.stdout
    except Exception:
        return False


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--browser", default="auto", choices=["auto", "edge", "ego"])
    ap.add_argument("--port", default="auto")
    ap.add_argument("--profile", default="dedicated", choices=["dedicated", "default"])
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-port", action="store_true")
    ap.add_argument("--force-new", action="store_true", help="不复用已有 CDP，强制新起一个")
    ap.add_argument("--reuse-port", default="", help="强制复用指定端口上的 CDP（不新起实例）")
    ap.add_argument("--wait", type=int, default=40)
    ap.add_argument("--open-url", default="about:blank",
                    help="启动后确保打开的网页（ego 冷启动停在引导页时用它拿到 page 目标）")
    a = ap.parse_args()

    print("=" * 66)
    print(" 采集浏览器启动器  (端口自动 / 配置隔离 / CDP 校验)")
    print("=" * 66)

    live = scan_live_cdp()
    print("\n[1] 现有 CDP 端点：")
    if live:
        for p, b in live:
            print("        %s  %s" % (p, b))
    else:
        print("        （无）")

    if a.print_port:
        _, pkey = resolve_browser(a.browser)
        pud = os.path.join(DEDICATED_ROOT, pkey)
        ours = find_our_instance(pud)
        if ours:
            print("\n可用采集端口：%d（我们的采集实例在跑）" % ours)
        else:
            try:
                print("\n可用采集端口：%d（采集浏览器尚未启动，这是当前空闲端口）" % pick_free_port())
            except SystemExit as e:
                print("\n%s" % e)
        return 0

    # [2] 决定浏览器与配置目录
    exe, key = resolve_browser(a.browser)
    cfg = BROWSERS[key]
    if a.profile == "dedicated":
        ud = os.path.join(DEDICATED_ROOT, key)
        tag = "专用配置（与日常浏览器隔离，可同时开）"
    else:
        ud = cfg["default_ud"]
        tag = "你的日常配置（需要先完全退出该浏览器）"
    print("\n[2] 浏览器：%s" % cfg["label"])
    print("        exe  %s" % exe)
    print("        配置 %s" % ud)
    print("        模式 %s" % tag)

    if not os.path.isdir(ud) and a.profile == "default":
        print("        [ERR] 日常配置目录不存在：%s" % ud)
        return 2

    # [3] 复用：只认【我们自己的配置目录】上的实例
    if not a.force_new:
        if a.reuse_port:
            p = int(a.reuse_port)
            if cdp_info(p, 1.0):
                print("\n[3] 按 --reuse-port 复用手动指定的端口 %s。" % p)
                print("\n[4] 校验：")
                okv = verify(p)
                finish(p, okv, a)
                return 0 if okv else 3
            print("\n[3] --reuse-port %s 上没有 CDP 端点，改为新起实例。" % p)
        else:
            ours = find_our_instance(ud)
            if ours:
                print("\n[3] 我们的采集实例已在端口 %s 上运行，直接复用。" % ours)
                print("\n[4] 校验：")
                okv = verify(ours)
                finish(ours, okv, a)
                return 0 if okv else 3
    print("\n[3] 需要新起一个受控实例。")
    other = [(p, b) for p, b in live if "node" not in b.lower()]
    if other:
        print("        注：另有 %d 个 CDP 端点在跑（%s），它们不是你日常浏览器的采集实例，"
              % (len(other), ", ".join("%d=%s" % (p, b) for p, b in other)))
        print("            不会被复用，也不会被你这次启动影响。")

    # [4] 端口
    port = int(a.port) if a.port != "auto" else pick_free_port()
    print("        选定端口 %s" % port)

    # [5] 同配置目录已在跑 → 单实例机制会吞掉端口参数
    busy = procs_using_ud(ud)
    if busy:
        print("\n[5] [STOP] 有进程正在使用这个配置目录：PID %s" % busy)
        print("        同一配置目录只能有一个实例；这时再启动，新端口参数会被忽略。")
        print("        请先关闭那个窗口（专用配置模式下，关掉它即可，不影响你日常浏览器）。")
        return 4

    # [6] 启动
    args = [
        exe,
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % ud,
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
    ]
    if key == "edge":
        args.append("--disable-features=msEdgeFirstRunExperience,msImplicitSignin")
    if a.profile == "default":
        args.append("--profile-directory=Profile 1")
    print("\n[6] 启动：%s …" % " ".join(args[:2]))
    if a.dry_run:
        print("        (--dry-run，不真的启动)")
        return 0
    try:
        subprocess.Popen(args, creationflags=NO_WINDOW, close_fds=True)
    except Exception as e:
        print("        [ERR] 启动失败：%s" % e)
        return 5

    # [7] 等端口
    print("\n[7] 等 /json/version 就绪（最多 %d 秒）…" % a.wait)
    t0 = time.time()
    v = None
    while time.time() - t0 < a.wait:
        v = cdp_info(port, 1.0)
        if v:
            break
        time.sleep(0.5)
    if not v:
        print("        [ERR] %d 秒内端口没开。可能：" % a.wait)
        print("              1) 该浏览器版本拒绝 --remote-debugging-port")
        print("              2) 配置目录被锁（先彻底退出该浏览器再试）")
        print("              3) 端口被中途占用")
        return 6
    print("        就绪 %.1fs  %s" % (time.time() - t0, v.get("Browser")))

    # [7.5] ego 冷启动停在引导页时只有 browser_ui，这里补一个真实网页目标
    print("\n[7.5] 确保有真实网页目标：")
    _, why = ensure_page_target(port, url=a.open_url)
    print("        %s" % why)

    # [8] 校验
    print("\n[8] 五层校验：")
    okv = verify(port)

    finish(port, okv, a)
    return 0 if okv else 3


def finish(port, okv, a):
    print("\n[9] 结论：", "OK 可用，采集可以连这个端口" if okv else "FAIL 不可用，见上面 FAIL 项")
    if not a.no_save:
        print("\n[10] 回写配置（端口不再写死）：")
        save_port(port)
        if hoguang_running():
            print("       [NOTE] 上货台正在运行，settings 的改动要重启程序才生效。")
    else:
        print("\n[10] 未回写配置（--no-save）。采集端口 = %s" % port)
    print("""
下一步：
  1. 这个窗口里打开淘宝，第一次需要登录一次（专用配置下是全新的 Cookie）。
     登录一次之后长期复用，不用每次重登。
  2. 回上货台 -> "1 采集初筛" -> 点「开始采集」。
     采集是后台跑的（Runtime.evaluate / Input.dispatch），不抢鼠标键盘。
  3. 若换了端口，把上货台设置里的「调试端口」也改成 %s，或直接重启上货台读新配置。
""" % port)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

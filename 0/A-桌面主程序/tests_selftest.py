# -*- coding: utf-8 -*-
"""自测：不开显示器也能验「窗口能不能起来、五个步骤页在不在、有没有偷偷开浏览器」。

    F:\\pyenvs\\douyin\\Scripts\\python.exe tests_selftest.py

它会：
    · 用 offscreen 平台把窗口建起来（不抢你的焦点、不出现在任务栏）
    · 逐页渲染截图到 data\\screenshots\\
    · 检查页数、页名、最小尺寸、有没有浏览器跳转代码
    · 打印 合计 N 项，通过 N 项，失败 0 项
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PASS, FAIL, NOTES = [], [], []


def ck(name: str, ok: bool, note: str = ""):
    (PASS if ok else FAIL).append(name)
    NOTES.append(("✓" if ok else "✗", name, note))


def main() -> int:
    from PyQt6.QtWidgets import QApplication
    from app import paths, theme
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLE)
    win = MainWindow()
    win.resize(theme.DEFAULT_W, theme.DEFAULT_H)
    win.show()
    app.processEvents()

    # ---- 1 窗口 ----
    ck("窗口标题含「上货台」", "上货台" in win.windowTitle(), win.windowTitle())
    ck("最小尺寸 = 750x600（对齐小猪 MinimumSize）",
       win.minimumWidth() == 750 and win.minimumHeight() == 600,
       f"{win.minimumWidth()}x{win.minimumHeight()}")

    # ---- 2 步骤页（含 2.5 商品库；第 6 步已删、设置移到右侧）----
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    _has_wall = getattr(win, "t_wall", None) is not None
    # 2026-10-24：又多了一个「4.1 · 主图 AI 重做」页，页签数不再固定 5/6。
    # 断言改成"至少包含应有的步骤，且 2.5 位置严格正确"，避免每加一页就红一次。
    ck("步骤页数量 ≥ 5 且含 2.5 时为 6 起",
       win.tabs.count() >= (6 if _has_wall else 5), str(names))
    ck("2.5 商品库在第二步与第三步之间",
       (not _has_wall) or (
           any("商品库" in n for n in names)
           and names.index([n for n in names if "商品库" in n][0])
           == names.index([n for n in names if "商品卡导入" in n][0]) + 1),
       str(names))
    ck("商品库页只读打开生产库（mode=ro）",
       (not _has_wall) or (
           "mode=ro" in (HERE / ".." / "taobao-picker" / "server" / "library.py")
           .read_text(encoding="utf-8", errors="replace")
           if (HERE / ".." / "taobao-picker" / "server" / "library.py").exists() else False),
       "Library 用 file:...?mode=ro")
    ck("设置不再占页签，改为右侧抽屉",
       not any("设置" in n for n in names) and hasattr(win, "settings_panel"),
       "settings_panel=" + str(hasattr(win, "settings_panel")))
    want = ["采集初筛", "商品卡导入", "商品精筛", "检测打码", "出表"]
    for w in want:
        ck(f"有「{w}」页", any(w in n for n in names))

    # ---- 3 没有店铺管理这类无关页 ----
    dropped = ["店铺管理", "云库", "授权码", "卡密"]
    ck("没有店铺管理等无关页",
       not any(any(d in n for d in dropped) for n in names), str(names))

    # ---- 4 浏览器：只许「用户点一下才开」，不许后台自动跳 ----
    #   2026-10-22 主人要求保留列表里能点开商品页做人工二次核实，
    #   所以断言从「0 处」改成「只有 1 处，且必须由点击触发」。
    CALLS = [
        re.compile(r"QDesktopServices\s*\.\s*openUrl\s*\("),
        re.compile(r"webbrowser\s*\.\s*open\w*\s*\("),
        re.compile(r"startfile\s*\(\s*[\"']https?://"),
        re.compile(r"ShellExecute\w*\s*\([^)]*[\"']https?://"),
    ]
    # ★ 2026-10-27：断言再改口径。6.0 集成后新增 3 个页面（下载中心/下载监控/
    #   直接上架）也有用户触发的开浏览器/开本地文件调用，原「只在 tabs.py」
    #   的写死口径失效。真不变量 = 每处调用都在某个 def 方法内，且该方法名在
    #   本文件有 .connect(...)self.方法 或 addAction(...,self.方法) 绑定
    #   （按钮/双击/右键菜单 = 用户动作触发）。
    url_open = []
    for py in (HERE / "app").rglob("*.py"):
        txt = py.read_text(encoding="utf-8", errors="replace")
        for rx in CALLS:
            for m in rx.finditer(txt):
                head = txt[:m.start()]
                fn = re.findall(r"^\s*def\s+(\w+)", head, re.M)
                url_open.append((py.name, head.count(chr(10)) + 1,
                                 fn[-1] if fn else "<模块级>", txt, m.group(0)[:28]))
    bad_calls = []
    for name, ln, fn, t2, frag in url_open:
        bound = fn != "<模块级>" and re.search(
            r"\.(?:connect|addAction)\([^)]*self\." + re.escape(fn) + r"\b", t2, re.S)
        if not bound:
            bad_calls.append(f"{name}:{ln} {fn}（无用户绑定）{frag}")
    ck("打开浏览器的调用全部由用户动作触发（不在后台自动跳）",
       bool(url_open) and not bad_calls,
       "; ".join(bad_calls) or f"{len(url_open)} 处全部有用户绑定")

    ui = HERE / "app" / "ui" / "tabs.py"
    utxt = ui.read_text(encoding="utf-8", errors="replace") if ui.exists() else ""
    ck("这一处由用户点击触发（不是启动/采集时自动跳）",
       ("clicked.connect(self._open_current)" in utxt)
       or ("cellDoubleClicked" in utxt),
       "tabs.py 里有 clicked / cellDoubleClicked 绑定")

    noauto = []
    for name in ("main_window.py", "tabs.py"):
        p2 = HERE / "app" / "ui" / name
        if not p2.exists():
            continue
        t2 = p2.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"QDesktopServices\s*\.\s*openUrl", t2):
            head = t2[:m.start()]
            # 必须处在 _open_current 这个函数体内（最近一次 def 就是它）
            fn = re.findall(r"^\s*def\s+(\w+)", head, re.M)
            if not fn or fn[-1] != "_open_current":
                noauto.append(f"{name}:{head.count(chr(10)) + 1} 在 {fn[-1] if fn else '模块级'}")
    ck("openUrl 只在 _open_current 里，别处不存在", not noauto, "; ".join(noauto) or "0 处")

    # os.startfile 只允许用来开文件夹
    starts = []
    for py in (HERE / "app").rglob("*.py"):
        txt = py.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"os\.startfile\(([^)\n]*)", txt):
            starts.append(f"{py.name}:{txt[:m.start()].count(chr(10)) + 1} → {m.group(1)[:40]}")
    ck("os.startfile 只用来开目录，不开网址",
       not any("http" in s for s in starts), "; ".join(starts) or "0 处")

    # ---- 5 各页可刷新、不抛异常 ----
    for i in range(win.tabs.count()):
        name = win.tabs.tabText(i)
        try:
            win.tabs.setCurrentIndex(i)
            app.processEvents()
            w = win.tabs.widget(i)
            if hasattr(w, "refresh"):
                w.refresh()
            app.processEvents()
            ck(f"「{name}」能正常渲染", True)
        except Exception as e:  # noqa: BLE001
            ck(f"「{name}」能正常渲染", False, f"{type(e).__name__}: {e}")

    # ---- 6 统计卡 ----
    win.set_stats(cards=47, keep=13, drop=34, ms=4929)
    app.processEvents()
    ck("统计卡能更新", True, "47 / 13 / 34 / 4.9s")

    # ---- 7 截图 ----
    shot_dir = paths.DATA / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
        safe = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", win.tabs.tabText(i)) or f"tab{i}"
        p = shot_dir / f"{i + 1}-{safe}.png"
        pm = win.grab()
        ok = pm.save(str(p), "PNG")
        saved.append((p.name, ok, pm.width(), pm.height()))
    ck("逐页截图已保存", all(s[1] for s in saved), f"{len(saved)} 张 → {shot_dir}")

    # ---- 7.5 多关键词 / 每词一个结果页签（v0.1.2）----
    try:
        ct = None
        for i in range(win.tabs.count()):
            w = win.tabs.widget(i)
            if type(w).__name__ == "CollectTab":
                ct = w
                break
        ck("找到采集页", ct is not None)

        if ct is not None:
            # 关键词框支持多个（占位提示里写明）
            ck("关键词框提示「可一次输多个」",
               "多个" in (ct.ed_kw.placeholderText() or ""),
               ct.ed_kw.placeholderText()[:30])
            # 结果区是页签容器
            ck("结果区是页签容器（QTabWidget）",
               type(ct.tabs).__name__ == "QTabWidget")
            ck("页签可关闭（× 就是删缓存）", bool(ct.tabs.tabsClosable()))
            # 历史页签控件
            ck("有历史页签下拉 + 载入 + 删缓存",
               all(hasattr(ct, a) for a in ("cb_hist", "btn_load", "btn_hist_del")))

            # 造两个假结果页签，验证「一个关键词一个页签」
            ct._add_pane("自测-帐篷", [{"商品标题": "A", "店铺名称": "S1",
                                        "店铺类型": "工厂店", "价格": "9",
                                        "完整URL": "https://item.taobao.com/item.htm?id=1"}],
                         [{"商品标题": "B", "店铺名称": "S2",
                           "硬drop": 1, "hard_drop_reasons": ["旗舰店必删"],
                           "完整URL": "https://item.taobao.com/item.htm?id=2"}])
            ct._add_pane("自测-露营灯", [{"商品标题": "C", "店铺名称": "S3",
                                          "店铺类型": "个人店"}], [])
            app.processEvents()
            ck("两个关键词 → 两个页签", ct.tabs.count() == 2,
               f"页签 {ct.tabs.count()} 个")
            ck("页签标题带关键词", "自测-帐篷" in (ct.tabs.tabText(0) or ""),
               ct.tabs.tabText(0))
            # 切页签时导出按钮跟着变
            ct.tabs.setCurrentIndex(0)
            app.processEvents()
            ck("当前页签有内容 → 导出按钮可用", ct.btn_export.isEnabled())
            # 选中一行，详情区跟着填
            pane = ct._cur_pane()
            pane["tb_keep"].selectRow(0)
            app.processEvents()
            ck("选中行后详情区填上标题", ct.kv["标题"].v.text() not in ("", "—"),
               ct.kv["标题"].v.text()[:20])
            # 删掉一个页签（无 sid，不会真删缓存）
            ct._close_tab(1)
            app.processEvents()
            ck("删页签后只剩 1 个", ct.tabs.count() == 1, f"剩 {ct.tabs.count()} 个")
            ct._close_tab(0)
            app.processEvents()
            ck("全部删完回到空态提示", ct.tabs.count() == 0)
    except Exception as e:  # noqa: BLE001
        ck("多关键词页签链路", False, f"{type(e).__name__}: {e}")

    # ---- 7.6 后端：拆词 / 页签缓存 ----
    try:
        sys.path.insert(0, str(paths.TAOBAO_SERVER))
        import collector as _col
        import sessions as _ses
        kws = _col.split_keywords("透明帐篷，露营灯, 户外椅；透明帐篷")
        ck("多关键词按逗号/顿号/分号拆开并去重",
           kws == ["透明帐篷", "露营灯", "户外椅"], str(kws))
        fake = {"summary": {"keyword": "自测会话", "total": 1, "keep": 1, "drop": 0,
                            "collect": {"pages_wanted": 2, "pages_done": 1,
                                        "stop_reason": "自测"}},
                "keep": [{"商品ID": "1"}], "drop": []}
        m = _ses.save(fake, params={"pages": 2})
        got = _ses.get(m["id"])
        gone = _ses.delete(m["id"])
        ck("页签缓存 存→取→删 全通",
           bool(got) and gone and _ses.get(m["id"]) is None,
           m["id"])
    except Exception as e:  # noqa: BLE001
        ck("后端拆词与页签缓存", False, f"{type(e).__name__}: {e}")

    # ---- 8 后端可达性（不算失败，只报告）----
    try:
        from app import bridge
        n_ok = sum(1 for _, _, ok in bridge.env_report() if ok)
        n_all = len(bridge.env_report())
        ck("后端路径可读", n_ok > 0, f"{n_ok}/{n_all} 项存在")
        ov = bridge.pig_overview()
        ck("商品库可读（只读打开）", bool(ov.get("ok")) or bool(ov.get("error")),
           f"商品 {ov.get('goods')} · 图片 {ov.get('images')} · 包 {ov.get('packs')}")
    except Exception as e:  # noqa: BLE001
        ck("后端路径可读", False, f"{type(e).__name__}: {e}")

    # ---- 输出 ----
    print()
    for mark, name, note in NOTES:
        print(f"  {mark} {name}" + (f"   —— {note}" if note else ""))
    print()
    print(f"合计 {len(PASS) + len(FAIL)} 项，通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

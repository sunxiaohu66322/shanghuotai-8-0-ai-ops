# -*- coding: utf-8 -*-
"""v3 上货流水线 · Flask 接口层

只在 app.py 末尾注册一次：
    from v3.api import register_v3
    register_v3(app)

约定：
  · 所有写操作返回 {ok, ...}；错误返回 {ok:false, error, code, retryable}
  · 统一前缀 /api/v3/flow/** 与 /api/v3/products/**，**不与既有 /api/v3/run 等冲突**
  · 图片导入路径走白名单校验，防任意文件读取
"""
from __future__ import annotations

import os
import traceback
from typing import Any

from flask import jsonify, request

from . import flow, images, importer, keywords, packaging, pack, profile as prof_mod, store, titles

ALLOWED_ROOTS = [
    r"J:\aixitong\小猪",
    r"D:\DSH_Cache",
    store.BASE_DIR,                      # 本项目目录（taobao-picker）
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Pictures"),
    os.path.expanduser("~/Downloads"),
]
# Windows 上 realpath 会把 ~ 展开成 C:\Users\...，但中文目录名可能大小写/短名不同，
# 故再补一条基于 normcase 的等价比较（见 _safe_path）


def _err(e: Exception, code: int = 500) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "trace": traceback.format_exc(limit=3)}), code


def _body() -> dict:
    return request.get_json(force=True, silent=True) or {}


def _res(d, ok_code: int = 200):
    """业务 ok=False 时给出合适的状态码（404=对象不存在，400=参数/状态不对）。"""
    if isinstance(d, dict) and not d.get("ok"):
        e = str(d.get("error") or "")
        code = 404 if ("没有这个商品" in e or "没有这条" in e or "没有这张图" in e
                       or "没有这个任务" in e) else 400
        return jsonify(d), code
    return jsonify(d), ok_code


def _safe_path(p: str) -> tuple[str, str]:
    """返回 (realpath, '') 或 ('', 错误原因)。"""
    if not p:
        return "", "路径是空的"
    rp = os.path.normcase(os.path.realpath(p))
    for root in ALLOWED_ROOTS:
        try:
            rr = os.path.normcase(os.path.realpath(root))
        except Exception:
            continue
        if rp == rr or rp.startswith(rr + os.sep):
            return os.path.realpath(p), ""
    return "", ("路径不在允许范围（只允许 小猪目录 / D:\\DSH_Cache / 本项目 / 桌面 / 图片 / 下载）：%s" % p)


def register_v3(app) -> None:
    # ------------------------------------------------------------ 概览 / 元数据

    @app.route("/api/v3/flow/overview")
    def v3_overview():
        c = flow.conn()
        try:
            return jsonify(flow.overview(c))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/sources")
    def v3_sources():
        try:
            return jsonify({"ok": True, **importer.scan_sources()})
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/flow/keywords/build", methods=["POST"])
    def v3_kw_build():
        c = flow.conn()
        try:
            return jsonify(keywords.build(c))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/keywords")
    def v3_kw_list():
        c = flow.conn()
        try:
            kinds = (request.args.get("kinds") or "core,attribute,scene,synonym").split(",")
            cat = request.args.get("category") or ""
            return jsonify({"ok": True, "stats": keywords.stats(c),
                            "items": keywords.terms(c, kinds, cat, limit=300)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 词库豁免

    @app.route("/api/v3/flow/keywords/waivers")
    def v3_kw_waivers():
        c = flow.conn()
        try:
            return jsonify({"ok": True, "items": keywords.waivers(c)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/keywords/waive", methods=["POST"])
    def v3_kw_waive():
        """人判定「这不是违规词」→ 记台账 + 置 disabled + 可顺带重筛。"""
        d = _body()
        words = [str(x) for x in (d.get("words") or []) if str(x).strip()]
        if not words:
            return jsonify({"ok": False, "error": "words 不能空"}), 400
        c = flow.conn()
        try:
            r = keywords.waive(c, words, reason=(d.get("reason") or ""),
                               actor=d.get("actor") or "人", hits=int(d.get("hits") or 0))
            if d.get("pids"):
                r["rescreen"] = flow.fine_screen(c, d.get("pids"),
                                                 actor="词库豁免后重筛")
            return jsonify(r)
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/keywords/unwaive", methods=["POST"])
    def v3_kw_unwaive():
        d = _body()
        c = flow.conn()
        try:
            r = keywords.unwaive(c, d.get("word") or "", actor=d.get("actor") or "人")
            if r.get("ok") and d.get("pids"):
                r["rescreen"] = flow.fine_screen(c, d.get("pids"),
                                                 actor="撤销豁免后重筛")
            return _res(r)
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 导入

    @app.route("/api/v3/flow/import", methods=["POST"])
    def v3_import():
        d = _body()
        src = (d.get("source") or "pig").strip()
        pack = (d.get("pack") or "").strip()
        path = (d.get("path") or "").strip()
        try:
            if src == "pig":
                rep = importer.import_pig(pack=pack, limit=int(d.get("limit") or 0))
            elif src == "taobao":
                rep = importer.import_taobao(keyword=pack,
                                             limit=int(d.get("limit") or 0))
            elif src == "csv":
                rp, why = _safe_path(path)
                if why:
                    return jsonify({"ok": False, "error": why}), 400
                rep = importer.import_csv(rp, pack_code=pack)
            elif src == "json":
                rp, why = _safe_path(path)
                if why:
                    return jsonify({"ok": False, "error": why}), 400
                rep = importer.import_json(rp, pack_code=pack)
            else:
                return jsonify({"ok": False, "error": "source 只能是 pig|taobao|csv|json"}), 400
        except Exception as e:  # noqa: BLE001
            return _err(e)
        if rep.get("imported") is None:
            return jsonify({"ok": False, "error": rep.get("error") or "导入失败"}), 400
        return jsonify({"ok": True, **dict(rep), "lines": rep.lines})

    # ------------------------------------------------------------ 数据包（第二步）
    # 一个「数据包」= 一份导入文件 + 它在库里的商品。第三步检测打码只认这里的包。

    @app.route("/api/v3/pack/list")
    def v3_pack_list():
        try:
            return jsonify({"ok": True, "items": pack.list_packs(),
                            "inbox": pack.list_inbox(),
                            "inbox_dir": pack.ensure_dir()})
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/pack/preview", methods=["POST"])
    def v3_pack_preview():
        d = _body()
        rp, why = _safe_path((d.get("path") or "").strip())
        if why:
            return jsonify({"ok": False, "error": why}), 400
        return jsonify(pack.preview(rp))

    @app.route("/api/v3/pack/convert", methods=["POST"])
    def v3_pack_convert():
        d = _body()
        rp, why = _safe_path((d.get("path") or "").strip())
        if why:
            return jsonify({"ok": False, "error": why}), 400
        try:
            r = pack.convert(rp, pack_code=(d.get("pack_code") or "").strip(),
                             actor=d.get("actor") or "上货台导入")
            return _res(r)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/pack/status")
    def v3_pack_status():
        try:
            return _res(pack.status(request.args.get("pack_code") or request.args.get("code") or ""))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/pack/rename", methods=["POST"])
    def v3_pack_rename():
        d = _body()
        try:
            return _res(pack.rename((d.get("pack_code") or d.get("code") or "").strip(),
                                    d.get("display_name") or d.get("name") or ""))
        except Exception as e:                     # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/pack/impact", methods=["GET", "POST"])
    def v3_pack_impact():
        code = (request.args.get("pack_code") or (request.json or {}).get("pack_code") or "").strip()
        purge = str(request.args.get("purge", "0")).lower() in ("1", "true", "yes")
        try:
            from . import safedelete
            from .store import connect
            c = connect()
            try:
                return _res(safedelete.impact(c, code, purge_products=purge))
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @app.route("/api/v3/pack/delete", methods=["POST"])
    def v3_pack_delete():
        d = _body()
        try:
            # ★ 2026-10-24：默认安全（只删包与成员），彻底删商品必须显式传 purge
            return _res(pack.delete_pack((d.get("pack_code") or d.get("code") or "").strip(),
                                         purge_products=bool(d.get("purge", False)),
                                         purge_shared=bool(d.get("purge_shared", False))))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    # ------------------------------------------------------------ 阶段批跑

    @app.route("/api/v3/flow/chain", methods=["POST"])
    def v3_chain():
        d = _body()
        pids = d.get("pids") or None
        c = flow.conn()
        try:
            return jsonify(flow.run_chain(c, pids, store_id=d.get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/fine-screen", methods=["POST"])
    def v3_fine():
        c = flow.conn()
        try:
            return jsonify(flow.fine_screen(c, _body().get("pids") or None))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/profile", methods=["POST"])
    def v3_profile_all():
        c = flow.conn()
        try:
            return jsonify(flow.profile_all(c, _body().get("pids") or None))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/title", methods=["POST"])
    def v3_title_all():
        d = _body()
        c = flow.conn()
        try:
            return jsonify(flow.title_all(c, d.get("pids") or None,
                                          store_id=d.get("store_id") or "default",
                                          count=int(d.get("count") or 3),
                                          use_ai=d.get("use_ai")))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/images/status")
    def v3_img_status():
        c = flow.conn()
        try:
            return jsonify({"ok": True, **flow.image_status(c)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 商品

    @app.route("/api/v3/products")
    def v3_products():
        c = flow.conn()
        try:
            items = store.list_products(c, status=request.args.get("status") or None,
                                        q=request.args.get("q") or None,
                                        pack=request.args.get("pack") or None,
                                        limit=int(request.args.get("limit") or 300),
                                        offset=int(request.args.get("offset") or 0))
            return jsonify({"ok": True, "count": len(items), "items": items})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>")
    def v3_product(pid: int):
        c = flow.conn()
        try:
            p = store.get_product(c, pid)
            if not p:
                return jsonify({"ok": False, "error": "没有这个商品"}), 404
            return jsonify({"ok": True, "product": p,
                            "images": images.list_images(c, pid),
                            "variants": titles.list_variants(c, pid)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/profile/refresh", methods=["POST"])
    def v3_profile_refresh(pid: int):
        c = flow.conn()
        try:
            return _res(prof_mod.refresh(c, pid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/profile/confirm", methods=["POST"])
    def v3_profile_confirm(pid: int):
        c = flow.conn()
        try:
            return _res(prof_mod.confirm(c, pid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 核心词（人工）

    @app.route("/api/v3/products/<int:pid>/core/suggest")
    def v3_core_suggest(pid: int):
        c = flow.conn()
        try:
            return _res(prof_mod.suggest_core(c, pid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/core", methods=["POST"])
    def v3_core_set(pid: int):
        c = flow.conn()
        try:
            return _res(prof_mod.set_core(c, pid, _body().get("word") or ""))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/release", methods=["POST"])
    def v3_release(pid: int):
        """人工放行：把 blocked 的商品放回可流转状态（唯一能解开 blocked 的地方）。"""
        c = flow.conn()
        try:
            ok = store.set_product_status(c, pid, "imported", force=True)
            c.commit()
            return jsonify({"ok": ok, "product_id": pid, "status": "imported",
                            "note": "" if ok else "没改成（商品不存在）"})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 标题

    @app.route("/api/v3/products/<int:pid>/title-variants")
    def v3_tv_list(pid: int):
        c = flow.conn()
        try:
            return jsonify({"ok": True, "items": titles.list_variants(
                c, pid, request.args.get("store_id") or None)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/title-variants/generate", methods=["POST"])
    def v3_tv_gen(pid: int):
        d = _body()
        c = flow.conn()
        try:
            return _res(titles.generate(
                c, pid, store_id=d.get("store_id") or "default",
                count=int(d.get("count") or 3),
                exclude_previous=bool(d.get("exclude_previous", True)),
                strategy_mix=d.get("strategy_mix"),
                use_ai=d.get("use_ai")))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/title-variants/manual", methods=["POST"])
    def v3_tv_manual(pid: int):
        d = _body()
        c = flow.conn()
        try:
            return _res(titles.manual_edit(c, pid, d.get("title_text") or "",
                                           store_id=d.get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/title-variants/<int:vid>/select", methods=["POST"])
    def v3_tv_select(vid: int):
        c = flow.conn()
        try:
            return _res(titles.select(c, vid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/title-variants/<int:vid>/reject", methods=["POST"])
    def v3_tv_reject(vid: int):
        c = flow.conn()
        try:
            return _res(titles.reject(c, vid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/copy-title")
    def v3_copy_title(pid: int):
        c = flow.conn()
        try:
            return _res(titles.copy_text(c, pid, request.args.get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 图片 / 交接

    @app.route("/api/v3/products/<int:pid>/images")
    def v3_images_list(pid: int):
        c = flow.conn()
        try:
            return jsonify({"ok": True, "items": images.list_images(c, pid)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/images/<int:iid>/download", methods=["POST"])
    def v3_img_download(pid: int, iid: int):
        c = flow.conn()
        try:
            return _res(images.download_image(c, iid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/images/main/import", methods=["POST"])
    def v3_img_import(pid: int):
        d = _body()
        rp, why = _safe_path(d.get("path") or "")
        if why:
            return jsonify({"ok": False, "error": why}), 400
        c = flow.conn()
        try:
            return _res(images.import_new(c, pid, rp, note=d.get("note") or ""))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/images/main/approve", methods=["POST"])
    def v3_img_approve(pid: int):
        d = _body()
        c = flow.conn()
        try:
            return _res(images.approve(c, pid, int(d.get("image_id") or 0)))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/images/main/revert", methods=["POST"])
    def v3_img_revert(pid: int):
        c = flow.conn()
        try:
            return _res(images.revert(c, pid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/products/<int:pid>/handoff")
    def v3_handoff(pid: int):
        c = flow.conn()
        try:
            return _res(images.handoff(c, pid, request.args.get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 出包 / 台账

    @app.route("/api/v3/products/<int:pid>/package", methods=["POST"])
    def v3_pkg_one(pid: int):
        c = flow.conn()
        try:
            return _res(packaging.export_package(c, [pid],
                                                 store_id=_body().get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/packages", methods=["GET", "POST"])
    def v3_packages():
        if request.method == "GET":
            return jsonify({"ok": True, "items": packaging.list_out_files()})
        d = _body()
        c = flow.conn()
        try:
            return jsonify(packaging.export_package(c, d.get("pids") or None,
                                                    store_id=d.get("store_id") or "default"))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/publish/record", methods=["POST"])
    def v3_pub_record():
        d = _body()
        c = flow.conn()
        try:
            return jsonify(packaging.record_publish(
                c, int(d.get("product_id") or 0),
                store_id=d.get("store_id") or "default",
                outcome=d.get("outcome") or "unknown",
                external_listing_id=d.get("external_listing_id") or "",
                note=d.get("note") or ""))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/publish")
    def v3_pub_list():
        c = flow.conn()
        try:
            return jsonify({"ok": True, "items": packaging.list_publish(c)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    # ------------------------------------------------------------ 任务 / 店铺 / 策略

    @app.route("/api/v3/jobs")
    def v3_jobs():
        c = flow.conn()
        try:
            return jsonify({"ok": True, "items": flow.jobs(c)})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/jobs/<jid>")
    def v3_job_one(jid: str):
        c = flow.conn()
        try:
            j = store.job_get(c, jid)
            if not j:
                return jsonify({"ok": False, "error": "没有这个任务"}), 404
            return jsonify({"ok": True, "job": j})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/jobs/<jid>/retry", methods=["POST"])
    def v3_job_retry(jid: str):
        c = flow.conn()
        try:
            return _res(store.job_retry(c, jid))
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/stores", methods=["GET", "POST"])
    def v3_stores():
        c = flow.conn()
        try:
            if request.method == "POST":
                d = _body()
                sid = (d.get("store_id") or "").strip()
                if not sid:
                    return jsonify({"ok": False, "error": "store_id 不能空"}), 400
                c.execute("INSERT INTO v3_store(store_id, platform, display_name,"
                          " title_policy_id, enabled, created_at, updated_at)"
                          " VALUES(?,?,?,?,1,?,?)"
                          " ON CONFLICT(store_id) DO UPDATE SET display_name=excluded.display_name,"
                          " platform=excluded.platform, title_policy_id=excluded.title_policy_id,"
                          " enabled=excluded.enabled, updated_at=excluded.updated_at",
                          (sid, d.get("platform") or "wechat",
                           d.get("display_name") or sid,
                           d.get("title_policy_id") or "wechat-default",
                           store.now(), store.now()))
                c.commit()
            items = [dict(r) for r in c.execute("SELECT * FROM v3_store ORDER BY store_id")]
            return jsonify({"ok": True, "items": items})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/title-policy", methods=["GET", "POST"])
    def v3_policy():
        c = flow.conn()
        try:
            if request.method == "POST":
                d = _body()
                pid = d.get("policy_id") or "wechat-default"
                c.execute("INSERT INTO v3_title_policy(policy_id, platform, min_len, max_len,"
                          " min_cjk, source, note, updated_at) VALUES(?,?,?,?,?,?,?,?)"
                          " ON CONFLICT(policy_id) DO UPDATE SET min_len=excluded.min_len,"
                          " max_len=excluded.max_len, min_cjk=excluded.min_cjk,"
                          " source=excluded.source, note=excluded.note,"
                          " updated_at=excluded.updated_at",
                          (pid, d.get("platform") or "wechat",
                           int(d.get("min_len") or 8), int(d.get("max_len") or 60),
                           int(d.get("min_cjk") or 6),
                           d.get("source") or "", d.get("note") or "", store.now()))
                c.commit()
            items = [dict(r) for r in c.execute("SELECT * FROM v3_title_policy")]
            return jsonify({"ok": True, "items": items,
                            "ai": titles.ai_status()})
        except Exception as e:  # noqa: BLE001
            return _err(e)
        finally:
            c.close()

    @app.route("/api/v3/flow/backup", methods=["POST"])
    def v3_backup():
        try:
            p = store.backup("api")
            return jsonify({"ok": bool(p), "path": p})
        except Exception as e:  # noqa: BLE001
            return _err(e)

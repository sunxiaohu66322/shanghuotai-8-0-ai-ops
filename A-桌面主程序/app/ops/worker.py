# -*- coding: utf-8 -*-
"""P0 探针 worker：只跑 probe.* 三种「不碰平台、不碰业务」的任务。

它存在的目的不是干活，是让队列的四种真实状态转移（成功/失败/退避/租约过期）
能在没有任何外部依赖的情况下被**真跑**出来。P1 的业务 kind 从这里注册进来。
"""
from __future__ import annotations

import json
import time
import uuid

from . import queue


def _sleep_with_heartbeat(con, task_id, worker, seconds, lease_s):
    """长任务：每 lease/3 秒续租一次，续不上说明租约被别人接管，立刻停。"""
    end = time.time() + seconds
    step = max(1.0, lease_s / 3.0)
    while time.time() < end:
        time.sleep(min(step, max(0.0, end - time.time())))
        if not queue.heartbeat(con, task_id, worker, lease_s):
            return False
    return True


def run_one(task: dict, worker: str, lease_s: int) -> tuple:
    """执行一个任务，返回 (ok, result_dict, error_str)。"""
    con = queue.connect()
    try:
        kind = task["kind"]
        payload = json.loads(task.get("payload") or "{}")
        if kind == "probe.noop":
            return True, {"echo": payload, "at": queue.now_text()}, ""
        if kind == "probe.sleep":
            secs = min(float(payload.get("seconds", 5)), 120.0)
            alive = _sleep_with_heartbeat(con, task["task_id"], worker, secs, lease_s)
            if not alive:
                return False, {}, "lease lost during sleep"
            return True, {"slept": secs}, ""
        if kind == "probe.fail":
            return False, {}, str(payload.get("error") or "probe.fail 按设计失败")
        return False, {}, "P0 未实现的 kind: %s（只有 probe.* 三种）" % kind
    finally:
        con.close()


STATS = {"alive": False, "claimed": 0, "done": 0, "failed": 0, "loops": 0,
         "last_error": None, "last_at": None, "worker": None}


def worker_loop(ttl_lease: int, stop_flag):
    """单线程 worker：领一条 → 跑 → 写终态。**任何异常都不许让线程静默死掉。**"""
    worker = "w-" + uuid.uuid4().hex[:8]
    STATS.update(alive=True, worker=worker)
    while not stop_flag.is_set():
        STATS["loops"] += 1
        try:
            con = queue.connect()
            try:
                task = queue.claim(con, worker, lease_s=ttl_lease,
                                   kinds=["probe.noop", "probe.sleep", "probe.fail"])
            finally:
                con.close()
            if not task:
                time.sleep(0.4)
                continue
            STATS["claimed"] += 1
            STATS["last_at"] = queue.now_text()
            ok, result, err = run_one(task, worker, ttl_lease)
            con = queue.connect()
            try:
                if ok:
                    queue.complete(con, task["task_id"], worker, result)
                    STATS["done"] += 1
                else:
                    queue.fail(con, task["task_id"], worker, err)
                    STATS["failed"] += 1
            finally:
                con.close()
        except BaseException as e:                            # noqa: BLE001
            import traceback
            STATS["last_error"] = "%s: %s" % (type(e).__name__, e)
            traceback.print_exc()
            time.sleep(0.5)
    STATS["alive"] = False

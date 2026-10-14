"""proactive 测试隔离护栏——PROACTIVE_DB 全测试隔离（proactive 测试隔离，TL-① 同族）。

修前（2026-10-14 只读清点非推断）：生产 data/proactive.db 29,900 行、数百行/小时
测试 burst——任何 TestClient(src.main) 触发 lifespan 心跳（register_default_watchers
重置 last_run_at=0 → 全部 watcher 立即到期 → 首 tick 全量跑）把测试事件行写进
生产 proactive.db（test_a2a_card_shape +4 复现，burst 特征=同秒多条 memory_decay）。
本文件锁死拆件隔离边界：

- _default_events_db() 缺省路径跟 PROACTIVE_DB env（TIMELINE_DB/NEST_DB 先例）
- run_watcher 真写 env tmp 库、生产库零测试行（哨兵 watcher 名硬证）
- 模块单例 proactive_engine 每用例被 conftest retarget（db_path/_db_ready/_events_memory）
- lifespan 真实链路（TestClient(src.main) → register → start → 首 tick →
  run_watcher → _record_event）的 burst 落 env tmp、生产零行

生产库只读探针（file:...?mode=ro URI）绝不写。
"""

import asyncio
import os
import sqlite3
import time
import uuid
from pathlib import Path

from src.will import proactive as proactive_mod

PROD_DB = str(Path(proactive_mod.__file__).resolve().parents[2] / "data" / "proactive.db")


def _marker() -> str:
    return f"isolation-guard-{uuid.uuid4().hex}"


def _count_watcher(db_path: str, watcher: str) -> int:
    """只读数某库中指定 watcher 名的事件行数（库/表不存在=0）。"""
    p = Path(db_path)
    if not p.exists():
        return 0
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM proactive_events WHERE watcher = ?", (watcher,)
        ).fetchone()[0]
    finally:
        con.close()


# ── 路径解析单一源 ───────────────────────────────────────────


def test_default_events_db_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROACTIVE_DB", str(tmp_path / "p.db"))
    assert proactive_mod._default_events_db() == str(tmp_path / "p.db")


def test_default_events_db_without_env_is_repo_data(monkeypatch):
    # 纯函数断言（不构造引擎=不接触生产库文件）
    monkeypatch.delenv("PROACTIVE_DB", raising=False)
    assert proactive_mod._default_events_db() == PROD_DB


def test_constructor_resolves_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROACTIVE_DB", str(tmp_path / "p.db"))
    eng = proactive_mod.ProactiveEngine()
    assert eng.db_path == str(tmp_path / "p.db")
    assert eng.db_path != PROD_DB


# ── 模块单例每用例 retarget（conftest _isolate_proactive_db）───


def test_singleton_retargeted_per_test(tmp_path):
    eng = proactive_mod.proactive_engine
    assert eng.db_path == os.environ["PROACTIVE_DB"] == str(tmp_path / "proactive.db")
    assert eng.db_path != PROD_DB
    assert eng._db_ready is False


def test_events_memory_reset_per_test():
    # 用例开始时内存环已被 conftest 复位（禁跨用例陈旧环）
    assert proactive_mod.proactive_engine._events_memory == []


# ── 真写路径：env tmp 库落行、生产库零测试行 ─────────────────


def test_run_watcher_records_env_tmp_not_production():
    watcher = _marker()
    eng = proactive_mod.proactive_engine
    eng.register_watcher(watcher, lambda: {"probe": watcher}, interval_seconds=3600)
    res = asyncio.run(eng.run_watcher(watcher))
    assert res["ok"] is True
    assert _count_watcher(os.environ["PROACTIVE_DB"], watcher) == 1
    assert _count_watcher(PROD_DB, watcher) == 0


# ── lifespan 真实 burst 链路（缺陷发现路径复现）────────────────


def test_lifespan_burst_writes_env_tmp_not_production(monkeypatch):
    """复现缺陷发现路径：TestClient(src.main) → lifespan → register → start →
    首 tick 全量跑到期 watcher → run_watcher → _record_event。

    行为回调用探针替身（防测试执行生产行为面：memory_decay/experience_collect
    等的依赖是生产单例，行为本体有各自 tmp 隔离测面）——引擎/生命周期/写库链路
    全真实：main.py lifespan 真调用 register_default_watchers（call-time import，
    monkeypatch 生效）→ start → 首 tick（last_run_at=0 全部到期）→ 真 _record_event。
    """
    from fastapi.testclient import TestClient

    import src.will.proactive_behaviors as behaviors_mod
    from src.main import app

    watcher = _marker()

    def probe_register(engine):
        engine.register_watcher(watcher, lambda: {"probe": watcher}, interval_seconds=3600)
        return [watcher]

    monkeypatch.setattr(behaviors_mod, "register_default_watchers", probe_register)

    with TestClient(app):
        # 首 tick 在 start() 后即跑；轮询等 burst 落库（≤10s，活体轮询非定时断言）
        deadline = time.time() + 10
        while time.time() < deadline and _count_watcher(os.environ["PROACTIVE_DB"], watcher) < 1:
            time.sleep(0.1)

    assert _count_watcher(os.environ["PROACTIVE_DB"], watcher) == 1
    assert _count_watcher(PROD_DB, watcher) == 0

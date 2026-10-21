"""RV-①b-① gland TokenMeter 用量记录持久化——写穿/回载/聚合单一源/真实路径接线。

修前（RV-① A 组定性）：TokenMeter 全在进程内存，record() 只 append——gland 用量
UI 真源（/api/gland/usage /usage/recent /stats 消费的 token_meter）随 service 重启
归零。本文件锁死：①record() 写穿落盘行逐字段 ②重启回载聚合逐键一致（聚合=行的
派生值，MEM-⑥ 纪律）③persist=None 纯内存既有语义零变化 ④写失败不阻断但
logger.error 可见 ⑤生产真实路径（ModelRouter(persist_usage=True)→api/gland 单例）
真接线。
"""

from __future__ import annotations

import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT provider, model, prompt_tokens, completion_tokens, total_tokens,"
            " user_id, task, timestamp FROM gland_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _meter(tmp_path, **kw):
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    return TokenMeter(persist=UsagePersistence(str(tmp_path / "usage.db")), **kw)


# ── ① 写穿（record() → DB 行）────────────────────────────────────


def test_record_write_through_row_verbatim(tmp_path):
    """record() 随写落盘：DB 行与 UsageRecord 逐字段一致（含 None 轴）。"""
    m = _meter(tmp_path)
    m.record("prov-x", "model-x", 10, 5, user_id="user-1", task="chat")
    (row,) = _rows(tmp_path / "usage.db")
    assert row[:7] == ("prov-x", "model-x", 10, 5, 15, "user-1", "chat")
    assert isinstance(row[7], float) and row[7] > 0


def test_write_through_null_user_task_roundtrip(tmp_path):
    """user_id/task 未提供=None → DB NULL → 回载 None（键在场值如实，禁硬造）。"""
    m = _meter(tmp_path)
    m.record("prov-x", "model-x", 3, 2)
    (row,) = _rows(tmp_path / "usage.db")
    assert row[5] is None and row[6] is None
    m2 = _meter(tmp_path)
    (rec,) = m2.recent_records(1)
    assert rec["user_id"] is None and rec["task"] is None


# ── ② 重启回载（聚合=行的派生值，MEM-⑥ 纪律）────────────────────


def test_restart_reload_summary_parity(tmp_path):
    """重启（新实例回载）后 summary() 逐键==写入实例——聚合从行真源折叠重算。"""
    m = _meter(tmp_path)
    m.record("prov-a", "m1", 10, 5, user_id="u1", task="chat")
    m.record("prov-a", "m2", 1, 1, task="embedding")
    m.record("prov-b", "m1", 7, 3, user_id="u2")
    before = m.summary()

    m2 = _meter(tmp_path)  # = service 重启后的新实例
    after = m2.summary()
    assert after == before
    assert after["total_tokens"] == 27
    assert after["call_count"] == 3
    assert after["by_model"] == {"m1": 25, "m2": 2}
    assert after["by_user"] == {"u1": 15, "u2": 10}
    assert after["by_provider"]["prov-a"] == {
        "prompt": 11,
        "completion": 6,
        "total": 17,
        "requests": 2,
    }


def test_restart_reload_recent_records_parity(tmp_path):
    """recent_records 回载后逐字段==写入实例（尾切语义=落盘序）。"""
    m = _meter(tmp_path)
    m.record("prov-a", "m1", 1, 1, user_id="u1", task="chat")
    m.record("prov-b", "m2", 2, 2)
    before = m.recent_records(50)

    m2 = _meter(tmp_path)
    assert m2.recent_records(50) == before
    assert [r["provider"] for r in m2.recent_records(1)] == ["prov-b"]


# ── ③ persist=None 纯内存既有语义零变化 ─────────────────────────


def test_default_meter_touches_no_db(tmp_path, monkeypatch):
    """TokenMeter() 缺省纯内存：连 env 指到 tmp 也不建库（既有语义零变化硬证）。"""
    from src.gland.token_meter import TokenMeter

    monkeypatch.setenv("GLAND_USAGE_DB", str(tmp_path / "never.db"))
    m = TokenMeter()
    m.record("prov-x", "m", 1, 1)
    assert not (tmp_path / "never.db").exists()
    assert m.summary()["call_count"] == 1


def test_modelrouter_persist_flag_defaults_off():
    """ModelRouter() 缺省 persist_usage=False（一次性/测试用路由实例零行为变化）；
    persist_usage=True 才挂持久化。"""
    from src.gland.router import ModelRouter

    assert ModelRouter().token_meter._persist is None
    assert ModelRouter(persist_usage=True).token_meter._persist is not None


# ── ④ 写失败可见不阻断（F4b-㉕ 记账写点先例）────────────────────


def test_persist_failure_visible_not_blocking(tmp_path, caplog):
    """落盘失败：record() 照常返回+内存聚合完整，logger.error 可见（禁静默吞）。"""
    from src.gland.token_meter import TokenMeter

    class _Boom:
        def load_all(self):
            return []

        def insert(self, **_kw):
            raise RuntimeError("disk on fire")

    m = TokenMeter(persist=_Boom())  # type: ignore[arg-type]  # duck-typed 故障桩
    with caplog.at_level("ERROR"):
        rec = m.record("prov-x", "m", 10, 5)
    assert rec.total_tokens == 15
    assert m.summary()["call_count"] == 1
    assert m.summary()["total_tokens"] == 15
    assert any("persist failed" in r.message for r in caplog.records)


def test_reload_failure_visible_not_blocking(caplog):
    """回载失败：构造不炸（gateway import 不因记账面故障起不来），空账起步+可见。"""
    from src.gland.token_meter import TokenMeter

    class _BoomLoad:
        def load_all(self):
            raise RuntimeError("db corrupt")

        def insert(self, **_kw):
            pass

    with caplog.at_level("ERROR"):
        m = TokenMeter(persist=_BoomLoad())  # type: ignore[arg-type]
    assert m.summary()["call_count"] == 0
    assert any("reload failed" in r.message for r in caplog.records)


# ── ⑤ 库路径/表幂等 ─────────────────────────────────────────────


def test_default_db_path_env_override(monkeypatch, tmp_path):
    from src.gland.usage_persist import default_db_path

    monkeypatch.setenv("GLAND_USAGE_DB", str(tmp_path / "x.db"))
    assert default_db_path() == str(tmp_path / "x.db")
    monkeypatch.delenv("GLAND_USAGE_DB")
    assert default_db_path().endswith("data/gland_usage.db")


def test_two_instances_share_table_idempotent(tmp_path):
    """同库双实例：DDL 幂等、各写各行、回载全量（落盘序）。"""
    from src.gland.usage_persist import UsagePersistence

    p1 = UsagePersistence(str(tmp_path / "shared.db"))
    p2 = UsagePersistence(str(tmp_path / "shared.db"))
    p1.insert(
        provider="a",
        model="m",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        timestamp=1.0,
    )
    p2.insert(
        provider="b",
        model="m",
        prompt_tokens=3,
        completion_tokens=4,
        total_tokens=7,
        timestamp=2.0,
    )
    rows = p1.load_all()
    assert [r["provider"] for r in rows] == ["a", "b"]  # 按落盘序=时间序
    assert len(p2.load_all()) == 2


# ── ⑥ 真实路径接线（写了≠接线了）────────────────────────────────


def test_gateway_singleton_persist_wired(tmp_path):
    """生产真实路径：api/gland gateway 单例（UI 真源）真挂持久化。"""
    from src.api import gland

    assert gland.gateway.token_meter._persist is not None


def test_router_record_lands_in_db_via_real_call_site(tmp_path, monkeypatch):
    """ModelRouter(persist_usage=True) 真调用点 record()（chat stub 上游）真落盘。"""
    import asyncio

    from src.gland.router import ModelRouter

    monkeypatch.setenv("GLAND_USAGE_DB", str(tmp_path / "r.db"))
    m = ModelRouter(persist_usage=True)
    m.add_provider("prov-x", "http://stub.invalid/v1", {"chat": "m-chat"})
    m.key_manager.add_key("prov-x", "sk-test")

    async def _fake_call_chat(provider, api_key, model, messages, **kwargs):
        return {
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "choices": [{"message": {"content": "ok"}}],
        }

    m._call_chat = _fake_call_chat  # type: ignore[assignment]
    asyncio.run(m.chat([{"role": "user", "content": "hi"}]))

    (row,) = _rows(tmp_path / "r.db")
    assert row[:5] == ("prov-x", "m-chat", 7, 3, 10)
    assert row[6] == "chat"

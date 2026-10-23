"""GR-①b-① TokenMeter._records 内存窗 + 计数真源化——窗=纯缓存零可观测变化、
计数恒对账行聚合（MEM-⑥）、预算判定读真源。

修前（GR-① 定性「事件流型真无界」）：_records 每 LLM 调用 append 一行永不裁剪、
启动回载 load_all 全量进内存、call_count=len(_records) 计数与展示列表耦合——长跑
进程内存无界增长且与自身 DB 真源（gland_usage，RV-①b-① 已落）不对称。本文件
锁死：①persist 在场才裁窗（无真源不裁=不丢唯一副本）②读面输出与裁窗前逐字一致
（recent_records 回落行真源、计数恒全表聚合=窗不漏计）③计数真源毒化硬反证
（内存缓存投毒读面仍读行聚合）④对账失败回落缓存禁静默假 0 ⑤预算判定读真源
（窗外用量照样计入预算，禁裁窗变相放大预算）⑥纯内存既有语义字节不变。
"""

from __future__ import annotations

import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT provider, model, prompt_tokens, completion_tokens, total_tokens,"
            " user_id, task FROM gland_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _meter(tmp_path, window=None, **kw):
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    m = TokenMeter(persist=UsagePersistence(str(tmp_path / "usage.db")), **kw)
    if window is not None:
        m.records_window = window
    return m


def _seed(m, n, per=10):
    for i in range(n):
        m.record("prov-x", f"m{i % 2}", per, 0, user_id=f"u{i}", task=f"t{i}")


# ── ① 窗裁剪（persist 在场）+ 行真源不受影响 ─────────────────────


def test_window_trims_memory_but_rows_stay_in_db(tmp_path):
    """窗=3 写 5 条：内存窗只剩最近 3 条，DB 行真源恒 5 行（窗外行没丢）。"""
    m = _meter(tmp_path, window=3)
    _seed(m, 5)
    assert len(m._records) == 3
    assert [r.task for r in m._records] == ["t2", "t3", "t4"]
    assert len(_rows(tmp_path / "usage.db")) == 5


def test_no_trim_without_persist_pure_memory(tmp_path, monkeypatch):
    """persist=None 无真源可回落：裁=丢唯一副本——不裁（纯内存既有语义字节不变）。"""
    from src.gland.token_meter import TokenMeter

    monkeypatch.setenv("GLAND_USAGE_DB", str(tmp_path / "never.db"))
    m = TokenMeter()
    m.records_window = 2
    _seed(m, 5)
    assert len(m._records) == 5
    assert not (tmp_path / "never.db").exists()
    assert m.summary()["call_count"] == 5


def test_window_zero_or_negative_means_no_trim(tmp_path):
    """records_window<=0=不裁窗（缓存退化全量，如实语义非静默截断）。"""
    m = _meter(tmp_path, window=0)
    _seed(m, 4)
    assert len(m._records) == 4


# ── ② 读面零可观测变化（窗=纯缓存，真源=全表）──────────────────


def test_recent_records_falls_back_to_rows_beyond_window(tmp_path):
    """limit 超窗=回落行真源：逐字段==落盘序全量（与裁窗前语义逐字一致）。"""
    m = _meter(tmp_path, window=2)
    _seed(m, 5)
    out = m.recent_records(5)
    assert [r["task"] for r in out] == ["t0", "t1", "t2", "t3", "t4"]
    assert [r["user_id"] for r in out] == ["u0", "u1", "u2", "u3", "u4"]
    assert all(r["provider"] == "prov-x" and r["prompt_tokens"] == 10 for r in out)
    assert set(out[0]) == {
        "provider",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "user_id",
        "task",
        "timestamp",
    }


def test_recent_records_tail_semantics_within_window(tmp_path):
    """limit 在窗内=内存快路径，尾切语义与回落面一致（同一读面两路径同输出）。"""
    m = _meter(tmp_path, window=4)
    _seed(m, 5)
    fast = m.recent_records(3)
    assert [r["task"] for r in fast] == ["t2", "t3", "t4"]
    # 同 limit 从回落面取（窗=2 时 3>2 走真源）输出相同
    m2 = _meter(tmp_path, window=2)
    assert [r["task"] for r in m2.recent_records(3)] == ["t2", "t3", "t4"]


def test_summary_counts_not_affected_by_window(tmp_path):
    """计数恒全表聚合：窗=2 时 call_count/总量/by_* 照算全部 5 行（窗不漏计）。"""
    m = _meter(tmp_path, window=2)
    _seed(m, 5)
    s = m.summary()
    assert s["call_count"] == 5
    assert s["total_tokens"] == 50
    assert s["total_prompt_tokens"] == 50
    assert s["total_completion_tokens"] == 0
    assert s["by_model"] == {"m0": 30, "m1": 20}
    assert s["by_user"] == {"u0": 10, "u1": 10, "u2": 10, "u3": 10, "u4": 10}
    assert s["by_provider"]["prov-x"] == {
        "prompt": 50,
        "completion": 0,
        "total": 50,
        "requests": 5,
    }


def test_restart_window_backfill_reads_full_faces(tmp_path, monkeypatch):
    """重启回载只装窗（len==窗），读面照旧全量（回载窗不漏计=零可观测变化）。"""
    from src.gland.token_meter import TokenMeter

    monkeypatch.setattr(TokenMeter, "records_window", 2)  # 构造期回载窗读类属性
    m = _meter(tmp_path)
    _seed(m, 5)
    before = m.summary()
    m2 = _meter(tmp_path)  # = service 重启
    assert len(m2._records) == 2
    assert m2.summary() == before
    assert len(m2.recent_records(5)) == 5
    assert [r["task"] for r in m2.recent_records(5)] == ["t0", "t1", "t2", "t3", "t4"]


# ── ③ 计数真源（MEM-⑥：恒对账行聚合，毒化硬反证）────────────────


def test_counter_truth_poisoned_cache_hard_reverse(tmp_path):
    """内存缓存投毒（计数/窗全改荒值）：读面仍读行真源——禁信第二套聚合列。"""
    m = _meter(tmp_path, window=2)
    _seed(m, 3)
    m._total = 999
    m._total_prompt = 999
    m._total_completion = 999
    m._by_model["m0"] = 999
    m._by_user["u0"] = 999
    m._by_provider["prov-x"]["total"] = 999
    m._records.clear()
    s = m.summary()
    assert s["call_count"] == 3
    assert s["total_tokens"] == 30
    assert s["by_model"] == {"m0": 20, "m1": 10}
    assert m.total_tokens == 30
    assert m.usage_by_model() == {"m0": 20, "m1": 10}


def test_counter_reconcile_failure_falls_back_to_cache_not_fake_zero(tmp_path, caplog):
    """counters() 对账失败=回落内存缓存（非假 0）+logger.error 可见。"""
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    class _BoomCounters(UsagePersistence):
        def counters(self):
            raise RuntimeError("aggregate on fire")

    m = TokenMeter(persist=_BoomCounters(str(tmp_path / "usage.db")))
    _seed(m, 2)
    with caplog.at_level("ERROR"):
        s = m.summary()
    assert s["call_count"] == 2  # 回落缓存=非 0
    assert s["total_tokens"] == 20
    assert any("reconciliation failed" in r.message for r in caplog.records)


# ── ④ 预算判定读真源（窗外用量照样计入预算）──────────────────────


def test_budget_checks_row_truth_not_window_cache(tmp_path):
    """窗=2、行合计 90、预算 100：record(15) 必须拒——若预算读缓存（60）就放行=反证。"""
    from src.gland.token_meter import BudgetExceededError

    m = _meter(tmp_path, window=2, budget_limit=100)
    for _ in range(3):
        m.record("prov-x", "m", 30, 0)
    with pytest.raises(BudgetExceededError, match="90/100"):
        m.record("prov-x", "m", 15, 0)
    assert m.remaining_budget == 10  # 100-90，真源口径


def test_budget_across_restart_uses_row_truth(tmp_path, monkeypatch):
    """重启后缓存只装窗：预算判定仍算全表（回载窗不放大预算）。"""
    from src.gland.token_meter import BudgetExceededError, TokenMeter

    monkeypatch.setattr(TokenMeter, "records_window", 1)  # 构造期回载窗读类属性
    m = _meter(tmp_path, budget_limit=100)
    for _ in range(3):
        m.record("prov-x", "m", 30, 0)
    m2 = _meter(tmp_path, budget_limit=100)
    assert len(m2._records) == 1  # 缓存只有 1 行=30
    with pytest.raises(BudgetExceededError):
        m2.record("prov-x", "m", 69, 0)  # 真源 90+69>100 拒；缓存口径 30+69=99 会放行=反证


# ── ⑤ persist 只读原语（load_recent/counters）───────────────────


def test_load_recent_tail_semantics_and_missing_db(tmp_path):
    """load_recent：尾切 limit+升序回填；库缺失回 [] 不建库（读口只读不建库）。"""
    from src.gland.usage_persist import UsagePersistence

    p = UsagePersistence(str(tmp_path / "u.db"))
    assert p.load_recent(5) == [] and not (tmp_path / "u.db").exists()
    for i in range(3):
        p.insert(
            provider=f"p{i}",
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            timestamp=float(i),
        )
    assert [r["provider"] for r in p.load_recent(2)] == ["p1", "p2"]
    assert [r["provider"] for r in p.load_recent(10)] == ["p0", "p1", "p2"]


def test_counters_shape_single_source(tmp_path):
    """counters() 形状与缓存派生逐键一致（单一形状禁两套）+ NULL user_id 不进 by_user。"""
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    p = UsagePersistence(str(tmp_path / "u.db"))
    p.insert(
        provider="p0",
        model="m",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        user_id="u1",
        timestamp=1.0,
    )
    p.insert(
        provider="p0",
        model="m",
        prompt_tokens=5,
        completion_tokens=5,
        total_tokens=10,
        timestamp=2.0,
    )  # user_id NULL
    out = p.counters()
    assert set(out) == {
        "call_count",
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "by_model",
        "by_user",
        "by_provider",
    }
    assert out["call_count"] == 2
    assert (out["total_prompt_tokens"], out["total_completion_tokens"], out["total_tokens"]) == (
        6,
        7,
        13,
    )
    assert out["by_user"] == {"u1": 3}  # NULL 不计（与 record() 聚合口径一致）
    assert out["by_provider"]["p0"] == {"prompt": 6, "completion": 7, "total": 13, "requests": 2}
    # 形状==缓存派生（同实例读面互证单一形状）
    m = TokenMeter(persist=p)
    assert set(m._cache_counters()) == set(out)


# ── ⑥ 诚实边界（写失败=非谎报 durable 的读面对应）───────────────


def test_write_failure_row_visible_in_window_only(tmp_path, caplog):
    """落盘失败行：窗内读面可见（缓存本职）、回落真源面/重启后不可见——如实边界。"""
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    class _BoomInsert(UsagePersistence):
        def insert(self, **_kw):
            raise RuntimeError("disk on fire")

    m = TokenMeter(persist=_BoomInsert(str(tmp_path / "usage.db")))
    m.records_window = 10
    with caplog.at_level("ERROR"):
        _seed(m, 2)
    assert len(m.recent_records(1)) == 1  # 窗内快路径可见
    assert m.recent_records(10) == []  # 回落真源=落盘失败行不在真源（不 fabricate）
    m2 = TokenMeter(persist=_BoomInsert(str(tmp_path / "usage.db")))
    assert m2.recent_records(10) == []


def test_recent_read_failure_degrades_to_window_visible(tmp_path, caplog):
    """回落读故障=降级窗内可见+logger.error（禁静默空列表）。"""
    from src.gland.token_meter import TokenMeter
    from src.gland.usage_persist import UsagePersistence

    class _BoomRead(UsagePersistence):
        def load_recent(self, limit):
            raise RuntimeError("db corrupt")

        def counters(self):
            raise RuntimeError("db corrupt")

    m = TokenMeter(persist=_BoomRead(str(tmp_path / "usage.db")))
    m.records_window = 10
    with caplog.at_level("ERROR"):
        _seed(m, 2)
        out = m.recent_records(10)
    assert [r["task"] for r in out] == ["t0", "t1"]
    assert any("recent read failed" in r.message for r in caplog.records)


# ── ⑦ 真实路径接线（写了≠接线了）────────────────────────────────


def test_modelrouter_real_call_site_window_and_truth(tmp_path, monkeypatch):
    """ModelRouter(persist_usage=True) 真调用点 record()：窗裁剪+读面回落真源全链。"""
    import asyncio

    from src.gland.router import ModelRouter

    monkeypatch.setenv("GLAND_USAGE_DB", str(tmp_path / "r.db"))
    m = ModelRouter(persist_usage=True)
    m.token_meter.records_window = 2
    m.add_provider("prov-x", "http://stub.invalid/v1", {"chat": "m-chat"})
    m.key_manager.add_key("prov-x", "sk-test")

    async def _fake_call_chat(provider, api_key, model, messages, **kwargs):
        return {
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "choices": [{"message": {"content": "ok"}}],
        }

    m._call_chat = _fake_call_chat  # type: ignore[assignment]
    for _ in range(3):
        asyncio.run(m.chat([{"role": "user", "content": "hi"}]))

    assert len(m.token_meter._records) == 2  # 窗真裁
    out = m.token_meter.recent_records(3)  # 回落真源=3 行全量
    assert len(out) == 3
    assert m.token_meter.summary()["call_count"] == 3
    assert m.token_meter.summary()["total_tokens"] == 30
    assert len(_rows(tmp_path / "r.db")) == 3


def test_api_summary_key_contract_unchanged(tmp_path):
    """/api/gland 消费的 summary 键集照旧（契约零变化：UI 键形不被搅乱）。"""
    m = _meter(tmp_path, window=1)
    _seed(m, 3)
    s = m.summary()
    assert set(s) == {
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "budget_limit",
        "remaining_budget",
        "call_count",
        "by_model",
        "by_user",
        "by_provider",
    }
    assert list(s)[:5] == [
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "budget_limit",
        "remaining_budget",
    ]

"""GR-①b-④ DreamDistiller._dream_history 内存窗 + 计数真源化——窗=纯缓存零可观测
变化、计数恒对账行聚合（MEM-⑥）、读面回落行真源。

修前（GR-① 定性「事件流型真无界」）：_dream_history 每次 dream() 运行 append 一行
（DreamResult.to_dict()）永不裁剪，get_stats 三个计数全量遍历派生——长跑进程内存
无界增长且「裁剪即破坏总计数」。本文件锁死：①persist 在场才裁窗（无真源不裁=不丢
唯一副本）②读面输出与裁窗前逐字一致（counters 恒全表行聚合=历史总量语义不变、
recent_dreams 回落行真源）③计数真源毒化硬反证（内存缓存投毒读面仍读行聚合）
④对账失败回落缓存禁静默假 0 ⑤重启回载有界（load_recent 窗内回填非全量）
⑥纯内存既有语义字节不变 ⑦写失败不阻断蒸馏本体（记账旁路纪律，非谎报 durable）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            return conn.execute(
                "SELECT dream_id, applied, skipped, failed, echo_blocked, error FROM dream_history"
                " ORDER BY created_at, rowid"
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # 表都未建（写从未成功）=行真源 0 行，如实
    finally:
        conn.close()


def _ok_llm(response="[]"):
    async def _llm(system_prompt, user_prompt):
        return response

    return _llm


_ADD_RESPONSE = json.dumps(
    [{"action": "ADD", "content": "dream-window-truth", "importance": 0.5, "reason": "x"}]
)


def _distiller(tmp_path, window=None, persist=True, llm=None, db_name="dream.db"):
    from src.hippo.dream_distiller import DreamDistiller
    from src.hippo.long_term_memory import LongTermMemoryStore

    kw = {}
    if persist:
        # 惰性 import（控制组不变面：persist=False 用例不依赖新模块，新旧皆过）
        from src.hippo.dream_persist import DreamPersistence

        kw["persist"] = DreamPersistence(str(tmp_path / db_name))
    d = DreamDistiller(
        ltm_store=LongTermMemoryStore(db_path=str(tmp_path / "ltm.db")),
        llm_call=llm or _ok_llm(),
        **kw,
    )
    if window is not None:
        d.history_window = window
    return d


def _run(d, n, messages=None):
    msgs = messages if messages is not None else [{"role": "user", "content": "hi"}]
    for _ in range(n):
        asyncio.run(d.dream(messages=msgs))
    return d


# ── ① 窗裁剪（persist 在场）+ 行真源不受影响 ─────────────────────


def test_window_trims_memory_but_rows_stay_in_db(tmp_path):
    """窗=3 跑 5 次 dream：内存窗只剩最近 3 条，DB 行真源恒 5 行（窗外行没丢）。"""
    d = _distiller(tmp_path, window=3)
    _run(d, 5)
    assert len(d._dream_history) == 3
    assert len(_rows(tmp_path / "dream.db")) == 5
    win_ids = [h["dream_id"] for h in d._dream_history]
    all_ids = [r[0] for r in _rows(tmp_path / "dream.db")]
    assert win_ids == all_ids[-3:]  # 窗=最近 3 条，序与行真源一致


def test_no_trim_without_persist_pure_memory(tmp_path):
    """persist=None 无真源可回落：裁=丢唯一副本——不裁（纯内存既有语义字节不变）。"""
    d = _distiller(tmp_path, window=3, persist=False)
    _run(d, 5)
    assert len(d._dream_history) == 5


def test_zero_window_never_trims(tmp_path):
    """N<=0=不裁窗语义（既有行为延续），行照写。"""
    d = _distiller(tmp_path, window=0)
    _run(d, 5)
    assert len(d._dream_history) == 5
    assert len(_rows(tmp_path / "dream.db")) == 5


# ── ② 读面零可观测变化（计数恒全表、recent 回落行真源） ─────────────


def test_stats_counters_ignore_trim(tmp_path):
    """窗=2 跑 5 次（每次 applied=1）：内存只剩 2 条，计数恒 5/5（历史总量语义不变）。"""
    d = _distiller(tmp_path, window=2, llm=_ok_llm(_ADD_RESPONSE))
    _run(d, 5)
    assert len(d._dream_history) == 2
    stats = d.get_stats()
    assert stats["total_dreams"] == 5
    assert stats["total_actions_applied"] == 5
    assert stats["echo_blocked_count"] == 0


def test_stats_key_shape_unchanged(tmp_path):
    d = _distiller(tmp_path)
    _run(d, 1)
    assert set(d.get_stats().keys()) == {
        "total_dreams",
        "total_actions_applied",
        "echo_blocked_count",
        "current_turn_echo",
        "recent_dreams",
    }


def test_recent_dreams_tail_unaffected_by_trim(tmp_path):
    """窗=2 跑 8 次：recent_dreams 仍是全史最近 5 条（回落行真源，与裁窗前逐字一致）。"""
    d = _distiller(tmp_path, window=2)
    _run(d, 8)
    stats = d.get_stats()
    all_ids = [r[0] for r in _rows(tmp_path / "dream.db")]
    assert [h["dream_id"] for h in stats["recent_dreams"]] == all_ids[-5:]


def test_recent_dreams_window_insufficient_returns_five(tmp_path):
    """窗=2 < 5：recent_dreams 从行真源补齐 5 条（任何 N 值读面输出不变）。"""
    d = _distiller(tmp_path, window=2)
    _run(d, 6)
    assert len(d.get_stats()["recent_dreams"]) == 5


# ── ③ 计数真源（MEM-⑥：恒对账行聚合） ───────────────────────────


def test_counter_truth_reads_rows_not_cache(tmp_path):
    """毒化硬反证：库外行（内存缓存看不见）照样计入——读面读行聚合非缓存派生。"""
    d = _distiller(tmp_path, window=1, llm=_ok_llm(_ADD_RESPONSE))
    _run(d, 2)  # applied=2 总，窗=1
    p = d._persist
    p.insert(
        {
            "dream_id": "dream_outsider",
            "total_actions": 3,
            "counts": {"ADD": 3},
            "applied": 7,
            "skipped": 0,
            "failed": 0,
            "echo_blocked": False,
            "error": "",
            "raw_response": "",
            "redacted_spans": 0,
            "created_at": 4102444800.0,
        }
    )
    stats = d.get_stats()
    assert stats["total_dreams"] == 3  # 缓存口径只能报 1
    assert stats["total_actions_applied"] == 9  # 缓存口径只能报 1


def test_counter_truth_failure_falls_back_to_cache_not_fake_zero(tmp_path):
    """对账（行聚合读取）失败=回落缓存派生值，禁静默假 0。"""
    d = _distiller(tmp_path, window=3)
    _run(d, 3)

    def _boom():
        raise RuntimeError("db down")

    d._persist.counters = _boom
    stats = d.get_stats()
    assert stats["total_dreams"] == 3  # 缓存口径（非 0）
    assert stats["total_actions_applied"] == 0  # no-actions 路径 applied=0


def test_echo_blocked_counted_from_rows_after_trim(tmp_path):
    """echo_blocked 行被裁出窗后计数不丢（计数真源=行聚合）。"""
    d = _distiller(tmp_path, window=2)
    d.mark_recall(["ltm_abc"])
    asyncio.run(d.dream(messages=[{"role": "user", "content": "hi"}]))  # blocked 行
    d.reset_turn()
    _run(d, 3)  # 后续 3 次把 blocked 行挤出窗
    assert len(d._dream_history) == 2
    assert d.get_stats()["echo_blocked_count"] == 1


# ── ④ 回载有界 + 重启 parity ────────────────────────────────────


def test_reload_bounded_to_window_not_all(tmp_path, monkeypatch):
    """回载=有界（load_recent 窗内回填）：库 20 行、窗 5 → 内存 5 条，计数恒 20。"""
    from src.hippo.dream_distiller import DreamDistiller

    p_db = tmp_path / "dream.db"
    seed = _distiller(tmp_path, llm=_ok_llm(_ADD_RESPONSE))
    _run(seed, 20)
    assert len(_rows(p_db)) == 20

    monkeypatch.setattr(DreamDistiller, "history_window", 5)
    d = _distiller(tmp_path)
    assert len(d._dream_history) == 5
    assert d.get_stats()["total_dreams"] == 20
    assert d.get_stats()["total_actions_applied"] == 20


def test_restart_parity_stats_and_recent(tmp_path):
    """重启（新实例回载）后计数/recent 与重启前逐键一致。"""
    d1 = _distiller(tmp_path)
    d1.mark_recall(["ltm_x"])
    asyncio.run(d1.dream(messages=[{"role": "user", "content": "hi"}]))  # echo blocked
    d1.reset_turn()
    _run(d1, 2)
    before = d1.get_stats()

    d2 = _distiller(tmp_path)
    after = d2.get_stats()
    assert after["total_dreams"] == before["total_dreams"] == 3
    assert after["total_actions_applied"] == before["total_actions_applied"]
    assert after["echo_blocked_count"] == before["echo_blocked_count"] == 1
    assert [h["dream_id"] for h in after["recent_dreams"]] == [
        h["dream_id"] for h in before["recent_dreams"]
    ]


# ── ⑤ 真实路径（dream() 五写点全落行、往返保真） ───────────────────


def test_real_path_row_roundtrip_fidelity(tmp_path):
    """真实 dream() 路径（LLM 回 ADD）→ 行落库，回读与 result.to_dict() 逐键相等
    （echo_blocked 还原 bool、counts 还原 dict、created_at float 保真）。"""
    d = _distiller(tmp_path, llm=_ok_llm(_ADD_RESPONSE))
    result = asyncio.run(d.dream(messages=[{"role": "user", "content": "hi"}]))
    rec = result.to_dict()
    loaded = d._persist.load_recent(10)
    assert len(loaded) == 1
    assert loaded[0] == rec
    assert loaded[0]["echo_blocked"] is False
    assert loaded[0]["counts"] == {"ADD": 1}


def test_all_five_record_paths_persist(tmp_path):
    """五条写史路径（echo 拦截/空消息/LLM 失败/无动作/正常）各落一行，真因如实。"""
    d = _distiller(tmp_path)
    d.mark_recall(["ltm_abc"])
    asyncio.run(d.dream(messages=[{"role": "user", "content": "hi"}]))  # ①echo blocked
    d.reset_turn()
    asyncio.run(d.dream(messages=[]))  # ②空消息

    async def _boom(system_prompt, user_prompt):
        raise RuntimeError("boom")

    d2 = _distiller(tmp_path, llm=_boom, db_name="dream.db")
    asyncio.run(d2.dream(messages=[{"role": "user", "content": "hi"}]))  # ③LLM 失败
    d3 = _distiller(tmp_path, llm=_ok_llm("[]"), db_name="dream.db")
    asyncio.run(d3.dream(messages=[{"role": "user", "content": "hi"}]))  # ④无动作
    d4 = _distiller(tmp_path, llm=_ok_llm(_ADD_RESPONSE), db_name="dream.db")
    asyncio.run(d4.dream(messages=[{"role": "user", "content": "hi"}]))  # ⑤正常

    rows = _rows(tmp_path / "dream.db")
    assert len(rows) == 5
    errors = [r[5] for r in rows]
    assert errors[0].startswith("Digest skipped")
    assert errors[1] == "No messages to distill"
    assert errors[2].startswith("LLM call failed")
    assert errors[3] == ""
    assert errors[4] == ""
    assert [r[1] for r in rows] == [0, 0, 0, 0, 1]  # applied
    assert rows[0][4] == 1  # echo_blocked


# ── ⑥⑦ 诚实边界（纯内存不变面 / 写失败非 durable） ─────────────────


def test_persist_none_stats_semantics_unchanged(tmp_path):
    """persist=None 纯内存：计数=修前遍历派生语义逐值不变（字节级不变面）。"""
    d = _distiller(tmp_path, persist=False)
    d.mark_recall(["ltm_abc"])
    asyncio.run(d.dream(messages=[{"role": "user", "content": "hi"}]))
    d.reset_turn()
    _run(d, 2)
    stats = d.get_stats()
    assert stats["total_dreams"] == 3
    assert stats["total_actions_applied"] == 0
    assert stats["echo_blocked_count"] == 1
    assert len(stats["recent_dreams"]) == 3
    assert stats["current_turn_echo"]["recalled_this_turn"] == 0


def test_persist_write_failure_does_not_block_dream(tmp_path):
    """落盘失败不阻断蒸馏本体（记账旁路纪律）：dream() 正常返回，窗内记录在。"""
    d = _distiller(tmp_path, window=5)

    def _boom(record):
        raise RuntimeError("disk full")

    d._persist.insert = _boom
    result = asyncio.run(d.dream(messages=[{"role": "user", "content": "hi"}]))
    assert result.error == ""
    assert len(d._dream_history) == 1
    assert len(_rows(tmp_path / "dream.db")) == 0  # 非谎报 durable：行真源无此行


def test_write_failed_row_falls_out_of_window_not_counted(tmp_path):
    """写失败行只活内存窗（诚实边界）：被裁出窗后计数真源如实不计（禁谎报 durable）。"""
    d = _distiller(tmp_path, window=1)

    def _boom(record):
        raise RuntimeError("disk full")

    d._persist.insert = _boom
    _run(d, 2)  # 两次写均失败，第二次把第一次挤出窗
    assert len(d._dream_history) == 1
    assert d.get_stats()["total_dreams"] == 0  # 行真源=0 行，如实


def test_bad_counts_row_skipped_visible_not_fatal(tmp_path):
    """坏行（counts 非法 JSON）跳过该行不丢整段史（禁静默丢=logger.error 可见）。"""
    d = _distiller(tmp_path)
    _run(d, 2)
    conn = sqlite3.connect(str(tmp_path / "dream.db"))
    try:
        conn.execute("UPDATE dream_history SET counts = '{bad json' WHERE rowid = 1")
        conn.commit()
    finally:
        conn.close()
    d2 = _distiller(tmp_path)
    assert len(d2._dream_history) == 1  # 坏行跳过，好行保留
    assert d2.get_stats()["total_dreams"] == 2  # 计数仍如实（行在库）

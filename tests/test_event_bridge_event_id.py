"""event_bridge 事件身份收口（F3b-⑨b-③-1c Gate3 逼出的挡路真断点，根因修）。

修前（同探针实证非推断）：push_event 默认 id=evt_{毫秒}、emit 记录 dict id=
evt_{毫秒}_{organ}_{type}——同毫秒内两条事件撞同一 event_id，被 TimelineStore.
record() 的 INSERT OR IGNORE 去重**静默吞掉**（record 仍返回 True=谎报）：
单请求内背靠背记录（webhook_received→webhook_ingress、webhook_sent→broadcast、
ws_message→ws_message 转发）必丢一条，真实用户同毫秒双动作同样丢。

根因修：事件身份=一次事件一身份（uuid 后缀）；record() 去重语义零变化——
同一事件 dict 重放仍同 id，replay 幂等不双写（sync_from_stream 既有语义）。

数据面全 tmp（注入 tmp TimelineStore/死端口 _BASE），绝不写生产 timeline.db。
"""

import asyncio
import os
import sqlite3
import tempfile

from src.nerve import event_bridge


def _store(db):
    from src.timeline.store import TimelineStore

    return TimelineStore(db)


def _rows(db, event_type=None):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM events"
    args = ()
    if event_type:
        sql += " WHERE event_type = ?"
        args = (event_type,)
    out = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return out


def _prep(monkeypatch):
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = _store(db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")
    return db


class _FrozenClock:
    """冻结时钟——逼出同毫秒碰撞（突发例在修前旧代码须确定性失败非时序侥幸）。"""

    def time(self):
        return 1700000000.0


def test_push_event_same_ms_burst_all_land(monkeypatch):
    """同毫秒连发 5 事件全落 5 行（修前第 2 条起被去重静默吞）。"""
    db = _prep(monkeypatch)
    monkeypatch.setattr(event_bridge, "time", _FrozenClock())
    for i in range(5):
        event_bridge.push_event({"organ": "probe", "type": f"t{i}", "summary": f"s{i}"})
    rows = _rows(db)
    assert len(rows) == 5
    assert len({r["event_id"] for r in rows}) == 5  # 身份互不撞


def test_push_event_replay_same_dict_dedupe_unchanged(monkeypatch):
    """record() replay 幂等零变化：同一事件 dict 重放仍同 id 不双写。"""
    db = _prep(monkeypatch)
    evt = {"organ": "probe", "type": "same", "summary": "s"}
    event_bridge.push_event(evt)
    event_bridge.push_event(evt)  # 同 dict 重放（id 已定，setdefault 不换）
    assert len(_rows(db)) == 1


def test_push_event_explicit_id_untouched(monkeypatch):
    db = _prep(monkeypatch)
    event_bridge.push_event({"id": "my-fixed-id", "organ": "probe", "type": "t", "summary": "s"})
    rows = _rows(db)
    assert rows and rows[0]["event_id"] == "my-fixed-id"


def test_push_event_id_keeps_evt_ms_prefix(monkeypatch):
    """id 形态保留 evt_<ms> 前缀（时序可读）+ 唯一后缀。"""
    db = _prep(monkeypatch)
    event_bridge.push_event({"organ": "probe", "type": "t", "summary": "s"})
    eid = _rows(db)[0]["event_id"]
    head, _, suffix = eid.partition("_")
    ms, _, tail = suffix.partition("_")
    assert head == "evt" and ms.isdigit() and len(tail) == 8  # uuid hex 8


def test_emit_same_ms_burst_same_organ_type_all_land(monkeypatch):
    """emit 记录 dict id 同修：同毫秒同 organ/type 突发不互吞。"""
    db = _prep(monkeypatch)
    monkeypatch.setattr(event_bridge, "time", _FrozenClock())

    async def _two():
        await event_bridge.emit("probe", "t", "s1", {})
        await event_bridge.emit("probe", "t", "s2", {})

    asyncio.run(_two())
    rows = _rows(db)
    assert len(rows) == 2
    assert len({r["event_id"] for r in rows}) == 2


def test_push_event_tenant_key_still_injected(monkeypatch):
    """既有 ⑨b-① 租户注入口零回归。"""
    db = _prep(monkeypatch)
    event_bridge.push_event({"organ": "probe", "type": "t", "summary": "s"}, tenant_id="tenant-x")
    rows = _rows(db)
    assert rows and rows[0]["tenant_id"] == "tenant-x"

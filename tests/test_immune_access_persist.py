"""RV-①b-② immune IPAccessControl 黑白名单持久化——写穿/回载/安全面语义/真实路径接线。

修前（RV-① A 组定性）：_blacklist/_whitelist 全在进程内存——运行时拉黑的 IP
（含 intrusion brute_force_auto_block 自动封禁）随 service 重启即解封=安全面。
本文件锁死：①增删/命中计数写穿落盘行逐字段 ②重启回载名单逐键一致+白名单-only
模式（安全面）不因重启消失 ③persist=None 纯内存既有语义零变化 ④写/回载失败
可见不阻断 ⑤生产真实路径（api/immune ip_control 单例）真接线。
"""

from __future__ import annotations

import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT list_kind, entry_key, ip, reason, added_at, expires_at, hit_count"
            " FROM immune_access ORDER BY list_kind, entry_key"
        ).fetchall()
    finally:
        conn.close()


def _control(tmp_path):
    from src.immune.access_control import IPAccessControl
    from src.immune.access_persist import AccessPersistence

    return IPAccessControl(persist=AccessPersistence(str(tmp_path / "access.db")))


# ── ① 写穿（变更 → DB 行）────────────────────────────────────────


def test_blacklist_add_write_through_row_verbatim(tmp_path):
    """blacklist_add 随写落盘：DB 行与内存条目逐字段一致（TTL 分支）。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10", reason="attack", ttl_seconds=3600)
    (row,) = _rows(tmp_path / "access.db")
    assert row[0] == "blacklist"
    assert row[1] == "192.168.1.10"  # entry_key=_normalize(ip)
    assert row[2] == "192.168.1.10" and row[3] == "attack"
    assert isinstance(row[4], float) and row[4] > 0
    assert isinstance(row[5], float) and row[5] > row[4]  # expires_at=added+ttl
    assert row[6] == 0


def test_blacklist_add_no_ttl_null_expires_roundtrip(tmp_path):
    """无 TTL → expires_at NULL → 回载 None（键在场值如实，禁硬造）。"""
    c = _control(tmp_path)
    c.blacklist_add("10.0.0.1", reason="manual")
    (row,) = _rows(tmp_path / "access.db")
    assert row[5] is None
    c2 = _control(tmp_path)
    (e,) = c2.list_blacklist()
    assert e["expires_at"] is None


def test_whitelist_add_write_through(tmp_path):
    """whitelist_add 落行 kind=whitelist（与黑名单同表分 kind）。"""
    c = _control(tmp_path)
    c.whitelist_add("172.16.0.1", reason="trusted")
    (row,) = _rows(tmp_path / "access.db")
    assert row[0] == "whitelist" and row[1] == "172.16.0.1" and row[3] == "trusted"
    assert row[5] is None and row[6] == 0


def test_hit_count_write_through(tmp_path):
    """命中计数随变更写穿：拦 3 次 → 行 hit_count==3（行状态整行落盘）。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10", reason="attack")
    for _ in range(3):
        assert c.is_allowed("192.168.1.10")["allowed"] is False
    (row,) = _rows(tmp_path / "access.db")
    assert row[6] == 3


# ── ② 重启回载（安全面：封禁不因重启消失）────────────────────────


def test_restart_ban_survives(tmp_path):
    """核心语义：拉黑→重启（新实例回载）→仍拦（修前=解封）。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10", reason="attack")
    c.blacklist_add("10.0.0.0/8", reason="cidr-block")
    before = c.list_blacklist()

    c2 = _control(tmp_path)  # = service 重启后的新实例
    assert c2.is_allowed("192.168.1.10")["allowed"] is False
    assert c2.is_allowed("10.1.2.3")["allowed"] is False  # CIDR 条目回载后仍命中
    after = c2.list_blacklist()

    # 非计数字段逐键 parity（回载序=entry_key 确定性序 vs 内存插入序，列表序非契约）
    def fields(e):
        return (e["ip"], e["reason"], e["added_at"], e["expires_at"])

    assert sorted(map(fields, after)) == sorted(map(fields, before))
    # 命中计数轴：直配条目被拦 1 次→1（写穿）；CIDR 命中不计数=既有语义逐字
    assert {e["ip"]: e["hit_count"] for e in after} == {"192.168.1.10": 1, "10.0.0.0/8": 0}
    assert c2.stats() == {"blacklist_count": 2, "whitelist_count": 0}


def test_restart_whitelist_only_mode_survives(tmp_path):
    """白名单-only 模式（安全面）不因重启消失：名单非空=只放行名单内。"""
    c = _control(tmp_path)
    c.whitelist_add("172.16.0.1", reason="trusted")
    assert c.is_allowed("8.8.8.8")["allowed"] is False  # 名单外被拦

    c2 = _control(tmp_path)
    assert c2.is_allowed("8.8.8.8")["allowed"] is False  # 修前=重启后放行
    assert c2.is_allowed("172.16.0.1")["allowed"] is True
    assert c2.list_whitelist() == c.list_whitelist()


def test_restart_hit_count_parity(tmp_path):
    """hit_count 是行状态非丢弃量：重启后 list 可见面不归零。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10")
    c.is_allowed("192.168.1.10")
    c.is_allowed("192.168.1.10")
    (e,) = _control(tmp_path).list_blacklist()
    assert e["hit_count"] == 2


def test_ttl_lazy_expiry_semantics_parity(tmp_path):
    """过期条目回载原样（与不停机语义逐字一致），懒清理同时清内存与库行。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10", reason="temp", ttl_seconds=-1)  # 已过期
    rows = _rows(tmp_path / "access.db")
    assert len(rows) == 1  # 懒清理前库行在场（与内存同语义）

    c2 = _control(tmp_path)
    assert c2.is_allowed("192.168.1.10")["allowed"] is True  # 过期=放行
    assert c2.list_blacklist() == []  # 懒清理真删内存行
    assert _rows(tmp_path / "access.db") == []  # 写穿删除：库行同清


def test_remove_write_through(tmp_path):
    """blacklist_remove/whitelist_remove 写穿删除：行真消失，另一名单不动。"""
    c = _control(tmp_path)
    c.blacklist_add("192.168.1.10")
    c.blacklist_add("10.0.0.1")
    c.whitelist_add("172.16.0.1")
    c.blacklist_remove("192.168.1.10")
    c.whitelist_remove("172.16.0.1")
    rows = _rows(tmp_path / "access.db")
    assert [(r[0], r[1]) for r in rows] == [("blacklist", "10.0.0.1")]
    assert _control(tmp_path).list_blacklist()[0]["ip"] == "10.0.0.1"


# ── ③ persist=None 纯内存既有语义零变化 ─────────────────────────


def test_default_control_touches_no_db(tmp_path, monkeypatch):
    """IPAccessControl() 缺省纯内存：连 env 指到 tmp 也不建库（既有语义零变化硬证）。"""
    from src.immune.access_control import IPAccessControl

    monkeypatch.setenv("IMMUNE_ACCESS_DB", str(tmp_path / "never.db"))
    c = IPAccessControl()
    c.blacklist_add("192.168.1.10")
    c.is_allowed("192.168.1.10")
    c.whitelist_add("172.16.0.1")
    c.blacklist_remove("192.168.1.10")
    assert not (tmp_path / "never.db").exists()
    assert c.list_blacklist() == [] and c.list_whitelist()[0]["ip"] == "172.16.0.1"


def test_default_control_semantics_unchanged():
    """纯内存缺省实例行为面：is_allowed 判法与修前逐字一致（白名单优先+黑拦）。"""
    from src.immune.access_control import IPAccessControl

    c = IPAccessControl()
    assert c.is_allowed("1.2.3.4") == {"allowed": True, "reason": "", "action": "allow"}
    c.blacklist_add("1.2.3.4", reason="bad")
    assert c.is_allowed("1.2.3.4") == {"allowed": False, "reason": "bad", "action": "block"}
    c.whitelist_add("5.6.7.8")
    assert c.is_allowed("9.9.9.9")["reason"] == "IP not in whitelist"


# ── ④ 写失败可见不阻断（旁路纪律：内存即生效）────────────────────


class _BrokenPersist:
    def upsert(self, **kw):
        raise RuntimeError("disk full")

    def delete(self, *a):
        raise RuntimeError("disk full")

    def load_all(self):
        raise RuntimeError("disk full")


def test_write_failure_does_not_block_ban(tmp_path, caplog):
    """落盘失败：封禁当下仍生效（内存），logger.error 可见——禁谎报 durable。"""
    import logging

    from src.immune.access_control import IPAccessControl

    c = IPAccessControl(persist=_BrokenPersist())  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        c.blacklist_add("192.168.1.10", reason="attack")
    assert c.is_allowed("192.168.1.10")["allowed"] is False  # 内存即生效
    assert any("immune access persist failed" in r.message for r in caplog.records)


def test_remove_failure_does_not_block_removal(caplog):
    """删除落盘失败：内存移除照常（当下解封生效），错误可见。"""
    import logging

    from src.immune.access_control import IPAccessControl, IPEntry

    c = IPAccessControl(persist=_BrokenPersist())  # type: ignore[arg-type]
    c._blacklist["192.168.1.10"] = IPEntry(ip="192.168.1.10", reason="x")
    with caplog.at_level(logging.ERROR):
        c.blacklist_remove("192.168.1.10")
    assert c.list_blacklist() == []
    assert any("immune access persist failed" in r.message for r in caplog.records)


def test_reload_missing_db_no_file_created(tmp_path):
    """回载只读不建库：库缺失=空起步，构造回载绝不顺手建空生产库（写穿才建库）。"""
    from src.immune.access_control import IPAccessControl
    from src.immune.access_persist import AccessPersistence

    db = tmp_path / "absent.db"
    c = IPAccessControl(persist=AccessPersistence(str(db)))
    assert not db.exists()
    assert c.list_blacklist() == [] and c.list_whitelist() == []
    c.blacklist_add("192.168.1.10")  # 写穿才建库
    assert db.exists()


# ── ⑤ 回载失败可见（诚实边界②：空名单起步，日志真因）────────────


def test_reload_failure_visible_empty_start(caplog):
    """回载失败：空名单起步+logger.error 真因可见，禁静默（安全面窗口如实记）。"""
    import logging

    from src.immune.access_control import IPAccessControl

    with caplog.at_level(logging.ERROR):
        c = IPAccessControl(persist=_BrokenPersist())  # type: ignore[arg-type]
    assert c.list_blacklist() == [] and c.list_whitelist() == []
    assert any("immune access reload failed" in r.message for r in caplog.records)
    assert c.is_allowed("1.2.3.4")["allowed"] is True  # 仍功能性


# ── ⑥ 生产真实路径接线（写了≠接线了）────────────────────────────


def test_production_wiring_static():
    """api/immune 模块级 ip_control 真接 persist（AST 静态锁死禁回退裸构造）。"""
    import ast
    from pathlib import Path

    src = Path("src/api/immune.py").read_text()
    tree = ast.parse(src)
    hit = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "ip_control" for t in node.targets)
    ]
    assert len(hit) == 1
    call = hit[0].value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    assert call.func.id == "IPAccessControl"
    assert any(kw.arg == "persist" for kw in call.keywords)
    # AccessPersistence 真消费（构造点带参=写穿/回载可达）
    persist_call = [kw.value for kw in call.keywords if kw.arg == "persist"][0]
    assert isinstance(persist_call, ast.Call) and isinstance(persist_call.func, ast.Name)
    assert persist_call.func.id == "AccessPersistence"

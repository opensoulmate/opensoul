"""F3b-⑨b-②b-6 marrow 3 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍」第六件=marrow 模块全 3 点
（F3b-⑨ census：marrow 3 全在 src/api/marrow.py，
backup_created/backup_restored/schedule_created 各 1，三个 handler）。

修前：3 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 marrow
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein/②b-2 immune/②b-3 mirror/②b-4 healer/
②b-5 pipeline 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
BackupManager 与 BackupScheduler 单例桩），绝不写生产 opensoul.db/
enterprise.db/timeline.db/~/.opensoul/backups、不打 live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    from src.services import tenant_binding as tb

    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


class _StubBackupManager:
    """BackupManager 桩——handler→push_event 接线是本拍被测物，备份本体不进测试面。"""

    def create_backup(self, source_dirs, name, description, tags):
        return SimpleNamespace(
            backup_id="bk_stub_1",
            name=name or "stub-backup",
            file_count=2,
            size_bytes=11,
            checksum="hash-stub",
        )

    def restore_backup(self, backup_id, target_dir):
        return {"success": True, "backup_id": backup_id, "target_dir": str(target_dir)}


class _StubScheduler:
    """BackupScheduler 桩——同上，定时任务本体不进测试面。"""

    def create_schedule(self, name, source_dirs, interval, interval_seconds, description, tags):
        return SimpleNamespace(
            schedule_id="sch_stub_1",
            name=name,
            cron_expr="0 3 * * *",
            interval_seconds=86400,
            next_run_at="2026-01-01T03:00:00",
            enabled=True,
        )


@pytest.fixture()
def marrow_env(tmp_path, monkeypatch):
    """marrow router + tmp 身份库/TimelineStore（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.marrow as marrow_api
    from src.timeline.store import TimelineStore

    # 备份/调度重步骤换桩（备份本体不进测试面，接线是被测物）
    monkeypatch.setattr(marrow_api, "backup_manager", _StubBackupManager())
    monkeypatch.setattr(marrow_api, "scheduler", _StubScheduler())

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # timeline 行归属真源=tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    from src.api import event_stream

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(marrow_api.router, prefix="/api/marrow")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream, marrow_api, tmp_path
    event_stream._event_buffer.clear()


def _create_backup(c, tmp_path, headers=None):
    return c.post(
        "/api/marrow/backup",
        json={"source_dirs": [str(tmp_path)], "name": "stub-backup"},
        headers=headers or {},
    )


def _restore(c, tmp_path, headers=None):
    return c.post(
        "/api/marrow/restore/bk_stub_1",
        json={"backup_id": "bk_stub_1", "target_dir": str(tmp_path / "restore-out")},
        headers=headers or {},
    )


def _create_schedule(c, tmp_path, headers=None):
    return c.post(
        "/api/marrow/schedules",
        json={"name": "stub-sched", "source_dirs": [str(tmp_path)], "interval": "daily"},
        headers=headers or {},
    )


# ── ① 3 调用点真接派生 → record 行归属 ───────────────────────────


class TestMarrowEmitTenantWiring:
    def test_backup_created_event_row_tenant_derived(self, marrow_env):
        """认证派生租户→backup_created 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es, _m, tmp_path = marrow_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-marrow-a")
        r = _create_backup(c, tmp_path, _auth(_ent_token(uid, "alice")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "backup_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-marrow-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "marrow"

    def test_backup_restored_event_row_tenant_derived(self, marrow_env):
        c, tl_db, ent_db, _es, _m, tmp_path = marrow_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-marrow-b")
        r = _restore(c, tmp_path, _auth(_ent_token(uid, "bob")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "backup_restored"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-marrow-b"

    def test_schedule_created_event_row_tenant_derived(self, marrow_env):
        c, tl_db, ent_db, _es, _m, tmp_path = marrow_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-marrow-c")
        r = _create_schedule(c, tmp_path, _auth(_ent_token(uid, "carol")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "schedule_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-marrow-c"

    def test_buffer_dict_carries_tenant_key(self, marrow_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es, _m, tmp_path = marrow_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-marrow-i")
        _create_backup(c, tmp_path, _auth(_ent_token(uid, "iris")))
        buffered = [e for e in es._event_buffer if e.get("type") == "backup_created"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-marrow-i"

    def test_dual_tenant_rows_isolated(self, marrow_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es, _m, tmp_path = marrow_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert _create_backup(c, tmp_path, _auth(_ent_token(uid_a, "u_a"))).status_code == 200
        time.sleep(0.02)
        assert _create_schedule(c, tmp_path, _auth(_ent_token(uid_b, "u_b"))).status_code == 200
        rows = _rows(tl_db)
        bk = [x for x in rows if x["event_type"] == "backup_created"]
        sc = [x for x in rows if x["event_type"] == "schedule_created"]
        assert bk[0]["tenant_id"] == "tenant-dual-a"
        assert sc[0]["tenant_id"] == "tenant-dual-b"

    def test_bad_token_backup_401_first_zero_event(self, marrow_env):
        """身份证明先于一切（F3b-⑤ 纪律）：坏 token 在备份/任何副作用之前即 401，零事件。"""
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _create_backup(c, tmp_path, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_bad_token_restore_401_zero_event(self, marrow_env):
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _restore(c, tmp_path, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_bad_token_schedule_401_zero_event(self, marrow_env):
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _create_schedule(c, tmp_path, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestMarrowEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, marrow_env):
        """匿名='default' 行值不变+backup 响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _create_backup(c, tmp_path)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == [
            "backup_id",
            "checksum",
            "file_count",
            "name",
            "size_bytes",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "backup_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_restore_anonymous_default_contract(self, marrow_env):
        """/restore 匿名契约不变+行 'default'。"""
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _restore(c, tmp_path)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["backup_id"] == "bk_stub_1"
        rows = [x for x in _rows(tl_db) if x["event_type"] == "backup_restored"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_schedule_anonymous_default_contract(self, marrow_env):
        """/schedules 匿名契约逐键不变+行 'default'。"""
        c, tl_db, _ent, _es, _m, tmp_path = marrow_env
        r = _create_schedule(c, tmp_path)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == [
            "cron_expr",
            "enabled",
            "interval_seconds",
            "name",
            "next_run_at",
            "schedule_id",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "schedule_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_all_three_call_sites_wired(self):
        """静态接线普查：3 handler 全带派生、3 调用点全传租户键（写了必须接线）。"""
        pipe = chr(124)
        src = (Path(__file__).parent.parent / "src" / "api" / "marrow.py").read_text(
            encoding="utf-8"
        )
        assert src.count("identity = bind_identity(authorization)") == 3
        assert src.count("tenant_id=identity.tenant_id") == 3
        assert src.count("push_event(") == 3
        assert src.count("authorization: str " + pipe + " None = Header(None)") == 3  # 竖线完好探针
        starts = []
        i = 0
        while True:
            j = src.find('"organ": "marrow"', i)
            if j < 0:
                break
            starts.append(j)
            i = j + 1
        assert len(starts) == 3
        for j in starts:
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[j : j + 900]

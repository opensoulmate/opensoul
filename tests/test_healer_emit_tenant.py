"""F3b-⑨b-②b-4 healer 4 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍」第四件=healer 模块全 4 点
（F3b-⑨ census：healer 4 全在 src/api/healer.py，
diagnosis/diagnosis_all/heal/heal_all 各 1）。

修前：4 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 healer
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein/②b-2 immune/②b-3 mirror 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
OrganHealer 桩），绝不写生产 opensoul.db/enterprise.db/timeline.db、不打
live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    return {"Authorization": f"Bearer {token}"} if token else {}


class _StubHealer:
    """OrganHealer 桩——handler→push_event 接线是本拍被测物，诊断本体不进测试面。"""

    def __init__(self):
        self.calls = []

    @staticmethod
    def _res(organ, healthy=True, severity="info"):
        from src.healer.diagnose import Action, DiagnosisResult, Severity

        return DiagnosisResult(
            organ=organ,
            healthy=healthy,
            severity=Severity(severity),
            symptoms=[] if healthy else ["stub-down"],
            root_cause="" if healthy else "stub-down",
            recommended_action=Action.NONE,
            action_taken=Action.NONE,
            action_success=healthy,
            response_time_ms=1.0,
        )

    async def diagnose(self, organ, endpoint):
        self.calls.append(("diagnose", organ))
        return self._res(organ)

    async def diagnose_all(self, organs):
        self.calls.append(("diagnose_all", tuple(organs.keys())))
        return [self._res(k, healthy=(i == 0)) for i, k in enumerate(organs.keys())]

    async def diagnose_and_heal(self, organ, endpoint):
        self.calls.append(("diagnose_and_heal", organ))
        return self._res(organ)

    async def heal(self, result):
        self.calls.append(("heal", result.organ))
        return self._res(result.organ, healthy=True, severity="recovered")

    def mark_recovered(self, result):
        # 契约随更新（RV-①b-⑬）：OrganHealer 新增随影口，桩同面镜像
        self.calls.append(("mark_recovered", result.organ))
        from src.healer.diagnose import Severity

        result.healthy = True
        result.severity = Severity.RECOVERED
        return result

    async def notify(self, results):
        return {"notified": True}

    async def audit_log(self, results):
        return None

    def stats(self):
        return {"total": 0, "history_size": 0}

    @property
    def history(self):
        return []


@pytest.fixture()
def healer_env(tmp_path, monkeypatch):
    """healer router + tmp 身份库/TimelineStore（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.healer as healer_api
    from src.timeline.store import TimelineStore

    # 诊断本体换桩（真实诊断打 live :8090 健康口，不进测试面）
    monkeypatch.setattr(healer_api, "healer", _StubHealer())

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
    app.include_router(healer_api.router, prefix="/api/healer")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream
    event_stream._event_buffer.clear()


# ── ① 4 调用点真接派生 → record 行归属 ───────────────────────────


class TestHealerEmitTenantWiring:
    def test_diagnose_event_row_tenant_derived(self, healer_env):
        """认证派生租户→diagnosis 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es = healer_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-healer-a")
        r = c.post("/api/healer/diagnose/soul", headers=_auth(_ent_token(uid, "alice")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "diagnosis"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-healer-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "healer"

    def test_diagnose_all_event_row_tenant_derived(self, healer_env):
        c, tl_db, ent_db, _es = healer_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-healer-b")
        r = c.post("/api/healer/diagnose-all", headers=_auth(_ent_token(uid, "bob")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "diagnosis_all"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-healer-b"

    def test_heal_event_row_tenant_derived(self, healer_env):
        c, tl_db, ent_db, _es = healer_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-healer-c")
        r = c.post("/api/healer/heal/soul", headers=_auth(_ent_token(uid, "carol")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "heal"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-healer-c"

    def test_heal_all_event_row_tenant_derived(self, healer_env):
        c, tl_db, ent_db, _es = healer_env
        uid = _insert_ent_user(ent_db, "dave", "tenant-healer-d")
        r = c.post("/api/healer/heal-all", headers=_auth(_ent_token(uid, "dave")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "heal_all"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-healer-d"

    def test_buffer_dict_carries_tenant_key(self, healer_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es = healer_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-healer-i")
        c.post("/api/healer/diagnose/soul", headers=_auth(_ent_token(uid, "iris")))
        buffered = [e for e in es._event_buffer if e.get("type") == "diagnosis"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-healer-i"

    def test_dual_tenant_rows_isolated(self, healer_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es = healer_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert (
            c.post("/api/healer/diagnose/soul", headers=_auth(_ent_token(uid_a, "u_a"))).status_code
            == 200
        )
        time.sleep(0.02)
        assert (
            c.post(
                "/api/healer/diagnose/cortex", headers=_auth(_ent_token(uid_b, "u_b"))
            ).status_code
            == 200
        )
        rows = [x for x in _rows(tl_db) if x["event_type"] == "diagnosis"]
        assert sorted(x["tenant_id"] for x in rows) == ["tenant-dual-a", "tenant-dual-b"]

    def test_bad_token_unknown_organ_401_first(self, healer_env):
        """身份证明先于一切（F3b-⑤ 纪律）：坏 token 在 404 organ 检查之前即 401，零事件。"""
        c, tl_db, _ent, _es = healer_env
        r = c.post("/api/healer/diagnose/no-such-organ", headers=_auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestHealerEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, healer_env):
        """匿名='default' 行值不变+响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es = healer_env
        r = c.post("/api/healer/diagnose/soul")
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == [
            "action_success",
            "action_taken",
            "healthy",
            "organ",
            "recommended_action",
            "response_time_ms",
            "root_cause",
            "severity",
            "symptoms",
            "timestamp",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "diagnosis"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_diagnose_all_response_contract(self, healer_env):
        """diagnose-all 响应形状逐键不变（接派生不改 handler 返回面）。"""
        c, _tl, _ent, _es = healer_env
        r = c.post("/api/healer/diagnose-all")
        assert r.status_code == 200
        assert sorted(r.json().keys()) == ["healthy", "organs", "total", "unhealthy"]

    def test_unknown_organ_404_contract_unchanged(self, healer_env):
        """匿名未知 organ 仍 404 契约不变（身份检查只前置鉴权，不改 404 面）。"""
        c, tl_db, _ent, _es = healer_env
        r = c.post("/api/healer/diagnose/no-such-organ")
        assert r.status_code == 404
        assert _rows(tl_db) == []

    def test_bad_token_401_zero_event(self, healer_env):
        """坏 token=401（bind_identity 闸门），零事件行落库（身份证明先于一切）。"""
        c, tl_db, _ent, _es = healer_env
        r = c.post("/api/healer/diagnose/soul", headers=_auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_all_four_call_sites_wired(self):
        """静态接线普查：4 handler 全带派生、4 调用点全传租户键（写了必须接线）。"""
        pipe = chr(124)
        src = (Path(__file__).parent.parent / "src" / "api" / "healer.py").read_text(
            encoding="utf-8"
        )
        assert src.count("identity = bind_identity(authorization)") == 4
        assert src.count("tenant_id=identity.tenant_id") == 4
        assert src.count("authorization: str " + pipe + " None = Header(None)") == 4  # 竖线完好探针
        for etype in ("diagnosis", "diagnosis_all", "heal", "heal_all"):
            i = src.index(f'"type": "{etype}",')
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[i : i + 900]

"""pipeline 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒写）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例
「已裁决语义的同族既有面延伸=无需拍板可立即执行件」，F3b-⑦ entity/F3b-⑩
cortex/F3b-⑪ capture 同款）：POST /api/pipeline/upload 与 /run 修前 user_id
完全客户端自报直接进 knowledge 写键——认证请求可冒写他人知识档案
（F3b-⑥ search、F3b-⑦ entity、F3b-⑪ capture 同型，本件=流水线入库面收口）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空=未声明→取派生、匿名=保留自报（既有本地多档案语义契约零变化）、
坏 token=401 零副作用。

写键=档案键家族正典 str(_resolve_user_id(identity.user_id))（与
search/cortex/graph/entity/capture 家族同源）——修前 _step_knowledge_import
内部 uuid5 归一+按用户名反查/注册回填 users.id 两路派生，与家族 md5 正典键形
分裂=pipeline 入库行对家族读面不可见（孤儿行），且写键真值随 users 表同名行
有无漂移；语义收口如实记：写键自此唯一派生=_resolve_user_id，用户开通面
（register_user）保留既有契约但不再回填写键（两权分离）。

本文件 pipeline_client tmp 全隔离（tmp opensoul.db 真 schema + tmp vein +
USERS_DB/ENTERPRISE_DB tmp），绝不写生产库。数据面=真 db_pool 真 SQL 落库
回读（非接缝桩——入库是纯写面，落库回读即真实产出证据）。
"""

import hashlib
import sqlite3
import uuid

import pytest


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _seed_user(db_path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch, pipeline_client):
    """pipeline_client tmp 库（users/knowledge/tags 真 schema）+ 种子身份行。"""
    db_path = pipeline_client.app.state.pipeline_client_opensoul_db
    user_a = _seed_user(db_path, str(uuid.uuid4()), "alice-pipe")
    user_b = _seed_user(db_path, str(uuid.uuid4()), "bob-pipe")
    return {"client": pipeline_client, "db": db_path, "user_a": user_a, "user_b": user_b}


def _upload(env, headers=None, user_id=None):
    data = {"pipeline": "text", "skip_immune": "true"}
    if user_id is not None:
        data["user_id"] = user_id
    return env["client"].post(
        "/api/pipeline/upload",
        files={
            "file": (
                f"probe-{uuid.uuid4().hex}.txt",
                b"guard sentinel content for knowledge import",
                "text/plain",
            )
        },
        data=data,
        headers=headers or {},
    )


def _store_vein_file(env) -> str:
    from src.vein.file_store import FileStore

    store = FileStore()
    meta = store.store(
        data=b"probe content for pipeline run face",
        name="run-probe.txt",
        mime_type="text/plain",
        tags=["probe"],
    )
    return meta.file_id


def _run(env, file_id, headers=None, user_id=None):
    body = {"file_id": file_id, "pipeline": "text", "skip_immune": True}
    if user_id is not None:
        body["user_id"] = user_id
    return env["client"].post("/api/pipeline/run", json=body, headers=headers or {})


def _knowledge_rows(env) -> list[dict]:
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM knowledge")]


def _users_rows(env) -> list[dict]:
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM users")]


def _know_step(resp) -> dict:
    return next(s for s in resp.json()["steps"] if s["step"] == "knowledge")


class TestUploadIdentityAxis:
    def test_anon_default_canonical_key(self, env):
        """匿名缺省：写键=档案键家族正典 str(_resolve_user_id('default'))（契约随更新，
        修前 uuid5(NAMESPACE_DNS,'default') 与家族读面键形不匹配=孤儿行）。"""
        resp = _upload(env)
        assert resp.status_code == 200
        expected = str(_md5_uuid("default"))
        rows = _knowledge_rows(env)
        assert len(rows) == 1
        assert rows[0]["user_id"] == expected
        assert _know_step(resp)["user_id"] == expected  # 真写入键如实报

    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-⑥ 契约零变化）→ 写键=自报档案正典键。"""
        resp = _upload(env, user_id="alice-anon")
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == str(_md5_uuid("alice-anon"))

    def test_anon_claim_existing_user_not_rekeyed(self, env):
        """语义收口锁死：同名 users 行存在也不再回填写键（修前 uid 被覆写为
        users.id=第二路键派生，写键真值随 users 表漂移）。"""
        resp = _upload(env, user_id="alice-pipe")
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == str(_md5_uuid("alice-pipe"))
        assert _knowledge_rows(env)[0]["user_id"] != env["user_a"]

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→写键=派生（users.id），修前自报 'default' 顶替（冒写面）。"""
        resp = _upload(env, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_declare_default_is_undeclared(self, env):
        """'default'/空=未声明→取派生（不误伤不传参调用方）。"""
        resp = _upload(env, headers=_token(env["user_a"]), user_id="default")
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常入库。"""
        resp = _upload(env, headers=_token(env["user_a"]), user_id=env["user_a"])
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒写面收口（修前 200 直接把知识写进 victim 档案）。"""
        users_before = len(_users_rows(env))
        resp = _upload(env, headers=_token(env["user_a"]), user_id=env["user_b"])
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _knowledge_rows(env) == []  # 零副作用硬反证：知识行零落库
        assert len(_users_rows(env)) == users_before  # 用户开通零副作用

    def test_bad_token_401_zero_side_effect(self, env):
        resp = _upload(env, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _knowledge_rows(env) == []
        assert len(_users_rows(env)) == 2  # 只有种子行


class TestRunIdentityAxis:
    def test_run_anon_default_canonical_key(self, env):
        """/run 同轴：写键=家族正典（修前 req.user_id 直通 _step_knowledge_import）。"""
        fid = _store_vein_file(env)
        resp = _run(env, fid)
        assert resp.status_code == 200
        expected = str(_md5_uuid("default"))
        rows = _knowledge_rows(env)
        assert len(rows) == 1
        assert rows[0]["user_id"] == expected
        assert _know_step(resp)["user_id"] == expected

    def test_run_authed_undeclared_derives_user(self, env):
        fid = _store_vein_file(env)
        resp = _run(env, fid, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_run_authed_conflict_403_zero_side_effect(self, env):
        """矛盾自报=403 先于一切步骤（身份证明先于副作用），零落库零开通。"""
        users_before = len(_users_rows(env))
        resp = _run(env, "nonexistent", headers=_token(env["user_a"]), user_id=env["user_b"])
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _knowledge_rows(env) == []
        assert len(_users_rows(env)) == users_before


class TestWriteKeyFamilyShape:
    def test_written_key_parses_as_uuid_family(self, env):
        """落库键=合法 UUID 字符串（家族读面 _resolve_user_id 可命中），修前
        uuid5/裸串键形与家族分裂=孤儿行收口。"""
        _upload(env, user_id="alice-anon")
        key = _knowledge_rows(env)[0]["user_id"]
        assert uuid.UUID(key) == _md5_uuid("alice-anon")

    def test_provisioning_contract_preserved(self, env):
        """用户开通面既有契约保留（test_pipeline_api 同款断言）：新名导入=users 行
        真开通（username=导入名），但开通不回填写键（两权分离）。"""
        resp = _upload(env, user_id="fresh-provision")
        assert resp.status_code == 200
        rows = [u for u in _users_rows(env) if u["username"] == "fresh-provision"]
        assert len(rows) == 1
        assert _knowledge_rows(env)[0]["user_id"] == str(_md5_uuid("fresh-provision"))
        assert _knowledge_rows(env)[0]["user_id"] != rows[0]["id"]

    def test_upload_response_shape_unchanged(self, env):
        """upload 响应顶层契约键集不变（knowledge step 加性键 user_id 不动顶层）。"""
        resp = _upload(env)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "pipeline_id",
            "status",
            "file_id",
            "pipeline_type",
            "steps",
            "started_at",
            "finished_at",
            "elapsed_ms",
        }


class TestStaticWiring:
    def test_wiring_shape(self):
        """静态接线：恰 2 处 bind_identity 声明式派生（upload Form user_id/run
        req.user_id）、2 个写点真消费 identity.user_id、修前 uuid5 派生与
        users.id 回填清零、import 在文件顶部。"""
        from pathlib import Path

        src = Path("src/api/pipeline.py").read_text(encoding="utf-8")
        assert src.count('bind_identity(authorization, "", user_id)') == 1
        assert src.count('bind_identity(authorization, "", req.user_id)') == 1
        assert "bind_identity(authorization)" not in src
        assert src.count("user_id=identity.user_id") == 2
        assert "uuid.uuid5" not in src
        assert 'uid = uuid.UUID(by_name["id"])' not in src
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")
        assert src.index("def _resolve_user_id") < src.index("@router")

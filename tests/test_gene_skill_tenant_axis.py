"""skill_learner 面行租户键加轴测试（DEC-⑦-⑱）。

修前（⑦-⑯ 边界留档定性）：skill_learner 面（learned_skills/skill_audit 两表）无行
租户键——extract 写点零身份轴、list/count/categories/find_relevant/get_stats/
audit_tail/history 读面零过滤（跨租户泄露）、usage/delete/revert 按 skill_id 跨租户
改删零闸、purge/prune 全量清理可跨租户。

收口=skill_row_tenant 单一判法（自带键缺失/空白→'default' 兜底，⑦-③
template_row_tenant 同款）+两表行键（DDL+ALTER 幂等回填 'default'）+写点归属
（extract 落行 bind_identity 派生、匿名 'default' 契约不变、UPDATE 粘性不洗键、
跨租户同 skill_id=劫持 SkillTenantDenied→403 行零改写）+读面恒过滤（None=系统视图
字节不变）+by-ID 归属闸（usage/delete/revert-preview/revert/current_state：跨租户
与 not-found 同契约不差分泄露）+审计行归属（显式键→父行继承→'default'）+
purge/prune 租户作用域（"只给租户"的清理不得变相清全史，F3b-③a/③c-1 纪律）。

测试面=引擎 tmp SkillLearner（绝不碰生产 ~/.hermes/opensoul/gene/skills.db）+
conftest gene_client 隔离面（真实 router）+ tmp 身份库（USERS_DB/ENTERPRISE_DB
env，真 bind_identity）。
"""

import json
import sqlite3
import time
import uuid

import pytest

from src.gene.skill_learner import (
    DEFAULT_SKILL_TENANT,
    Skill,
    SkillLearner,
    SkillTenantDenied,
    skill_row_tenant,
)

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _tc(*names):
    return [{"function": {"name": n, "arguments": {}}} for n in names]


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _auth(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


# ── 判法/迁移 ─────────────────────────────────────────────────────


def test_row_tenant_judge_shapes():
    """skill_row_tenant 单一判法：dict/Row/无键/空白/None 全形态→'default' 兜底。"""
    assert skill_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"
    assert skill_row_tenant({"tenant_id": "  tenant-a  "}) == "tenant-a"
    assert skill_row_tenant({"tenant_id": ""}) == DEFAULT_SKILL_TENANT
    assert skill_row_tenant({}) == DEFAULT_SKILL_TENANT
    assert skill_row_tenant(None) == DEFAULT_SKILL_TENANT
    assert skill_row_tenant({"tenant_id": None}) == DEFAULT_SKILL_TENANT


def test_migration_backfill_and_raw_insert_default(learner, tmp_path):
    """老库迁移：无键行回填 'default'；新裸 INSERT 缺列=DEFAULT 'default' 可见。"""
    # 先用老 schema 建库落行（无 tenant_id 列）
    raw = tmp_path / "raw.db"
    with sqlite3.connect(raw) as c:
        c.execute(
            "CREATE TABLE learned_skills (skill_id TEXT PRIMARY KEY, name TEXT NOT NULL,"
            " description TEXT DEFAULT '', category TEXT DEFAULT 'general',"
            " code_template TEXT DEFAULT '', parameters TEXT DEFAULT '[]',"
            " usage_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,"
            " avg_duration_ms REAL DEFAULT 0, created_at REAL NOT NULL,"
            " last_used_at REAL DEFAULT 0, tags TEXT DEFAULT '[]',"
            " source_session TEXT DEFAULT '', source TEXT DEFAULT '')"
        )
        c.execute(
            "CREATE TABLE skill_audit (audit_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " skill_id TEXT NOT NULL, event TEXT NOT NULL, old_json TEXT DEFAULT '',"
            " new_json TEXT DEFAULT '', actor TEXT DEFAULT '', reason TEXT DEFAULT '',"
            " created_at REAL NOT NULL)"
        )
        c.execute(
            "INSERT INTO learned_skills (skill_id, name, created_at)"
            " VALUES ('legacy-1', 'legacy', 1.0)"
        )
        c.execute(
            "INSERT INTO skill_audit (skill_id, event, created_at) VALUES ('legacy-1', 'ADD', 1.0)"
        )
        c.commit()
    lg = SkillLearner(db_path=str(raw))
    rows = lg.list_skills()  # 系统视图
    assert [r["skill_id"] for r in rows] == ["legacy-1"]
    assert rows[0]["tenant_id"] == DEFAULT_SKILL_TENANT  # 回填归一
    assert lg.audit_tail()[0]["tenant_id"] == DEFAULT_SKILL_TENANT
    # 新裸 INSERT（不带 tenant_id 列）= DDL DEFAULT 'default'，default 查询可见
    with sqlite3.connect(raw) as c:
        c.execute(
            "INSERT INTO learned_skills (skill_id, name, created_at) VALUES ('raw-2', 'raw', 2.0)"
        )
        c.commit()
    assert sorted(r["skill_id"] for r in lg.list_skills(tenant_id="default")) == [
        "legacy-1",
        "raw-2",
    ]
    # 幂等：重复 init 零副作用
    lg2 = SkillLearner(db_path=str(raw))
    assert sorted(r["skill_id"] for r in lg2.list_skills(tenant_id="default")) == [
        "legacy-1",
        "raw-2",
    ]


# ── 写点归属 ─────────────────────────────────────────────────────


def test_extract_writes_row_and_audit_tenant(learner):
    """extract 落行=派生租户键；ADD 审计行同键（显式键）；Skill.tenant_id 归一。"""
    skill = learner.extract_from_execution(
        session_id="s1",
        task_description="读取文件并写入结果",
        tool_calls=_tc("read_file", "write_file"),
        success=True,
        tenant_id="tenant-a",
    )
    assert skill is not None and skill.tenant_id == "tenant-a"
    rows = learner.list_skills(tenant_id="tenant-a")
    assert [r["skill_id"] for r in rows] == [skill.skill_id]
    assert rows[0]["tenant_id"] == "tenant-a"
    events = learner.history(skill.skill_id, tenant_id="tenant-a")
    assert [e["event"] for e in events] == ["ADD"]
    assert all(e["tenant_id"] == "tenant-a" for e in events)


def test_extract_anonymous_default(learner):
    """未提供 tenant_id → 'default'（系统/缺省写方契约不变）。"""
    skill = learner.extract_from_execution(
        session_id="s1",
        task_description="检索网页并读取",
        tool_calls=_tc("web_search", "read_file"),
        success=True,
    )
    assert skill.tenant_id == DEFAULT_SKILL_TENANT
    assert [r["skill_id"] for r in learner.list_skills(tenant_id="default")] == [skill.skill_id]


def test_extract_hijack_gate_row_untouched(learner):
    """跨租户同 skill_id=劫持：SkillTenantDenied 行/计数/审计零改写。"""
    learner.extract_from_execution(
        session_id="s1",
        task_description="浏览器与终端协作",
        tool_calls=_tc("browser_exec", "terminal"),
        success=True,
        tenant_id="tenant-a",
    )
    before = learner.list_skills()[0]
    events_before = len(learner.audit_tail())
    with pytest.raises(SkillTenantDenied):
        learner.extract_from_execution(
            session_id="s2",
            task_description="浏览器与终端协作",  # 同任务+同工具序列→同 skill_id
            tool_calls=_tc("browser_exec", "terminal"),
            success=True,
            tenant_id="tenant-b",
        )
    after = learner.list_skills()[0]
    assert after["usage_count"] == before["usage_count"] == 1
    assert after["tenant_id"] == "tenant-a"  # 行归属未被改写
    assert len(learner.audit_tail()) == events_before  # 零新增审计


def test_extract_update_sticky(learner):
    """同租户再提炼=UPDATE 计数累加，行租户键粘性不洗。"""
    for _ in range(2):
        skill = learner.extract_from_execution(
            session_id="s1",
            task_description="读取文件并写入结果二",
            tool_calls=_tc("read_file", "write_file"),
            success=True,
            tenant_id="tenant-a",
        )
    rows = learner.list_skills(tenant_id="tenant-a")
    assert rows[0]["usage_count"] == 2 and rows[0]["success_count"] == 2
    assert rows[0]["tenant_id"] == "tenant-a"
    assert [e["event"] for e in learner.history(skill.skill_id, tenant_id="tenant-a")] == [
        "UPDATE",
        "ADD",
    ]


# ── 读面过滤 ─────────────────────────────────────────────────────


def test_list_count_categories_three_sides(learner):
    """list/count/categories 三面互斥+过滤先于截断+系统视图全量。"""
    time.time()
    learner.extract_from_execution(
        "s", "A 任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    learner.extract_from_execution(
        "s", "B 任务", _tc("web_search", "read_file"), True, tenant_id="tenant-b"
    )
    learner.extract_from_execution("s", "D 任务", _tc("terminal", "read_file"), True)
    assert {r["skill_id"] for r in learner.list_skills(tenant_id="tenant-a")} == {
        r["skill_id"] for r in learner.list_skills(tenant_id="tenant-a")
    }
    a_rows = learner.list_skills(tenant_id="tenant-a")
    b_rows = learner.list_skills(tenant_id="tenant-b")
    assert len(a_rows) == 1 and len(b_rows) == 1
    assert a_rows[0]["tenant_id"] == "tenant-a"
    assert len(learner.list_skills()) == 3  # 系统视图全量
    assert learner.count_skills(tenant_id="tenant-a") == 1
    assert learner.count_skills() == 3
    cats = learner.category_counts(tenant_id="tenant-a")
    assert sum(c["count"] for c in cats) == 1
    assert sum(c["count"] for c in learner.category_counts()) == 3
    # 过滤先于截断：limit=1 只计可见行
    assert len(learner.list_skills(tenant_id="tenant-a", limit=1)) == 1


def test_find_relevant_filter(learner):
    """find_relevant 候选全集恒按行租户键过滤+加性键（同 skill_id 跨租户被劫持闸
    挡在写点=构造上不可同族双行，本例用同主题不同任务文本）。"""
    for t, task in (("tenant-a", "清理日志文件甲"), ("tenant-b", "清理日志文件乙")):
        learner.extract_from_execution("s", task, _tc("terminal", "read_file"), True, tenant_id=t)
    ra = learner.find_relevant("清理日志", tenant_id="tenant-a")
    assert len(ra) == 1 and ra[0]["tenant_id"] == "tenant-a"
    rb = learner.find_relevant("清理日志", tenant_id="tenant-b")
    assert len(rb) == 1 and rb[0]["tenant_id"] == "tenant-b"
    assert ra[0]["skill_id"] != rb[0]["skill_id"]
    assert len(learner.find_relevant("清理日志")) == 2  # 系统视图
    assert learner.get_context_prompt("清理日志", tenant_id="tenant-a").count("###") == 1


def test_get_stats_scope(learner):
    """get_stats 逐键恒按行租户键聚合（禁泄露全局分布）；None=系统视图。"""
    learner.extract_from_execution(
        "s", "统计任务 A", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    learner.extract_from_execution(
        "s", "统计任务 B", _tc("web_search", "read_file"), True, tenant_id="tenant-b"
    )
    sa = learner.get_stats(tenant_id="tenant-a")
    assert sa["total_skills"] == 1
    assert sum(sa["by_category"].values()) == 1
    assert len(sa["top_skills"]) == 1
    assert learner.get_stats()["total_skills"] == 2


# ── by-ID 归属闸 ─────────────────────────────────────────────────


def test_record_usage_gate(learner):
    """usage 归属闸：own 更新、跨租户与缺行同契约（updated=False）、系统视图更新。"""
    skill = learner.extract_from_execution(
        "s", "遥测任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    ok = learner.record_usage(skill.skill_id, success=True, tenant_id="tenant-a")
    assert ok["updated"] and ok["usage_count"] == 2
    cross = learner.record_usage(skill.skill_id, success=True, tenant_id="tenant-b")
    assert cross == {
        "updated": False,
        "usage_count": 0,
        "success_count": 0,
        "success_rate": 0.0,
    }  # 与缺行同契约
    assert learner.list_skills(tenant_id="tenant-a")[0]["usage_count"] == 2  # 零改写
    sysv = learner.record_usage(skill.skill_id, success=True)
    assert sysv["updated"] and sysv["usage_count"] == 3


def test_delete_gate(learner):
    """delete 归属闸：own 删+DELETE 审计行同键；跨租户 False 行存活；系统视图可删。"""
    skill = learner.extract_from_execution(
        "s", "删除任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    assert learner.delete_skill(skill.skill_id, tenant_id="tenant-b") is False
    assert learner.current_state(skill.skill_id, tenant_id="tenant-a") is not None
    assert learner.delete_skill(skill.skill_id, tenant_id="tenant-a") is True
    assert learner.current_state(skill.skill_id) is None
    events = learner.audit_tail(tenant_id="tenant-a")
    assert [e["event"] for e in events] == ["DELETE", "ADD"]
    assert all(e["tenant_id"] == "tenant-a" for e in events)


def test_revert_gate(learner):
    """revert 链归属闸：own preview+revert 真回滚；跨租户 None 与缺行同契约零副作用。"""
    skill = learner.extract_from_execution(
        "s", "回滚任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    learner.extract_from_execution(
        "s", "回滚任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )  # UPDATE
    preview = learner.revert_preview(skill.skill_id, tenant_id="tenant-a")
    assert preview is not None and preview["action"] == "update"
    assert learner.revert_preview(skill.skill_id, tenant_id="tenant-b") is None
    assert learner.revert_skill(skill.skill_id, tenant_id="tenant-b") is None
    assert learner.list_skills(tenant_id="tenant-a")[0]["usage_count"] == 2  # 零副作用
    result = learner.revert_skill(skill.skill_id, tenant_id="tenant-a")
    assert result is not None and result["action"] == "update"
    assert learner.list_skills(tenant_id="tenant-a")[0]["usage_count"] == 1  # 真回滚
    events = learner.audit_tail(tenant_id="tenant-a")
    assert [e["event"] for e in events][:2] == ["REVERT", "UPDATE"]
    assert all(e["tenant_id"] == "tenant-a" for e in events)


def test_history_and_audit_tail_filter(learner):
    """history 跨租户=空面与空历史同契约；audit_tail 恒过滤；行带加性键。"""
    skill = learner.extract_from_execution(
        "s", "历史任务", _tc("read_file", "write_file"), True, tenant_id="tenant-a"
    )
    learner.extract_from_execution(
        "s", "历史任务 B", _tc("web_search", "read_file"), True, tenant_id="tenant-b"
    )
    assert len(learner.history(skill.skill_id, tenant_id="tenant-a")) == 1
    assert learner.history(skill.skill_id, tenant_id="tenant-b") == []
    assert len(learner.history(skill.skill_id)) == 1  # 系统视图
    tail = learner.audit_tail(tenant_id="tenant-a")
    assert len(tail) == 1 and tail[0]["tenant_id"] == "tenant-a"
    assert len(learner.audit_tail()) == 2


# ── purge / prune 作用域 ─────────────────────────────────────────


def test_purge_scope(learner):
    """purge 租户作用域：清 A 的 test 行（技能+审计），B 的 test 行存活；None=全量。"""
    for t in ("tenant-a", "tenant-b"):
        learner.extract_from_execution(
            "s",
            f"测试任务 {t}",
            _tc("read_file", "write_file"),
            True,
            source="test",
            tenant_id=t,
        )
    result = learner.purge_by_source("test", tenant_id="tenant-a")
    assert result["deleted_skills"] == 1
    assert learner.list_skills(tenant_id="tenant-a") == []
    assert len(learner.list_skills(tenant_id="tenant-b")) == 1
    assert learner.audit_tail(tenant_id="tenant-a") == []
    assert len(learner.audit_tail(tenant_id="tenant-b")) == 1
    full = learner.purge_by_source("test")
    assert full["deleted_skills"] == 1  # 系统视图清余下


def test_prune_scope(learner):
    """prune 三层裁剪恒限租户（外层 DELETE 带键防变相清他租户史）+红线按 (租户,技能)。"""
    for t in ("tenant-a", "tenant-b"):
        skill = learner.extract_from_execution(
            "s", f"修剪任务 {t}", _tc("read_file", "write_file"), True, tenant_id=t
        )
        learner._audit(skill.skill_id, "FAIL", new={"x": 1}, tenant_id=t)
        # 老化 FAIL 行
        with sqlite3.connect(learner.db_path) as c:
            c.execute(
                "UPDATE skill_audit SET created_at = ? WHERE skill_id = ? AND event = 'FAIL'",
                (time.time() - 10 * 86400, skill.skill_id),
            )
            c.commit()
    result = learner.prune_audit(fail_max_age_days=1.0, tenant_id="tenant-a")
    assert result["deleted_aged_fail"] == 1
    a_events = [e["event"] for e in learner.audit_tail(tenant_id="tenant-a")]
    b_events = [e["event"] for e in learner.audit_tail(tenant_id="tenant-b")]
    assert a_events == ["ADD"]  # A 的 FAIL 已清、红线 ADD 保留
    assert b_events == ["FAIL", "ADD"]  # B 一行不动
    result2 = learner.prune_audit(fail_max_age_days=1.0)  # 系统视图清余下
    assert result2["deleted_aged_fail"] == 1


# ── API 全链（gene_client + 真 bind_identity） ────────────────────


@pytest.fixture()
def env(gene_client, tmp_path, monkeypatch):
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()
    yield {
        "api": gene_client,
        "ua": _seed_user(users_db, str(uuid.uuid4()), "alice-skill", "tenant-a"),
        "ub": _seed_user(users_db, str(uuid.uuid4()), "bob-skill", "tenant-b"),
        "ud": _seed_user(users_db, str(uuid.uuid4()), "carol-skill", "default"),
    }


def _extract(api, task, auth=None, source="extract"):
    return api.post(
        "/api/gene/skill/extract",
        json={
            "task_description": task,
            "execution_log": json.dumps([tc for tc in _tc("read_file", "write_file")]),
            "success": True,
            "source": source,
        },
        headers=auth or {},
    )


def test_api_anonymous_default_contract(env):
    """匿名='default' 契约不变：落行/可见/usage/delete 全链 + 加性键。"""
    api = env["api"]
    r = _extract(api, "匿名提炼任务")
    assert r.status_code == 200
    assert r.json()["tenant_id"] == DEFAULT_SKILL_TENANT
    sid = r.json()["skill_id"]
    lst = api.get("/api/gene/skill").json()
    assert lst["total"] == 1 and lst["tenant_id"] == DEFAULT_SKILL_TENANT
    assert lst["skills"][0]["tenant_id"] == DEFAULT_SKILL_TENANT
    assert api.post(f"/api/gene/skill/{sid}/usage", json={"success": True}).status_code == 200
    assert api.get(f"/api/gene/skill/{sid}/history").json()["events"][0]["tenant_id"] == (
        DEFAULT_SKILL_TENANT
    )
    assert api.delete(f"/api/gene/skill/{sid}").status_code == 200
    # default 身份用户==匿名面（同作用域同视图）
    r2 = _extract(api, "default 用户提炼任务", auth=_auth(env["ud"]))
    assert r2.json()["tenant_id"] == DEFAULT_SKILL_TENANT
    ud_view = api.get("/api/gene/skill", headers=_auth(env["ud"])).json()
    anon_view = api.get("/api/gene/skill").json()
    assert ud_view["total"] == anon_view["total"] == 1
    assert ud_view["skills"] == anon_view["skills"]


def test_api_bad_token_401_zero_effects(env):
    """坏 token 401 先于读/写（零行零读）。"""
    api = env["api"]
    bad = {"Authorization": "Bearer garbage-token"}
    assert _extract(api, "坏 token 任务", auth=bad).status_code == 401
    assert api.get("/api/gene/skill", headers=bad).status_code == 401
    assert api.get("/api/gene/skill/audit", headers=bad).status_code == 401
    assert (
        api.post("/api/gene/skill/purge-source", json={"source": "test"}, headers=bad).status_code
        == 401
    )
    # 零副作用：匿名面仍为空
    assert api.get("/api/gene/skill").json()["total"] == 0


def test_api_three_sides_isolated(env):
    """三面互斥：A/B/匿名各自只见己行（list/recommend/audit 全过滤）。"""
    api = env["api"]
    ra = _extract(api, "A 面独有任务", auth=_auth(env["ua"]))
    rb = _extract(api, "B 面独有任务", auth=_auth(env["ub"]))
    assert ra.json()["tenant_id"] == "tenant-a"
    assert rb.json()["tenant_id"] == "tenant-b"
    la = api.get("/api/gene/skill", headers=_auth(env["ua"])).json()
    lb = api.get("/api/gene/skill", headers=_auth(env["ub"])).json()
    la_ids = {s["skill_id"] for s in la["skills"]}
    lb_ids = {s["skill_id"] for s in lb["skills"]}
    assert la_ids == {ra.json()["skill_id"]} and la["total"] == 1
    assert lb_ids == {rb.json()["skill_id"]} and lb["total"] == 1
    assert la_ids.isdisjoint(lb_ids)
    # recommend 同过滤
    rec_a = api.post(
        "/api/gene/skill/recommend",
        json={"task_description": "A 面独有"},
        headers=_auth(env["ua"]),
    ).json()
    assert {s["skill_id"] for s in rec_a["skills"]} <= la_ids
    # audit 全过滤
    au_a = api.get("/api/gene/skill/audit", headers=_auth(env["ua"])).json()
    assert all(e["tenant_id"] == "tenant-a" for e in au_a["events"])
    assert len(au_a["events"]) == 1


def test_api_byid_battery(env):
    """by-ID 电池：B 冒读写 A 全 404 与 not-found 同契约零副作用；A 正对照全通。"""
    api = env["api"]
    sid = _extract(api, "闸门任务", auth=_auth(env["ua"])).json()["skill_id"]
    _extract(api, "闸门任务", auth=_auth(env["ua"]))  # UPDATE → 产生可回滚事件
    hb, ab = _auth(env["ub"]), _auth(env["ua"])
    # B 冒读/写
    assert (
        api.post(f"/api/gene/skill/{sid}/usage", json={"success": True}, headers=hb).status_code
        == 404
    )
    assert api.get(f"/api/gene/skill/{sid}/history", headers=hb).json()["events"] == []
    assert api.get(f"/api/gene/skill/{sid}/revert-preview", headers=hb).status_code == 404
    assert api.post(f"/api/gene/skill/{sid}/revert", headers=hb).status_code == 404
    assert api.delete(f"/api/gene/skill/{sid}", headers=hb).status_code == 404
    # 行零改写（A 正对照仍可见、计数=2——B 的 usage 被拒不递增）
    lst = api.get("/api/gene/skill", headers=ab).json()
    assert lst["total"] == 1 and lst["skills"][0]["usage_count"] == 2
    # A 正对照
    assert (
        api.post(f"/api/gene/skill/{sid}/usage", json={"success": True}, headers=ab).status_code
        == 200
    )
    assert api.get(f"/api/gene/skill/{sid}/revert-preview", headers=ab).status_code == 200
    assert api.delete(f"/api/gene/skill/{sid}", headers=ab).status_code == 200


def test_api_hijack_403_row_untouched(env):
    """跨租户同 skill_id 提炼=劫持 403 行零改写（usage_count 不递增）。"""
    api = env["api"]
    ra = _extract(api, "劫持目标任务", auth=_auth(env["ua"]))
    ra.json()["skill_id"]
    rb = _extract(api, "劫持目标任务", auth=_auth(env["ub"]))  # 同任务同工具序列
    assert rb.status_code == 403 and "another tenant" in rb.json()["detail"]
    lst = api.get("/api/gene/skill", headers=_auth(env["ua"])).json()
    assert lst["skills"][0]["usage_count"] == 1 and lst["skills"][0]["tenant_id"] == "tenant-a"
    assert api.get("/api/gene/skill", headers=_auth(env["ub"])).json()["total"] == 0


def test_api_purge_and_prune_scope(env):
    """purge/prune 作用域：A 的清理不动 B 的行。"""
    api = env["api"]
    ha, hb = _auth(env["ua"]), _auth(env["ub"])
    _extract(api, "A 测试行", auth=ha, source="test")
    _extract(api, "B 测试行", auth=hb, source="test")
    r = api.post("/api/gene/skill/purge-source", json={"source": "test"}, headers=ha)
    assert r.status_code == 200
    assert r.json()["deleted_skills"] == 1 and r.json()["tenant_id"] == "tenant-a"
    assert api.get("/api/gene/skill", headers=ha).json()["total"] == 0
    assert api.get("/api/gene/skill", headers=hb).json()["total"] == 1
    r2 = api.post("/api/gene/skill/audit/prune", headers=ha)
    assert r2.status_code == 200 and r2.json()["tenant_id"] == "tenant-a"
    assert api.get("/api/gene/skill", headers=hb).json()["total"] == 1  # B 一行不动

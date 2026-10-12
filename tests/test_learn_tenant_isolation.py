"""F3b-③c-2②b learn 面（courses/chapters 表）数据面隔离接线测试。

覆盖四层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB/LEARN_DB 环境隔离，
测试行绝不写生产 ~/.opensoul/learn/learn.db）：
①courses/chapters.tenant_id 列迁移（老库补列+存量行回填 'default'+租户索引）与
  落行归属（create=身份派生/章节行=课程行继承/旧 JSON 迁移='default'）
②查询过滤（list/stats 恒按租户，tenant_id=None=系统视图）
③IsolationEngine.check_access 真执行（by-ID 闸门跨租户 CourseAccessDenied→API 403、
  bulk 审计留痕、审计行 ns 对账）
④API 层派生租户全链（auth token → bind_identity → 行归属落库 → 跨租户 403 电池，
  真实 learn router；匿名 default 契约不变、响应形状逐键不变、缺行 404、坏 token 401）

注：src.api.learn 模块级 engine 单例（import 即构造）——本文件 fixture 延迟 import
并 monkeypatch 换 tmp 实例，测试行零生产接触（conftest LEARN_DB 全测试隔离双保险）。
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.learn.course_engine import CourseAccessDenied, CourseEngine
from src.services import tenant_binding as tb

# 生产 learn.db F3b-③c-2②b 迁移前形状（无 tenant_id）
OLD_COURSES_DDL = """CREATE TABLE courses (
                    course_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    topics TEXT DEFAULT '[]',
                    domain TEXT DEFAULT '',
                    knowledge_ids TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'not_started',
                    created_at REAL,
                    updated_at REAL,
                    generated_by TEXT DEFAULT 'manual'
                )"""

OLD_CHAPTERS_DDL = """CREATE TABLE chapters (
                    chapter_id TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    ord INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    completed_at REAL,
                    quiz TEXT DEFAULT '[]'
                )"""

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


def _engine(tmp_path):
    return CourseEngine(storage_dir=tmp_path, db_path=str(tmp_path / "learn.db"))


def _rows(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── ① 迁移 + 落行归属 ─────────────────────────────────────────────────


class TestMigrationAndAttribution:
    def test_tenant_columns_added_backfilled_and_indexed(self, tmp_path):
        db = tmp_path / "learn.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_COURSES_DDL)
        conn.execute(OLD_CHAPTERS_DDL)
        conn.execute("INSERT INTO courses (course_id, title) VALUES ('course_old', '老课')")
        conn.execute(
            "INSERT INTO chapters (chapter_id, course_id, title) VALUES ('ch_old', 'course_old', '老章')"
        )
        conn.commit()
        conn.close()
        eng = CourseEngine(storage_dir=tmp_path, db_path=str(db))
        course = eng.get_course("course_old")
        cols_c = [r[1] for r in _rows(db, "PRAGMA table_info(courses)")]
        cols_h = [r[1] for r in _rows(db, "PRAGMA table_info(chapters)")]
        idx_c = [r[1] for r in _rows(db, "PRAGMA index_info(idx_courses_tenant)")]
        idx_h = [r[1] for r in _rows(db, "PRAGMA index_info(idx_chapters_tenant)")]
        assert "tenant_id" in cols_c and "tenant_id" in cols_h
        # 存量行回填 default（与生产数据分布一致），租户索引真建（补列之后建不炸）
        assert _rows(db, "SELECT tenant_id FROM courses") == [("default",)]
        assert _rows(db, "SELECT tenant_id FROM chapters") == [("default",)]
        assert idx_c and idx_h
        assert course.tenant_id == "default"
        assert course.chapters[0].tenant_id == "default"

    def test_course_row_attributed_and_sticky(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="A 的课", tenant_id="tenant-a")
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM courses") == [("tenant-a",)]
        # update 往返粘性（字段置空/改名不洗回 default）
        eng.update_course(course.course_id, title="改名", tenant_id="tenant-a")
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM courses") == [("tenant-a",)]
        # 重启（同库新实例）回载粘性
        eng2 = CourseEngine(storage_dir=tmp_path, db_path=str(tmp_path / "learn.db"))
        assert eng2.get_course(course.course_id).tenant_id == "tenant-a"

    def test_chapter_rows_inherit_course_tenant(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="继承验证", tenant_id="tenant-a")
        ch = eng.add_chapter(course.course_id, title="ch1", tenant_id="tenant-a")
        assert ch.tenant_id == "tenant-a"
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM chapters") == [("tenant-a",)]
        # _save_course 全量重写章节行后继承键不丢
        eng.mark_chapter(course.course_id, ch.chapter_id, True, tenant_id="tenant-a")
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM chapters") == [("tenant-a",)]

    def test_legacy_json_migrates_to_default(self, tmp_path):
        (tmp_path / "course_legacy0001.json").write_text(
            '{"course_id": "course_legacy0001", "title": "旧课", "chapters": '
            '[{"chapter_id": "ch_l1", "title": "旧章", "content": "正文"}]}'
        )
        eng = _engine(tmp_path)
        course = eng.get_course("course_legacy0001")
        assert course.tenant_id == "default"  # 旧 JSON=历史单租户数据
        assert course.chapters[0].tenant_id == "default"
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM courses") == [("default",)]


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFiltering:
    def test_list_and_stats_isolated(self, tmp_path):
        eng = _engine(tmp_path)
        a = eng.create_course(title="A1", tenant_id="tenant-a")
        eng.add_chapter(a.course_id, title="c1", tenant_id="tenant-a")
        eng.add_chapter(a.course_id, title="c2", tenant_id="tenant-a")
        eng.create_course(title="B1", tenant_id="tenant-b")
        eng.create_course(title="D1", tenant_id="default")
        # 显式租户=恒按租户过滤（B 恒读不到 A 的行）
        assert [c.course_id for c in eng.list_courses(tenant_id="tenant-a")] == [a.course_id]
        assert [c.title for c in eng.list_courses(tenant_id="tenant-b")] == ["B1"]
        st_b = eng.stats(tenant_id="tenant-b")
        assert st_b["total_courses"] == 1 and st_b["total_chapters"] == 0
        st_a = eng.stats(tenant_id="tenant-a")
        assert st_a["total_courses"] == 1 and st_a["total_chapters"] == 2
        # status 过滤与租户过滤叠加（tenant-b 的 not_started 只见自己）
        assert [c.title for c in eng.list_courses(status="not_started", tenant_id="tenant-b")] == [
            "B1"
        ]

    def test_system_view_unfiltered(self, tmp_path):
        # tenant_id=None=系统视图（search.py 跨模块消费等无身份轴路径）
        eng = _engine(tmp_path)
        eng.create_course(title="A1", tenant_id="tenant-a")
        eng.create_course(title="B1", tenant_id="tenant-b")
        assert len(eng.list_courses()) == 2
        assert eng.stats()["total_courses"] == 2
        assert eng.isolation.stats()["total_access_checks"] == 0  # 系统视图零审计噪音


# ── ③ IsolationEngine 闸门真执行 ───────────────────────────────────────


class TestEngineGates:
    def test_cross_tenant_read_update_denied(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="A 的课", tenant_id="tenant-a")
        with pytest.raises(CourseAccessDenied) as exc:
            eng.get_course(course.course_id, tenant_id="tenant-b")
        assert "different tenant" in exc.value.reason  # 真因如实携带
        with pytest.raises(CourseAccessDenied):
            eng.update_course(course.course_id, title="B 篡改", tenant_id="tenant-b")
        # 拒绝后行存活且未被改写；同租户正对照通过
        assert eng.get_course(course.course_id, tenant_id="tenant-a").title == "A 的课"
        assert (
            eng.update_course(course.course_id, title="A 自改", tenant_id="tenant-a").title
            == "A 自改"
        )

    def test_cross_tenant_delete_denied_row_survives(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="A 的课", tenant_id="tenant-a")
        with pytest.raises(CourseAccessDenied):
            eng.delete_course(course.course_id, tenant_id="tenant-b")
        eng2 = CourseEngine(storage_dir=tmp_path, db_path=str(tmp_path / "learn.db"))
        assert eng2.get_course(course.course_id) is not None  # 行存活（DB 回读硬证）
        # 同租户删除 + 级联按行租户键清（自己行全清）
        ch = eng.add_chapter(course.course_id, title="c1", tenant_id="tenant-a")
        assert eng.delete_course(course.course_id, tenant_id="tenant-a") is True
        assert _rows(tmp_path / "learn.db", "SELECT COUNT(*) FROM courses") == [(0,)]
        assert _rows(tmp_path / "learn.db", "SELECT COUNT(*) FROM chapters") == [(0,)]
        assert ch  # 消除未用告警

    def test_cross_tenant_chapter_ops_denied(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="A 的课", tenant_id="tenant-a")
        ch = eng.add_chapter(course.course_id, title="c1", tenant_id="tenant-a")
        denied_ops = [
            lambda: eng.add_chapter(course.course_id, title="B 章", tenant_id="tenant-b"),
            lambda: eng.update_chapter(
                course.course_id, ch.chapter_id, content="B 篡", tenant_id="tenant-b"
            ),
            lambda: eng.mark_chapter(course.course_id, ch.chapter_id, True, tenant_id="tenant-b"),
            lambda: eng.add_quiz(
                course.course_id,
                ch.chapter_id,
                [{"question": "q", "options": ["a"], "correct_index": 0}],
                tenant_id="tenant-b",
            ),
            lambda: eng.get_quiz(course.course_id, ch.chapter_id, tenant_id="tenant-b"),
            lambda: eng.delete_chapter(course.course_id, ch.chapter_id, tenant_id="tenant-b"),
        ]
        for op in denied_ops:
            with pytest.raises(CourseAccessDenied):
                op()
        # 章节行未被改写/删除（读权威=课程闸门，章节行=课程行继承）
        assert eng.get_course(course.course_id, tenant_id="tenant-a").chapters[0].quiz == []
        # 同租户正对照（全通）
        assert eng.add_chapter(course.course_id, title="A 章", tenant_id="tenant-a")
        assert eng.mark_chapter(course.course_id, ch.chapter_id, True, tenant_id="tenant-a")
        assert eng.delete_chapter(course.course_id, ch.chapter_id, tenant_id="tenant-a")

    def test_gate_audit_traces(self, tmp_path):
        eng = _engine(tmp_path)
        course = eng.create_course(title="A 的课", tenant_id="tenant-a")
        with pytest.raises(CourseAccessDenied):
            eng.get_course(course.course_id, tenant_id="tenant-b")
        eng.get_course(course.course_id, tenant_id="tenant-a")
        eng.list_courses(tenant_id="tenant-b")
        log = eng.isolation.get_access_log(tenant_id="ns-tenant-b")
        blocked = [
            x
            for x in log
            if x["resource_type"] == "course"
            and x["allowed"] is False
            and x["resource_id"] == f"ns-tenant-a:{course.course_id}"  # ns 对账硬断言
        ]
        assert blocked and "namespace mismatch" in blocked[0]["reason"]
        bulk = [x for x in log if x["action"] == "list" and x["resource_id"] == "ns-tenant-b:*"]
        assert bulk and bulk[0]["allowed"] is True
        allowed = eng.isolation.get_access_log(tenant_id="ns-tenant-a")
        assert any(x["allowed"] is True and x["resource_type"] == "course" for x in allowed)
        stats = eng.isolation.stats()
        assert stats["total_access_checks"] >= 3  # 数据面真咨询隔离引擎
        assert "course" in stats["by_resource_type"]


# ── ④ API 层：认证派生租户 → 行归属 → 跨租户不可见 全链 ────────────────


class TestAPI:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        ent_db = tmp_path / "enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        return ent_db

    @pytest.fixture()
    def api(self, tmp_path, monkeypatch):
        from src.api import learn as learn_api

        tmp_engine = CourseEngine(storage_dir=tmp_path, db_path=str(tmp_path / "learn.db"))
        monkeypatch.setattr(learn_api, "engine", tmp_engine)
        app = FastAPI()
        app.include_router(learn_api.router, prefix="/api/learn")
        with TestClient(app) as c:
            yield c

    def test_auth_to_visibility_full_chain(self, api, env, tmp_path):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 创建课程：行归属从认证身份派生落库（无自报字段）
        resp = api.post("/api/learn/courses", json={"title": "A 课"}, headers=tok_a)
        assert resp.status_code == 200
        cid = resp.json()["id"]
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM courses") == [("tenant-a",)]
        # A 章节写入继承课程行租户
        ch_resp = api.post(
            f"/api/learn/courses/{cid}/chapters", json={"title": "c1"}, headers=tok_a
        )
        assert ch_resp.status_code == 200
        ch_id = ch_resp.json()["id"]
        assert _rows(tmp_path / "learn.db", "SELECT tenant_id FROM chapters") == [("tenant-a",)]

        # B 冒读写电池：by-ID 9 口全 403 带真因（修前 200/404 反证面）
        battery = [
            ("GET", f"/api/learn/courses/{cid}", None),
            ("PUT", f"/api/learn/courses/{cid}", {"title": "B 篡改"}),
            ("DELETE", f"/api/learn/courses/{cid}", None),
            ("POST", f"/api/learn/courses/{cid}/chapters", {"title": "B 章"}),
            ("PUT", f"/api/learn/courses/{cid}/chapters/{ch_id}", {"content": "B 篡"}),
            ("DELETE", f"/api/learn/courses/{cid}/chapters/{ch_id}", None),
            ("POST", f"/api/learn/courses/{cid}/chapters/{ch_id}/mark", {"completed": True}),
            ("GET", f"/api/learn/courses/{cid}/chapters/{ch_id}/quiz", None),
            ("POST", f"/api/learn/courses/{cid}/chapters/{ch_id}/quiz", {"questions": []}),
        ]
        for method, url, body in battery:
            r = api.request(method, url, json=body, headers=tok_b)
            assert r.status_code == 403, f"{method} {url} -> {r.status_code}"
            assert "different tenant" in r.json()["detail"]
        # A 正对照（同一批口全通）
        assert api.get(f"/api/learn/courses/{cid}", headers=tok_a).status_code == 200
        assert (
            api.put(
                f"/api/learn/courses/{cid}", json={"title": "A 自改"}, headers=tok_a
            ).status_code
            == 200
        )
        assert (
            api.post(
                f"/api/learn/courses/{cid}/chapters/{ch_id}/mark",
                json={"completed": True},
                headers=tok_a,
            ).status_code
            == 200
        )

        # 三面隔离：列表互不可见（B/匿名恒不见 A 行）
        lst_a = api.get("/api/learn/courses", headers=tok_a).json()
        assert [c["id"] for c in lst_a["courses"]] == [cid] and lst_a["count"] == 1
        assert api.get("/api/learn/courses", headers=tok_b).json()["count"] == 0
        assert api.get("/api/learn/courses").json()["count"] == 0

        # B 删 A 的课被拒后行存活（A 仍可见）
        assert api.delete(f"/api/learn/courses/{cid}", headers=tok_b).status_code == 403
        assert api.get(f"/api/learn/courses/{cid}", headers=tok_a).status_code == 200

    def test_anonymous_default_contract_unchanged(self, api, env):
        # 匿名写→'default' 契约不变；课程形状逐键不变（修前契约）
        resp = api.post("/api/learn/courses", json={"title": "匿名课"})
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {
            "id",
            "title",
            "description",
            "tags",
            "topics",
            "domain",
            "knowledge_ids",
            "totalChapters",
            "completedChapters",
            "status",
            "generated_by",
            "createdAt",
            "updatedAt",
        }
        cid = resp.json()["id"]
        assert api.get(f"/api/learn/courses/{cid}").status_code == 200
        # chapters 内嵌形状逐键不变
        ch = api.post(f"/api/learn/courses/{cid}/chapters", json={"title": "c1"}).json()
        assert set(ch.keys()) == {
            "id",
            "title",
            "content",
            "order",
            "completed",
            "completedAt",
            "quiz",
        }
        # stats 形状逐键不变（修前契约：全局 5 键）
        st = api.get("/api/learn/stats").json()
        assert set(st.keys()) == {
            "total_courses",
            "total_chapters",
            "completed_chapters",
            "pending_chapters",
            "reviewing_courses",
            "by_status",
        }
        assert st["total_courses"] == 1  # 匿名=default 视图只见 default 行
        assert api.delete(f"/api/learn/courses/{cid}").json() == {"status": "deleted"}

    def test_cross_tenant_list_stats_invisible(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        api.post("/api/learn/courses", json={"title": "A 课"}, headers=tok_a)
        api.post("/api/learn/courses", json={"title": "B 课"}, headers=tok_b)
        api.post("/api/learn/courses", json={"title": "默认课"})  # 匿名=default
        # 列表三面互不可见
        assert api.get("/api/learn/courses", headers=tok_a).json()["courses"][0]["title"] == "A 课"
        assert api.get("/api/learn/courses", headers=tok_b).json()["courses"][0]["title"] == "B 课"
        assert api.get("/api/learn/courses").json()["courses"][0]["title"] == "默认课"
        # /stats 恒按派生租户（禁泄露全局分布：A 只见 1 不是 3）
        assert api.get("/api/learn/stats", headers=tok_a).json()["total_courses"] == 1
        assert api.get("/api/learn/stats", headers=tok_b).json()["total_courses"] == 1
        assert api.get("/api/learn/stats").json()["total_courses"] == 1

    def test_missing_course_404_contract(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        # 缺行 404 契约不变（非 403——缺行与跨租户可区分是既有语义，③b-1 先例）
        for headers in ({}, tok_a):
            assert api.get("/api/learn/courses/course_nope", headers=headers).status_code == 404
            assert api.delete("/api/learn/courses/course_nope", headers=headers).status_code == 404
            assert (
                api.post(
                    "/api/learn/courses/course_nope/chapters",
                    json={"title": "x"},
                    headers=headers,
                ).status_code
                == 404
            )

    def test_bad_token_401(self, api, env):
        bad = {"Authorization": "Bearer not-a-token"}
        assert api.get("/api/learn/courses", headers=bad).status_code == 401
        assert api.post("/api/learn/courses", json={"title": "x"}, headers=bad).status_code == 401

    def test_health_system_view_contract(self, api, env):
        # /health=引擎健康面（系统视图全局统计不闸——诚实边界，与 proactive /status 同）
        api.post("/api/learn/courses", json={"title": "A 课"})
        resp = api.get("/api/learn/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok" and body["component"] == "OpenLearn"
        assert body["stats"]["total_courses"] == 1

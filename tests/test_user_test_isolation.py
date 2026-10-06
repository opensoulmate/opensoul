"""user API测试隔离钉——测试注册绝不写生产users表（2026-10-06 live测试普查轮）。

背景：tests/test_user.py走live :8090注册→生产 data/opensoul.db users表积
173/179行testuser_/logintest_/metest_测试账号（每次全量跑+3行，无删除端点
永不自清——03:27轮遗留#1残余"测试注册写生产users表"）。本轮把test_user.py
写/认证用例迁到conftest user_client隔离面（真实router+tmp users库+tmp
RBAC策略路径），本文件=防复发钉：
- 行为面：隔离面注册→登录→me全链在真实router代码上跑通（语义保真）
- 数据面：隔离面注册零生产写入（生产users表计数不变+行只落tmp库）
- 副作用面：register的add_role落tmp策略文件，生产运行时策略/git种子零接触
- 静态面：tests/内 /api/user/register 写用例必须走user_client（唯一豁免=
  rbac卫生live部署证明钉，且必须自带自清）；test_user.py只许一条live只读冒烟
- 泄漏探测器：生产users表测试命名行恒为0（任何live写者漏自清即红灯）
"""

import ast
import sqlite3
import uuid
from pathlib import Path

import src.services.permission as permission

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = Path(__file__).parent

# 生产库路径在收集期固化（fixture会把settings.database_url换成tmp——
# 运行期再解析就变成tmp自己比tmp自己，实测红灯教训）
from src.config import settings as _settings  # noqa: E402

_PROD_DB_PATH = _settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")

# 全tests/唯一豁免的live注册写用例（rbac卫生部署证明钉，自带自清）
_LIVE_REGISTER_ALLOWLIST = {"test_live_register_leaves_git_file_untouched"}


def _prod_test_pattern_rows() -> list[str]:
    """生产users表测试命名行（permission._is_test_user单一真源判定）。"""
    with sqlite3.connect(_PROD_DB_PATH) as conn:
        names = [r[0] for r in conn.execute("SELECT username FROM users").fetchall()]
    return [n for n in names if permission._is_test_user(n)]


class TestUserDataPlaneIsolation:
    def test_register_writes_only_tmp_db(self, user_client):
        """核心隔离钉：隔离面注册后生产users表计数不变，行只落tmp库。"""
        before = len(_prod_test_pattern_rows())
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        resp = user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": f"{username}@test.com",
                "password": "iso12345",
            },
        )
        assert resp.status_code == 200, resp.text
        # 生产零写入
        assert len(_prod_test_pattern_rows()) == before, "隔离面注册写穿到了生产users表"
        # tmp库真写入（数据面隔离≠没写入）
        tmp_db = user_client.app.state.user_client_db
        with sqlite3.connect(tmp_db) as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE username = ?", (username,)
            ).fetchone()
        assert row is not None, "注册行没落tmp库——隔离面数据面断线了"

    def test_full_auth_flow_on_isolated_face(self, user_client):
        """语义保真钉：注册→登录→me全链在真实router/auth代码上跑通。"""
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        resp = user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": f"{username}@test.com",
                "password": "flow12345",
            },
        )
        assert resp.status_code == 200, resp.text
        login = user_client.post(
            "/api/user/login",
            data={"username": username, "password": "flow12345"},
        )
        assert login.status_code == 200, login.text
        me = user_client.get(
            "/api/user/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.status_code == 200, me.text
        assert me.json()["username"] == username

    def test_rbac_side_effect_lands_in_tmp_policy(self, user_client):
        """register的add_role副作用落tmp策略文件，生产运行时策略零接触。"""
        import os

        runtime_policy = os.path.expanduser("~/.hermes/opensoul/rbac_policy.csv")
        before = Path(runtime_policy).read_bytes() if Path(runtime_policy).exists() else b""
        username = f"isolation_realuser_{uuid.uuid4().hex[:4]}"  # 非测试命名=不被剔除
        resp = user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": f"{username}@test.com",
                "password": "rbac12345",
            },
        )
        assert resp.status_code == 200, resp.text
        # 非测试命名的角色行应持久化在tmp策略文件（env覆盖路径）
        tmp_policy = Path(permission._resolve_policy_path())
        assert tmp_policy.exists(), "register的add_role没写tmp策略文件"
        assert f"g, {username}, user" in tmp_policy.read_text(encoding="utf-8")
        # 生产运行时策略与git种子都不许被碰
        after = Path(runtime_policy).read_bytes() if Path(runtime_policy).exists() else b""
        assert after == before, "隔离面注册弄脏了生产运行时策略文件"

    def test_fixture_swaps_db_pool_and_url(self, user_client):
        """接线钉：auth.db_pool确为tmp实例（不是生产单例），database_url指向tmp。"""
        import src.services.auth as auth_mod
        from src.config import settings
        from src.database.postgres import db_pool as prod_pool

        assert auth_mod.db_pool is not prod_pool, "auth.db_pool没换——隔离面脱线"
        tmp_db = user_client.app.state.user_client_db
        assert tmp_db.endswith("users.db")
        assert settings.database_url.endswith("users.db")
        assert _PROD_DB_PATH != tmp_db


class TestStaticGuards:
    def test_register_calls_only_on_isolated_face(self):
        """静态钉：tests/内 /api/user/register 写用例必须走user_client。

        唯一豁免=rbac卫生live部署证明钉（必须自带自清_purge_test_user_row）。
        """
        offenders = []
        for py in sorted(TESTS_DIR.glob("test_*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                # 只认真实调用点 client/user_client.post("/api/user/register", ...)
                # （裸字面量比较不算——本钉自身的扫描代码就含该字面量，实测红灯教训）
                has_register = any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "post"
                    and bool(c.args)
                    and isinstance(c.args[0], ast.Constant)
                    and c.args[0].value == "/api/user/register"
                    for c in ast.walk(node)
                )
                if not has_register:
                    continue
                params = {a.arg for a in node.args.args}
                if "user_client" in params:
                    continue
                if node.name in _LIVE_REGISTER_ALLOWLIST:
                    src_text = py.read_text(encoding="utf-8")
                    assert "_purge_test_user_row" in src_text, (
                        f"{py.name}: 豁免live注册钉必须自带自清"
                    )
                    continue
                offenders.append(f"{py.name}:{node.lineno}:{node.name}")
        assert offenders == [], f"live注册写用例未走user_client隔离面: {offenders}"

    def test_user_tests_keep_single_readonly_live_smoke(self):
        """静态钉：test_user.py只许一条live只读冒烟（health），写面全在user_client。"""
        tree = ast.parse((TESTS_DIR / "test_user.py").read_text(encoding="utf-8"))
        live_fns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                params = {a.arg for a in node.args.args}
                if "client" in params:
                    live_fns.append(node.name)
        assert live_fns == ["test_health"], f"test_user.py出现多余live用例: {live_fns}"


class TestProductionHygiene:
    def test_production_users_table_free_of_test_rows(self):
        """泄漏探测器：生产users表测试命名行恒为0（live写者漏自清即红灯）。"""
        leaked = _prod_test_pattern_rows()
        assert leaked == [], f"生产users表残留测试账号: {leaked[:5]}（共{len(leaked)}行）"

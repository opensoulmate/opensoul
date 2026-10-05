"""RBAC策略文件卫生钉——git种子模板与运行时策略分离（2026-10-06轮）。

背景：tests/test_user.py走live :8090注册→服务进程add_role→_save_policy写
config/rbac_policy.csv（git跟踪文件）——每次全量测试追加3行，历史积300+测试行
（清账前319行里300+是testuser_/logintest_/metest_产物），git工作区永久脏
（多轮dev-reports"sibling既存脏文件config/rbac_policy.csv"挂账）。

本轮改动（src/services/permission.py）：
1. 运行时策略迁出git：~/.hermes/opensoul/rbac_policy.csv（env OPENSOUL_RBAC_POLICY_PATH可覆盖）
2. 种子播种：运行时文件缺失时从config/rbac_policy.csv播种并剔除测试产物行（模板只读，永不写回）
3. 产生侧清理：_save_policy落盘前剔除测试产物行（测试用户名不进运行时策略）

本文件=防复发钉：种子模板不许再积测试行；运行期注册不许弄脏git工作区。
"""

import uuid
from pathlib import Path

import pytest

import src.services.permission as permission

REPO_ROOT = Path(__file__).parent.parent
GIT_SEED = REPO_ROOT / "config" / "rbac_policy.csv"


def _test_rows(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 2 and parts[0] == "g" and permission._is_test_user(parts[1]):
            out.append(line)
    return out


@pytest.fixture()
def isolated_rbac(tmp_path, monkeypatch):
    """进程内隔离面：env指向tmp运行时策略 + enforcer单例复位（每测重建）。"""
    monkeypatch.setenv(permission._RUNTIME_POLICY_ENV, str(tmp_path / "rbac_policy.csv"))
    monkeypatch.setattr(permission, "_enforcer", None)
    yield tmp_path
    monkeypatch.setattr(permission, "_enforcer", None)


class TestGitSeedHygiene:
    def test_git_seed_template_has_no_test_rows(self):
        """git跟踪的种子模板本身不许含测试产物行（存量清账后防复发）。"""
        text = GIT_SEED.read_text(encoding="utf-8")
        assert _test_rows(text) == [], f"种子模板混入测试行: {_test_rows(text)[:5]}"
        # 真实行/权限行必须还在（剔除不误杀）
        assert "g, climbing, user" in text
        assert "p, admin, knowledge, *" in text


class TestPolicyPathResolution:
    def test_env_override_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv(permission._RUNTIME_POLICY_ENV, str(tmp_path / "x.csv"))
        assert permission._resolve_policy_path() == str(tmp_path / "x.csv")

    def test_default_lives_outside_git_config(self, monkeypatch):
        monkeypatch.delenv(permission._RUNTIME_POLICY_ENV, raising=False)
        resolved = permission._resolve_policy_path()
        assert resolved.endswith(".hermes/opensoul/rbac_policy.csv")
        assert "/config/" not in resolved  # 绝不落回git目录


class TestSeedMigration:
    def test_seed_strips_test_rows_keeps_real_rows(self, isolated_rbac):
        enforcer = permission.get_enforcer()
        runtime = Path(permission._resolve_policy_path())
        assert runtime.exists(), "get_enforcer必须先播种再构造Enforcer"
        text = runtime.read_text(encoding="utf-8")
        assert _test_rows(text) == []
        assert "g, climbing, user" in text
        assert "p, admin, knowledge, *" in text
        # 播种后策略语义完好：admin继承admin角色、climbing有user权限
        assert enforcer.enforce("admin", "knowledge", "*") is True
        assert enforcer.enforce("climbing", "knowledge", "read") is True

    def test_existing_runtime_file_not_reseeded(self, isolated_rbac):
        runtime = Path(permission._resolve_policy_path())
        runtime.write_text("p, custom, obj, act\n", encoding="utf-8")
        permission.get_enforcer()
        assert runtime.read_text(encoding="utf-8") == "p, custom, obj, act\n"

    def test_seed_strips_test_rows_from_dirty_template(self, isolated_rbac, tmp_path, monkeypatch):
        """模板脏了也只播干净内容（播种剔除逻辑独立于模板现状）。"""
        dirty = tmp_path / "dirty_template.csv"
        dirty.write_text(
            "p, admin, knowledge, *\n"
            "g, testuser_0123abcd, user\n"
            "g, logintest_deadbeef, user\n"
            "g, metest_cafe0001, user\n"
            "g, testuser_nothex, user\n"  # 不匹配8位hex→保留（不误杀）
            "g, climbing, user\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(permission, "_seed_policy_path", str(dirty))
        permission.get_enforcer()
        text = Path(permission._resolve_policy_path()).read_text(encoding="utf-8")
        assert "testuser_0123abcd" not in text
        assert "logintest_deadbeef" not in text
        assert "metest_cafe0001" not in text
        assert "testuser_nothex" in text
        assert "g, climbing, user" in text


class TestSavePrunesTestRows:
    def test_test_user_role_never_persisted(self, isolated_rbac):
        """产生侧清理：注册测试用户→add_role→保存后运行时文件零测试行。"""
        name = f"testuser_{uuid.uuid4().hex[:8]}"
        permission.add_role(name, "user")
        assert permission.get_user_roles(name) == [], "测试角色不留在内存"
        text = Path(permission._resolve_policy_path()).read_text(encoding="utf-8")
        assert name not in text, "测试角色不落盘"

    def test_save_prunes_legacy_test_rows(self, isolated_rbac):
        """存量剔除：任何一次保存都会把内存里的历史测试行清出运行时策略。"""
        enforcer = permission.get_enforcer()
        enforcer.add_role_for_user("testuser_feedface", "user")  # 模拟旧内存残留
        permission.add_role("hygiene_realuser", "user")  # 触发一次保存
        text = Path(permission._resolve_policy_path()).read_text(encoding="utf-8")
        assert "testuser_feedface" not in text
        assert "g, hygiene_realuser, user" in text
        permission.remove_role("hygiene_realuser", "user")

    def test_non_test_names_not_pruned(self, isolated_rbac):
        """不误杀：不合测试命名模式的名字照常持久化。"""
        permission.add_role("testuser_special", "user")  # 无8位hex后缀
        text = Path(permission._resolve_policy_path()).read_text(encoding="utf-8")
        assert "g, testuser_special, user" in text
        permission.remove_role("testuser_special", "user")


class TestLiveRegisterGitIsolation:
    def test_live_register_leaves_git_file_untouched(self, client):
        """端到端钉（live :8090）：真实注册写路径不许碰git跟踪文件。

        服务重启进新代码后此钉=运行时策略已迁出git的部署证明；若服务还是旧代码，
        注册会弄脏config/rbac_policy.csv→红灯=该重启了。
        """
        before = GIT_SEED.read_bytes()
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": f"{username}@test.com",
                "password": "testpass123",
            },
        )
        assert resp.status_code in (200, 400), resp.text
        assert GIT_SEED.read_bytes() == before, "live注册弄脏了git跟踪的种子模板"

"""H1-②b-⑥ 用户风险偏好接线——UserMemory.should_auto_execute 真进执行门禁决策。

测的是 src/api/brain.py::think 策略决策对 UserMemory.should_auto_execute 的真实
消费（收紧/放宽/底线/旁路）+ 声明面 GET/PUT /api/brain/preferences（修前
UserMemory.set 全仓零调用=偏好无写入面）。

数据面零依赖（tmp SQLitePool 全隔离，绝不写生产 data/opensoul.db）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def pref_client(tmp_path, monkeypatch):
    """brain router + tmp SQLitePool（lifespan 真连接）——conf_client 同款路线。"""
    from contextlib import asynccontextmanager

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c


def _put(client, prefs, **params):
    return client.put("/api/brain/preferences", json={"preferences": prefs, **params})


LOW_RISK_INPUT = "帮我修复 foo.py 的 bug"


def _medium_input(tmp_path):
    """4 个真实存在的文件 → target_files=4 → 多文件改动 medium（确定性）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    names = []
    for i in range(4):
        name = f"f{i}.py"
        (repo / name).write_text("x = 1\n")
        names.append(name)
    return "重构 " + " ".join(names), str(repo)


# ── ① 声明面（GET/PUT /preferences）──────────────────────────────────


class TestPreferenceFace:
    def test_get_defaults_five_keys(self, pref_client):
        resp = pref_client.get("/api/brain/preferences")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["preferences"].keys()) == {
            "edit_mode_preference",
            "risk_tolerance",
            "auto_confirm_low_risk",
            "always_backup_before_edit",
            "notification_level",
        }
        assert data["preferences"]["risk_tolerance"] == "medium"
        assert data["preferences"]["auto_confirm_low_risk"] is True
        assert data["tenant_id"] == "default"
        assert data["user_id"] == "default"

    def test_put_roundtrip_partial_update(self, pref_client):
        resp = _put(pref_client, {"risk_tolerance": "high", "auto_confirm_low_risk": False})
        assert resp.status_code == 200
        prefs = resp.json()["preferences"]
        assert prefs["risk_tolerance"] == "high"
        assert prefs["auto_confirm_low_risk"] is False
        # 未给的键保持缺省（部分更新，非整包覆盖）
        assert prefs["always_backup_before_edit"] is True
        assert prefs["edit_mode_preference"] == "patch"
        got = pref_client.get("/api/brain/preferences").json()["preferences"]
        assert got == prefs  # 写读同源真落库

    def test_put_validation_battery(self, pref_client):
        cases = [
            ({"no_such_key": 1}, "unknown preference key"),
            ({"risk_tolerance": "extreme"}, "risk_tolerance"),
            ({"risk_tolerance": 3}, "risk_tolerance"),
            ({"auto_confirm_low_risk": "yes"}, "auto_confirm_low_risk"),
            ({"always_backup_before_edit": 1}, "always_backup_before_edit"),
            ({"edit_mode_preference": "rewrite"}, "edit_mode_preference"),
            ({"notification_level": "  "}, "notification_level"),
            ({}, "non-empty object"),
        ]
        for prefs, needle in cases:
            resp = _put(pref_client, prefs)
            assert resp.status_code == 422, f"unexpected pass: {prefs}"
            assert needle in resp.json()["detail"]
        # 422 零副作用：一通乱写后偏好仍是缺省
        assert (
            pref_client.get("/api/brain/preferences").json()["preferences"]["risk_tolerance"]
            == "medium"
        )

    def test_scope_isolation_by_user(self, pref_client):
        assert _put(pref_client, {"risk_tolerance": "high"}, user_id="u1").status_code == 200
        assert (
            pref_client.get("/api/brain/preferences", params={"user_id": "u1"}).json()[
                "preferences"
            ]["risk_tolerance"]
            == "high"
        )
        assert (
            pref_client.get("/api/brain/preferences", params={"user_id": "u2"}).json()[
                "preferences"
            ]["risk_tolerance"]
            == "medium"
        )


# ── ② think 决策面真消费 ───────────────────────────────────────────


class TestThinkConsumesPreference:
    def test_default_prefs_mapping_unchanged(self, pref_client):
        """缺省偏好=行为契约零变化：low→auto、medium→confirm_required（接线前同）。"""
        r1 = pref_client.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT})
        d1 = r1.json()
        assert d1["risk"]["overall_level"] == "low"
        assert d1["decision"] == {
            "edit_mode": "patch",
            "execute_mode": "auto",
            "confirm_prompt": None,
        }
        assert d1["preference_allows_auto"] is True

    def test_default_prefs_medium_confirm(self, pref_client, tmp_path):
        msg, repo = _medium_input(tmp_path)
        data = pref_client.post(
            "/api/brain/think", json={"user_input": msg, "repo_root": repo}
        ).json()
        assert data["risk"]["overall_level"] == "medium"
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert data["decision"]["confirm_prompt"].startswith("需要确认：")
        assert data["preference_allows_auto"] is False  # tolerance medium 对 medium 不放行

    def test_auto_confirm_false_tightens_low_risk(self, pref_client):
        """收紧面：auto_confirm_low_risk=False → 低风险也不自动（修前 auto）。"""
        assert _put(pref_client, {"auto_confirm_low_risk": False}).status_code == 200
        data = pref_client.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT}).json()
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert "用户偏好收紧" in data["decision"]["confirm_prompt"]
        assert data["preference_allows_auto"] is False

    def test_user_floor_survives_emotion_relax(self, pref_client):
        """用户偏好底线不被 5b 情绪放宽：joy+高信心的放宽规则不得撤销用户要的确认。"""
        _put(pref_client, {"auto_confirm_low_risk": False})
        body = {
            "user_input": "太棒了 开心 帮我修复 foo.py 的 bug",
            "agent_confidence": 1.0,
        }
        data = pref_client.post("/api/brain/think", json=body).json()
        assert data["emotion"]["primary"] == "joy"  # 情绪轴真驱动
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert "用户偏好收紧" in data["decision"]["confirm_prompt"]
        # 对照组：偏好改回容许自动后同一放宽规则生效 → auto（证明放宽规则真跑到、
        # 拦住它的只有用户偏好底线）。think 无 user_id 轴（ThinkRequest 契约），
        # 偏好域=identity 派生，对照只能翻转同域偏好。
        _put(pref_client, {"auto_confirm_low_risk": True})
        data2 = pref_client.post(
            "/api/brain/think",
            json={**body, "user_input": body["user_input"] + " "},
        ).json()
        assert data2["decision"]["execute_mode"] == "auto"

    def test_tolerance_high_loosens_medium(self, pref_client, tmp_path):
        """放宽面：risk_tolerance=high（用户显式选择）→ medium 风险映射 confirm→auto。"""
        msg, repo = _medium_input(tmp_path)
        _put(pref_client, {"risk_tolerance": "high"})
        data = pref_client.post(
            "/api/brain/think", json={"user_input": msg, "repo_root": repo}
        ).json()
        assert data["risk"]["overall_level"] == "medium"
        assert data["decision"]["execute_mode"] == "auto"
        assert data["decision"]["confirm_prompt"] is None
        assert data["preference_allows_auto"] is True

    def test_tolerance_high_never_loosens_high(self, pref_client):
        """底线：should_auto_execute(high)=False 恒真 → high 风险不被偏好放宽。"""
        _put(pref_client, {"risk_tolerance": "high", "auto_confirm_low_risk": True})
        data = pref_client.post("/api/brain/think", json={"user_input": "永久删除生产数据"}).json()
        assert data["risk"]["overall_level"] == "high"
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert data["preference_allows_auto"] is False

    def test_deny_bottom_line_untouched(self, pref_client, monkeypatch):
        """deny 风险底线：偏好既不放宽也不改写 deny（与 should_auto_execute 自洽）。"""
        import src.cortex.risk_assessor as ra
        from src.models.cognitive import Risk, RiskAssessment

        async def fake_assess(self, intent, task_ctx):
            return RiskAssessment(
                risks=[Risk("核心文件修改", "critical", "y")],
                overall_level="critical",
                recommendation="风险过高，建议人工审查后执行",
            )

        monkeypatch.setattr(ra.RiskAssessor, "assess", fake_assess)
        _put(pref_client, {"risk_tolerance": "high", "auto_confirm_low_risk": False})
        data = pref_client.post(
            "/api/brain/think", json={"user_input": "改一下 foo.py", "agent_confidence": 1.0}
        ).json()
        assert data["decision"]["execute_mode"] == "deny"
        assert data["decision"]["confirm_prompt"].startswith("风险过高：")

    def test_pref_loosen_still_tightenable_by_low_confidence(self, pref_client, tmp_path):
        """偏好放宽只压过风险映射：5b 低信心收紧在其之上仍生效（agent 没把握要问人）。"""
        msg, repo = _medium_input(tmp_path)
        _put(pref_client, {"risk_tolerance": "high"})
        body = {
            "user_input": "担心这个改动有危险，" + msg,
            "repo_root": repo,
            "agent_confidence": 0.3,
        }
        data = pref_client.post("/api/brain/think", json=body).json()
        assert data["emotion"]["primary"] == "fear"
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert "信心不足" in data["decision"]["confirm_prompt"]
        # 对照：同输入缺省信心（0.8）→ 偏好放宽生效 auto
        data2 = pref_client.post(
            "/api/brain/think", json={k: v for k, v in body.items() if k != "agent_confidence"}
        ).json()
        assert data2["decision"]["execute_mode"] == "auto"

    def test_put_to_think_closed_loop(self, pref_client):
        """写→读→决策闭环：同一低风险输入随偏好往返翻转（真消费非装饰）。"""
        _put(pref_client, {"auto_confirm_low_risk": False})
        d1 = pref_client.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT}).json()
        assert d1["decision"]["execute_mode"] == "confirm_required"
        _put(pref_client, {"auto_confirm_low_risk": True})
        d2 = pref_client.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT}).json()
        assert d2["decision"]["execute_mode"] == "auto"

    def test_read_failure_bypass_visible(self, monkeypatch):
        """读取故障→偏好不参与（回落接线前行为）+加性键如实 None 禁静默。"""
        import src.api.brain as brain_api
        import src.database.postgres as pg_mod
        from src.database.postgres import SQLitePool

        monkeypatch.setattr(pg_mod, "db_pool", SQLitePool())  # 从未 connect
        app = FastAPI()
        app.include_router(brain_api.router, prefix="/api/brain")
        with TestClient(app) as c:
            data = c.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT}).json()
        assert data["decision"]["execute_mode"] == "auto"
        assert data["preference_allows_auto"] is None

    def test_decision_contract_additive_key_only(self, pref_client):
        """decision 键集逐键不变，偏好生效值是加性顶层键。"""
        data = pref_client.post("/api/brain/think", json={"user_input": LOW_RISK_INPUT}).json()
        assert set(data["decision"].keys()) == {"edit_mode", "execute_mode", "confirm_prompt"}
        assert "preference_allows_auto" in data


# ── ③ 静态接线（写了≠接线了）───────────────────────────────────────


class TestStaticWiring:
    def test_brain_calls_should_auto_execute(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parent.parent / "src" / "api" / "brain.py").read_text(
            encoding="utf-8"
        )
        assert "should_auto_execute" in text
        assert "from src.mind.user_memory import UserMemory" in text
        # 消费点不止声明：think 决策面与声明面都真构造 UserMemory
        assert text.count("UserMemory(") >= 3

"""P0-3 工具级权限引擎测试 — AgentScope语义 + kilocode分层 + mem0审计"""

import asyncio

import pytest

from src.immune.permission_engine import (
    BUILTIN_ASK,
    BUILTIN_HARD_DENY,
    PermissionBehavior,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
    PermissionService,
    PermissionStore,
    generate_suggestions,
    match_rule,
)


def make_engine(mode=PermissionMode.DEFAULT, rules=None, wds=None):
    return PermissionEngine(mode=mode, rules=rules or [], working_directories=wds or [])


def rule(tool, content, behavior, source="user", hard=False, risk="medium", seq=1):
    return PermissionRule(
        tool_name=tool,
        rule_content=content,
        behavior=PermissionBehavior(behavior),
        source=source,
        hard=hard,
        risk=risk,
        seq=seq,
        rule_id=f"test-{source}-{seq}",
    )


# ── 规则匹配（AgentScope match_rule分流） ──────────────────────


class TestMatchRule:
    def test_empty_content_matches_all(self):
        assert match_rule(None, "terminal", {"command": "ls"}) is True
        assert match_rule("", "terminal", {"command": "ls"}) is True

    def test_shell_substring(self):
        assert match_rule("sudo ", "terminal", {"command": "sudo apt update"}) is True
        assert match_rule("sudo ", "terminal", {"command": "ls"}) is False

    def test_shell_prefix_wildcard(self):
        assert match_rule("git:*", "terminal", {"command": "git push origin main"}) is True
        assert match_rule("git:*", "terminal", {"command": "grep git file"}) is False

    def test_file_glob(self):
        assert match_rule("src/**", "write_file", {"path": "src/app/main.py"}) is True
        assert match_rule("*.pem", "read_file", {"path": "/home/u/cert.pem"}) is True
        assert match_rule("*.pem", "read_file", {"path": "/home/u/readme.md"}) is False

    def test_file_path_segment(self):
        # "**/.ssh/**" 风格：路径包含该段即命中
        assert match_rule("**/.ssh/**", "read_file", {"path": "/home/user/.ssh/id_rsa"}) is True

    def test_other_tool_param_substring(self):
        assert match_rule("danger", "mcp_tool", {"query": "this is danger"}) is True
        assert match_rule("danger", "mcp_tool", {"query": "safe"}) is False


# ── 模式语义（AgentScope逐模式评估顺序） ───────────────────────


class TestModes:
    def test_explore_allows_read_only(self):
        d = make_engine(PermissionMode.EXPLORE).check("read_file", {"path": "a.py"})
        assert d.behavior == PermissionBehavior.ALLOW

    def test_explore_denies_write(self):
        d = make_engine(PermissionMode.EXPLORE).check("write_file", {"path": "a.py"})
        assert d.behavior == PermissionBehavior.DENY

    def test_explore_allow_rule_cannot_grant_write(self):
        """EXPLORE的只读保证不可被用户allow规则授予（AgentScope注释规格）"""
        e = make_engine(PermissionMode.EXPLORE, rules=[rule("write_file", "", "allow", seq=1)])
        d = e.check("write_file", {"path": "a.py"})
        assert d.behavior == PermissionBehavior.DENY

    def test_explore_read_only_shell_allowed(self):
        d = make_engine(PermissionMode.EXPLORE).check("terminal", {"command": "ls -la"})
        assert d.behavior == PermissionBehavior.ALLOW

    def test_explore_write_shell_denied(self):
        d = make_engine(PermissionMode.EXPLORE).check("terminal", {"command": "touch /tmp/x"})
        assert d.behavior == PermissionBehavior.DENY

    def test_default_asks_when_no_rule(self):
        d = make_engine(PermissionMode.DEFAULT).check("terminal", {"command": "python build.py"})
        assert d.behavior == PermissionBehavior.ASK
        assert d.requires_human is True
        assert d.decision_id  # ASK决策必须有审计ID

    def test_default_allow_rule_grants(self):
        e = make_engine(
            PermissionMode.DEFAULT, rules=[rule("terminal", "python:*", "allow", seq=1)]
        )
        d = e.check("terminal", {"command": "python build.py"})
        assert d.behavior == PermissionBehavior.ALLOW
        assert d.rule_source == "user"

    def test_accept_edits_auto_allows_in_workdir(self):
        e = make_engine(PermissionMode.ACCEPT_EDITS, wds=["/home/climbing/openmate"])
        d = e.check("write_file", {"path": "/home/climbing/openmate/src/a.tsx", "content": "x"})
        assert d.behavior == PermissionBehavior.ALLOW

    def test_accept_edits_asks_outside_workdir(self):
        e = make_engine(PermissionMode.ACCEPT_EDITS, wds=["/home/climbing/openmate"])
        d = e.check("write_file", {"path": "/etc/hosts", "content": "x"})
        assert d.behavior == PermissionBehavior.ASK

    def test_bypass_skips_unmatched_ask_fallback(self):
        d = make_engine(PermissionMode.BYPASS).check("terminal", {"command": "python build.py"})
        assert d.behavior == PermissionBehavior.ALLOW

    def test_bypass_still_honors_deny_rules(self):
        e = make_engine(PermissionMode.BYPASS, rules=[rule("terminal", "deploy:*", "deny", seq=1)])
        d = e.check("terminal", {"command": "deploy prod"})
        assert d.behavior == PermissionBehavior.DENY

    def test_dont_ask_never_returns_ask(self):
        """DONT_ASK不变式：一切ASK转换为DENY（无人值守安全档）"""
        e = make_engine(PermissionMode.DONT_ASK)
        d1 = e.check("terminal", {"command": "python build.py"})
        d2 = e.check("terminal", {"command": "sudo apt update"})  # builtin ask规则
        assert d1.behavior == PermissionBehavior.DENY
        assert d2.behavior == PermissionBehavior.DENY
        assert "DONT_ASK" in d2.decision_reason or "dont_ask" in d2.decision_reason

    def test_dont_ask_preserves_suggestions(self):
        d = make_engine(PermissionMode.DONT_ASK).check("terminal", {"command": "npm run build"})
        assert d.behavior == PermissionBehavior.DENY
        assert d.suggested_rules  # 被拒后仍告诉用户"加什么规则能放行"

    def test_read_only_tool_allowed_in_default(self):
        d = make_engine(PermissionMode.DEFAULT).check("search_files", {"pattern": "foo"})
        assert d.behavior == PermissionBehavior.ALLOW


# ── kilocode分层：hard否决 + findLast + provenance ─────────────


class TestLayering:
    def test_hard_deny_veto_in_bypass(self):
        """hard规则在BYPASS模式下依然否决（kilocode hardRuleset）"""
        e = make_engine(
            PermissionMode.BYPASS,
            rules=[rule("terminal", "rm -rf /", "deny", source="builtin", hard=True, seq=1)],
        )
        d = e.check("terminal", {"command": "rm -rf / --no-preserve-root"})
        assert d.behavior == PermissionBehavior.DENY
        assert "HARD" in d.message

    def test_deny_beats_allow(self):
        e = make_engine(
            PermissionMode.DEFAULT,
            rules=[
                rule("terminal", "deploy:*", "allow", seq=1),
                rule("terminal", "deploy prod", "deny", seq=2),
            ],
        )
        d = e.check("terminal", {"command": "deploy prod"})
        assert d.behavior == PermissionBehavior.DENY

    def test_session_allow_overrides_builtin_ask_findlast(self):
        """kilocode三层findLast：session层allow覆盖builtin层非hard ask"""
        e = make_engine(
            PermissionMode.ACCEPT_EDITS,
            rules=[
                rule("terminal", "curl ", "ask", source="builtin", seq=1),
                rule("terminal", "curl:*", "allow", source="session", seq=2),
            ],
        )
        d = e.check("terminal", {"command": "curl https://api.example.com"})
        assert d.behavior == PermissionBehavior.ALLOW
        assert d.rule_source == "session"

    def test_provenance_present(self):
        e = make_engine(
            PermissionMode.ACCEPT_EDITS,
            rules=[rule("terminal", "deploy:*", "deny", source="user", seq=1)],
        )
        d = e.check("terminal", {"command": "deploy staging"})
        assert d.rule_id
        assert d.rule_source == "user"
        assert d.rule_content == "deploy:*"
        assert "Rule[user]" in d.decision_reason

    def test_builtin_secrets_ask(self):
        e = make_engine(PermissionMode.ACCEPT_EDITS, wds=["/home/climbing"])
        d = e.check("read_file", {"path": "/home/climbing/.ssh/id_rsa"})
        assert d.behavior == PermissionBehavior.ASK
        assert d.rule_source == "builtin"
        assert d.requires_human is True

    def test_builtin_hard_deny_catastrophic(self):
        e = make_engine(PermissionMode.ACCEPT_EDITS)
        d = e.check("terminal", {"command": "rm -rf /"})
        assert d.behavior == PermissionBehavior.DENY
        assert d.rule_source == "builtin"

    def test_builtin_seeding_complete(self):
        assert len(BUILTIN_HARD_DENY) >= 5
        assert len(BUILTIN_ASK) >= 10

    # ── P0-10 毁机硬规则锚定（终验实证回归） ──

    def test_hard_deny_anchored_root_only(self):
        """毁机硬规则只锚定根目标：rm -rf /tmp/x 不得被 'rm -rf /' 子串误伤成 hard deny"""
        e = make_engine(PermissionMode.BYPASS)
        d = e.check("terminal", {"command": "rm -rf /tmp/p010_db"})
        # 不是 hard 否决；回落 builtin ask（删库类强制 ask，人工可批）
        assert d.behavior == PermissionBehavior.ASK
        assert d.requires_human is True

    def test_hard_deny_root_forms_still_blocked(self):
        """根毁灭形（/、/*、--no-preserve-root）仍 hard deny，BYPASS 不可覆盖"""
        e = make_engine(PermissionMode.BYPASS)
        for cmd in ("rm -rf /", "rm -rf /*", "rm -rf / --no-preserve-root"):
            d = e.check("terminal", {"command": cmd})
            assert d.behavior == PermissionBehavior.DENY, cmd
            assert "HARD" in d.message, cmd

    def test_hard_deny_anchored_chmod_chown(self):
        """chmod/chown 同类锚定：/tmp 目标走 ask，根目标 hard deny"""
        e = make_engine(PermissionMode.BYPASS)
        d = e.check("terminal", {"command": "chmod -R 777 /tmp/p010_x"})
        assert d.behavior == PermissionBehavior.ASK
        d = e.check("terminal", {"command": "chmod -R 777 /"})
        assert d.behavior == PermissionBehavior.DENY
        d = e.check("terminal", {"command": "chown -R /"})
        assert d.behavior == PermissionBehavior.DENY


# ── 建议生成（AgentScope _generate_suggestions） ────────────────


class TestSuggestions:
    def test_shell_suggestion_prefix_wildcard(self):
        s = generate_suggestions("terminal", {"command": "npm run build"})
        assert s[0]["rule_content"] == "npm run:*"
        assert s[0]["behavior"] == "allow"

    def test_file_suggestion_directory_glob(self):
        s = generate_suggestions("write_file", {"path": "/a/b/c.py"})
        assert s[0]["rule_content"] == "/a/b/**"

    def test_other_tool_suggestion_tool_level(self):
        s = generate_suggestions("mcp_tool", {"x": 1})
        assert s[0]["rule_content"] == ""


# ── 持久化 + 审计（mem0审计表模式） ─────────────────────────────


class TestStore:
    def test_seed_idempotent(self, tmp_path):
        db = str(tmp_path / "p1.db")
        s1 = PermissionStore(db)
        n1 = len(s1.list_rules())
        s2 = PermissionStore(db)
        n2 = len(s2.list_rules())
        assert n1 == n2 and n1 > 0

    def test_rule_crud(self, tmp_path):
        store = PermissionStore(str(tmp_path / "p2.db"))
        r = store.add_rule("terminal", "foo:*", "allow", source="session")
        rules = store.list_rules(tool_name="terminal")
        assert any(x["rule_id"] == r["rule_id"] for x in rules)
        assert store.delete_rule(r["rule_id"]) is True
        assert not any(x["rule_id"] == r["rule_id"] for x in store.list_rules())
        # 软删：include_deleted仍可见（审计保留）
        assert any(x["rule_id"] == r["rule_id"] for x in store.list_rules(include_deleted=True))

    def test_mode_persistence(self, tmp_path):
        store = PermissionStore(str(tmp_path / "p3.db"))
        assert store.get_mode() == "accept_edits"
        store.set_mode("explore")
        assert PermissionStore(str(tmp_path / "p3.db")).get_mode() == "explore"

    def test_invalid_mode_rejected(self, tmp_path):
        store = PermissionStore(str(tmp_path / "p4.db"))
        with pytest.raises(ValueError):
            store.set_mode("yolo_mode")

    def test_decision_audit_logged(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "p5.db"))
        svc.check("terminal", {"command": "sudo apt update"}, session_id="s1")
        entries = svc.store.audit_query(tool_name="terminal")
        assert entries
        top = entries[0]
        assert top["behavior"] == "ask"
        assert top["rule_source"] == "builtin"
        assert top["requires_human"] == 1
        assert top["session_id"] == "s1"
        assert top["tool_input_preview"]

    def test_outcome_writeback(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "p6.db"))
        d = svc.check("terminal", {"command": "sudo apt update"}, session_id="s1")
        assert d.decision_id
        assert svc.store.record_outcome(d.decision_id, "approved", "ok this time") is True
        entries = svc.store.audit_query(outcome="approved")
        assert entries and entries[0]["decision_id"] == d.decision_id
        assert entries[0]["outcome_comment"] == "ok this time"

    def test_invalid_outcome_rejected(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "p7.db"))
        assert svc.store.record_outcome("nonexistent", "approved") is False
        d = svc.check("terminal", {"command": "sudo x"})
        assert svc.store.record_outcome(d.decision_id, "banana") is False

    def test_stats(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "p8.db"))
        svc.check("read_file", {"path": "ok.py"})
        svc.check("terminal", {"command": "sudo x"})
        st = svc.store.stats()
        assert st["decisions_total"] >= 2
        assert st["by_behavior"].get("ask", 0) >= 1
        assert st["active_rules"] > 0
        assert st["hard_rules"] >= 1
        assert st["mode"] == "accept_edits"


# ── Service端到端 ──────────────────────────────────────────────


class TestService:
    def test_check_end_to_end_deny_with_provenance(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "s1.db"))
        svc.store.set_mode("accept_edits")
        d = svc.check("terminal", {"command": "rm -rf /"}, session_id="sess-1")
        assert d.behavior == PermissionBehavior.DENY
        assert d.rule_source == "builtin"
        # 审计同步落库
        entries = svc.store.audit_query(behavior="deny")
        assert entries and entries[0]["session_id"] == "sess-1"

    def test_session_rule_added_then_allows(self, tmp_path):
        svc = PermissionService(db_path=str(tmp_path / "s2.db"))
        svc.store.set_mode("accept_edits")
        d1 = svc.check("terminal", {"command": "curl https://x.com"}, session_id="s")
        assert d1.behavior == PermissionBehavior.ASK
        # 人工批准后前端/API添加session层allow规则（kilocode approved层）
        svc.store.add_rule("terminal", "curl:*", "allow", source="session")
        d2 = svc.check("terminal", {"command": "curl https://x.com"}, session_id="s")
        assert d2.behavior == PermissionBehavior.ALLOW
        assert d2.rule_source == "session"


# ── HTTP API（经FastAPI TestClient） ────────────────────────────


class TestPermissionAPI:
    def test_check_endpoint_read_only(self, client):
        resp = client.post(
            "/api/immune/permission/check",
            json={
                "tool_name": "read_file",
                "arguments": {"path": "src/main.py"},
                "session_id": "t1",
                "working_dir": "/home/climbing",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["behavior"] == "allow"

    def test_check_endpoint_hard_deny(self, client):
        resp = client.post(
            "/api/immune/permission/check",
            json={
                "tool_name": "terminal",
                "arguments": {"command": "rm -rf /"},
                "session_id": "t2",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["behavior"] == "deny"
        assert data["rule_source"] == "builtin"

    def test_rules_list_includes_builtin(self, client):
        resp = client.get("/api/immune/permission/rules")
        assert resp.status_code == 200
        rules = resp.json()["rules"]
        assert any(r["hard"] for r in rules)
        assert any(r["behavior"] == "ask" for r in rules)

    def test_rule_add_and_delete(self, client):
        resp = client.post(
            "/api/immune/permission/rules",
            json={
                "tool_name": "terminal",
                "rule_content": "pytest:*",
                "behavior": "allow",
                "source": "session",
            },
        )
        assert resp.status_code == 200
        rule_id = resp.json()["rule_id"]
        resp2 = client.delete(f"/api/immune/permission/rules/{rule_id}")
        assert resp2.status_code == 200

    def test_rule_invalid_behavior(self, client):
        resp = client.post(
            "/api/immune/permission/rules",
            json={"tool_name": "terminal", "rule_content": "x", "behavior": "banana"},
        )
        assert resp.status_code == 400

    def test_mode_get_set_roundtrip(self, client):
        original = client.get("/api/immune/permission/mode").json()["mode"]
        resp = client.put("/api/immune/permission/mode", json={"mode": "dont_ask"})
        assert resp.status_code == 200
        assert client.get("/api/immune/permission/mode").json()["mode"] == "dont_ask"
        # DONT_ASK下敏感读取被转DENY
        resp2 = client.post(
            "/api/immune/permission/check",
            json={"tool_name": "read_file", "arguments": {"path": "/home/u/.ssh/id_rsa"}},
        )
        assert resp2.json()["behavior"] == "deny"
        # 恢复原模式
        client.put("/api/immune/permission/mode", json={"mode": original})

    def test_mode_invalid(self, client):
        resp = client.put("/api/immune/permission/mode", json={"mode": "nope"})
        assert resp.status_code == 400

    def test_audit_and_outcome_flow(self, client):
        # 触发一次ASK
        r = client.post(
            "/api/immune/permission/check",
            json={
                "tool_name": "terminal",
                "arguments": {"command": "sudo systemctl status"},
                "session_id": "api-test",
            },
        )
        assert r.json()["behavior"] == "ask"
        decision_id = r.json()["decision_id"]
        assert decision_id
        # 回写人工审批结果
        r2 = client.post(
            f"/api/immune/permission/approvals/{decision_id}",
            json={"outcome": "approved", "comment": "运维巡检"},
        )
        assert r2.status_code == 200
        # 审计查询可见
        r3 = client.get("/api/immune/permission/audit", params={"outcome": "approved"})
        assert r3.status_code == 200
        ids = [e["decision_id"] for e in r3.json()["entries"]]
        assert decision_id in ids
        # 无效outcome拒绝
        r4 = client.post(
            f"/api/immune/permission/approvals/{decision_id}", json={"outcome": "banana"}
        )
        assert r4.status_code in (400, 404)

    def test_stats_endpoint(self, client):
        resp = client.get("/api/immune/permission/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "decisions_total" in data and "active_rules" in data and "mode" in data

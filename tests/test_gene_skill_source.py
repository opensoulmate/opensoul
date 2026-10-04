"""gene技能来源provenance（learned_skills.source列）——"测试marker字符串识别"挂账解法。

核心契约：
- 写入时如实记来源（extract生产学习链 / seed种子落库 / test显式测试标记）；
  非法source→ValueError（API层400），不落"猜出来的来源"。
- 存量库schema迁移：source列ALTER + 空值回填unknown（不猜来源；unknown≠test，
  purge永不误删存量真实学习行），迁移幂等。
- 来源粘性（防purge误伤，fail-closed双向）：真实行被test触碰不降级成test；
  test行被真实提炼触碰升级成extract。
- purge_by_source只清test/seed（整批artifact清理+孤儿审计一并收拾）；
  extract硬拒绝——真实学习行走delete_skill留DELETE审计。
- source进STATE_FIELDS：回滚diff面/恢复面带来源（delete→restore无损保真）。

单元测试用tmp db直跑SkillLearner真实行为；API测试走进程内隔离面（conftest
gene_client）验证router接线（POST /skill/extract的source字段、POST /skill/purge-source）。
"""

import json
import sqlite3
import time

import pytest

from src.gene.skill_learner import (
    PURGEABLE_SOURCES,
    SKILL_SOURCES,
    SOURCE_UNKNOWN,
    SkillLearner,
)


def _tool_calls(*names, content="x" * 20, **arg_overrides):
    out = []
    for n in names:
        args = {"command": "echo hi", "content": content}
        args.update(arg_overrides)
        out.append({"function": {"name": n, "arguments": args}})
    return out


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestSourceBasics:
    def test_extract_defaults_to_extract_source(self, learner):
        """生产学习链缺省source=extract（写入时如实记，不靠事后猜）。"""
        s = learner.extract_from_execution(
            "sess-1", "来源测试任务A", _tool_calls("read_file", "write_file"), True
        )
        assert s is not None and s.source == "extract"
        st = learner.current_state(s.skill_id)
        assert st["source"] == "extract"

    def test_extract_explicit_test_source(self, learner):
        """测试/E2E脚本显式传test → 行来源=test（清理端可精确打击）。"""
        s = learner.extract_from_execution(
            "sess-t",
            "来源测试任务B",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        assert s.source == "test"
        assert learner.current_state(s.skill_id)["source"] == "test"

    def test_invalid_source_rejected(self, learner):
        """非法source→ValueError，不落"猜出来的来源"。"""
        with pytest.raises(ValueError, match="unknown skill source"):
            learner.extract_from_execution(
                "sess-1",
                "来源测试任务C",
                _tool_calls("read_file", "write_file"),
                True,
                source="wire解码测试-乱写",
            )

    def test_skill_sources_constant_is_single_truth(self):
        assert SKILL_SOURCES == ("extract", "seed", "test")
        assert "extract" not in PURGEABLE_SOURCES  # 真实学习行永不整批清

    def test_audit_snapshots_carry_source(self, learner):
        """ADD/UPDATE审计old/new快照带source（mem0 §1.2"被谁写入"全程可追溯）。"""
        s = learner.extract_from_execution(
            "sess-1", "来源审计任务", _tool_calls("read_file", "write_file"), True
        )
        events = learner.history(s.skill_id)
        add = next(e for e in events if e["event"] == "ADD")
        assert add["new"]["source"] == "extract"

        learner.extract_from_execution(
            "sess-2", "来源审计任务", _tool_calls("read_file", "write_file"), True
        )
        upd = next(e for e in learner.history(s.skill_id) if e["event"] == "UPDATE")
        assert upd["old"]["source"] == "extract"
        assert upd["new"]["source"] == "extract"


class TestSourceSticky:
    def test_real_row_not_downgraded_by_test_touch(self, learner):
        """真实行被test触碰 → 保留extract（真实行不许降级成可整批清的test）。"""
        s = learner.extract_from_execution(
            "sess-1", "粘性任务真实", _tool_calls("read_file", "write_file"), True
        )
        s2 = learner.extract_from_execution(
            "sess-t",
            "粘性任务真实",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        assert s.skill_id == s2.skill_id
        assert s2.source == "extract"  # 返回值也是生效值
        assert learner.current_state(s.skill_id)["source"] == "extract"

    def test_test_row_upgraded_by_real_extract(self, learner):
        """test行被真实提炼触碰 → 升级extract（技能族已被真实学习链确认，purge不收走）。"""
        s = learner.extract_from_execution(
            "sess-t",
            "粘性任务测试",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        s2 = learner.extract_from_execution(
            "sess-1", "粘性任务测试", _tool_calls("read_file", "write_file"), True
        )
        assert s.skill_id == s2.skill_id
        assert s2.source == "extract"
        assert learner.current_state(s.skill_id)["source"] == "extract"

    def test_test_row_updated_by_test_stays_test(self, learner):
        s = learner.extract_from_execution(
            "sess-t",
            "粘性任务双测试",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        s2 = learner.extract_from_execution(
            "sess-t2",
            "粘性任务双测试",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        assert s2.source == "test"
        assert learner.current_state(s.skill_id)["source"] == "test"


class TestMigration:
    def test_legacy_db_backfills_unknown(self, tmp_path):
        """存量库（source列上线前）迁移：ALTER加列+空值回填unknown（不猜来源）。"""
        db = tmp_path / "legacy.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE learned_skills (
                    skill_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT DEFAULT '', category TEXT DEFAULT 'general',
                    code_template TEXT DEFAULT '', parameters TEXT DEFAULT '[]',
                    usage_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0, created_at REAL NOT NULL,
                    last_used_at REAL DEFAULT 0, tags TEXT DEFAULT '[]',
                    source_session TEXT DEFAULT '')"""
            )
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, created_at)"
                " VALUES ('skill_old', '存量行', 123.0)"
            )
            conn.commit()

        learner = SkillLearner(db_path=str(db))
        assert learner.current_state("skill_old")["source"] == SOURCE_UNKNOWN
        # 迁移幂等：重复init不翻转、新提炼照常记extract
        learner2 = SkillLearner(db_path=str(db))
        assert learner2.current_state("skill_old")["source"] == SOURCE_UNKNOWN
        s = learner2.extract_from_execution(
            "sess-1", "迁移后新任务", _tool_calls("read_file", "write_file"), True
        )
        assert learner2.current_state(s.skill_id)["source"] == "extract"

    def test_unknown_source_exposed_not_guessed(self, tmp_path):
        """存量行在列表面如实显示unknown（unknown≠test）。"""
        db = tmp_path / "legacy.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE learned_skills (
                    skill_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT DEFAULT '', category TEXT DEFAULT 'general',
                    code_template TEXT DEFAULT '', parameters TEXT DEFAULT '[]',
                    usage_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0, created_at REAL NOT NULL,
                    last_used_at REAL DEFAULT 0, tags TEXT DEFAULT '[]',
                    source_session TEXT DEFAULT '')"""
            )
            conn.execute(
                "INSERT INTO learned_skills (skill_id, name, created_at)"
                " VALUES ('skill_old', '存量行', 123.0)"
            )
            conn.commit()
        learner = SkillLearner(db_path=str(db))
        rows = learner.list_skills()
        assert rows[0]["source"] == SOURCE_UNKNOWN
        assert learner.find_relevant("存量行")[0]["source"] == SOURCE_UNKNOWN


class TestRevertSource:
    def test_delete_restore_roundtrip_preserves_source(self, learner):
        """source进STATE_FIELDS：delete→restore整行无损（含来源）。"""
        s = learner.extract_from_execution(
            "sess-t",
            "回滚来源任务",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        sid = s.skill_id
        assert learner.delete_skill(sid, reason="先删") is True
        result = learner.revert_skill(sid)
        assert result["action"] == "restore"
        assert learner.current_state(sid)["source"] == "test"

    def test_revert_diff_shows_source(self, learner):
        """回滚预览diff面带source字段（STATE_FIELDS单一真源）。"""
        assert "source" in SkillLearner.STATE_FIELDS
        s = learner.extract_from_execution(
            "sess-t",
            "回滚diff任务",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        assert s is not None
        sid = s.skill_id
        learner.extract_from_execution(
            "sess-1", "回滚diff任务", _tool_calls("read_file", "write_file"), True
        )
        preview = learner.revert_preview(sid)
        assert "source" in preview["changes"]
        assert preview["changes"]["source"] == {"from": "extract", "to": "test"}

    def test_legacy_snapshot_without_source_keeps_current(self, learner):
        """旧审计快照（source列上线前形状）回滚：没记录的字段保留当前值（含source）。"""
        s = learner.extract_from_execution(
            "sess-1", "旧快照任务", _tool_calls("read_file", "write_file"), True
        )
        sid = s.skill_id
        legacy_old = {
            "name": "旧版任务",
            "description": "旧描述",
            "category": "general",
            "code_template": "legacy_call()",
            "parameters": [],
            "tags": [],
            "source_session": "sess-0",
            "usage_count": 1,
            "success_count": 1,
        }
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute(
                "INSERT INTO skill_audit (skill_id, event, old_json, new_json, actor, reason, created_at)"
                " VALUES (?, 'UPDATE', ?, '', 'legacy', 'legacy-shape', ?)",
                (sid, json.dumps(legacy_old, ensure_ascii=False), time.time()),
            )
            conn.commit()
        legacy_id = learner.history(sid)[0]["audit_id"]
        learner.revert_skill(sid, audit_id=legacy_id)
        assert learner.current_state(sid)["source"] == "extract"  # 不回退成unknown


class TestPurgeBySource:
    def test_purge_deletes_only_test_rows_and_their_audit(self, learner):
        """test行整批清+孤儿审计一并收拾；extract行纹丝不动（精确打击，不猜名字）。"""
        t1 = learner.extract_from_execution(
            "sess-t", "purge任务一", _tool_calls("read_file", "write_file"), True, source="test"
        )
        t2 = learner.extract_from_execution(
            "sess-t", "purge任务二", _tool_calls("read_file", "write_file"), True, source="test"
        )
        real = learner.extract_from_execution(
            "sess-1", "purge任务真实", _tool_calls("read_file", "write_file"), True
        )
        assert learner.history(t1.skill_id)  # test行有审计行

        result = learner.purge_by_source("test", reason="E2E清理")
        assert result == {"source": "test", "deleted_skills": 2, "deleted_audit": 2}
        assert learner.current_state(t1.skill_id) is None
        assert learner.current_state(t2.skill_id) is None
        assert learner.history(t1.skill_id) == []  # 孤儿审计同步清
        assert learner.current_state(real.skill_id) is not None
        assert learner.history(real.skill_id) != []  # 真实行审计留痕不动

    def test_purge_refuses_extract_and_unknown(self, learner):
        """extract（真实学习行）/unknown（存量行）硬拒绝——不许整批无痕蒸发。"""
        learner.extract_from_execution(
            "sess-1", "拒绝purge任务", _tool_calls("read_file", "write_file"), True
        )
        for bad in ("extract", SOURCE_UNKNOWN, "wire解码测试-乱写"):
            with pytest.raises(ValueError, match="purge refused"):
                learner.purge_by_source(bad)
        assert learner.count_skills() == 1

    def test_purge_empty_returns_zero(self, learner):
        assert learner.purge_by_source("test") == {
            "source": "test",
            "deleted_skills": 0,
            "deleted_audit": 0,
        }


class TestSeedSource:
    def test_seed_writes_seed_source(self, tmp_path, monkeypatch):
        """标准技能种子落库source=seed（种子≠执行提炼，可整批重播种）。"""
        import src.gene.seed_standard_skills as seed_mod

        db = tmp_path / "skills.db"
        SkillLearner(db_path=str(db))  # 建schema
        skills_dir = tmp_path / "agents-skills"
        skill_dir = skills_dir / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: 种子来源测试技能\n---\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(seed_mod, "SKILLS_DB", db)
        monkeypatch.setattr(seed_mod, "AGENT_SKILLS_DIR", skills_dir)

        seeded = seed_mod.seed_standard_skills()
        assert len(seeded) == 1
        learner = SkillLearner(db_path=str(db))
        rows = learner.list_skills()
        assert rows[0]["source"] == "seed"
        # seed可整批清（重播种语义）
        assert learner.purge_by_source("seed")["deleted_skills"] == 1


class TestListSurface:
    def test_list_and_relevant_expose_source(self, learner):
        s = learner.extract_from_execution(
            "sess-t",
            "列表面来源任务",
            _tool_calls("read_file", "write_file"),
            True,
            source="test",
        )
        row = next(r for r in learner.list_skills() if r["skill_id"] == s.skill_id)
        assert row["source"] == "test"
        rel = learner.find_relevant("列表面来源任务")
        assert rel and rel[0]["source"] == "test"


# ── API接线（conftest gene_client隔离面：真实router+tmp库） ──────────────


def _extract_payload(task, source=None, names=("read_file", "write_file")):
    body = {
        "task_description": task,
        "execution_log": json.dumps(_tool_calls(*names)),
        "success": True,
    }
    if source is not None:
        body["source"] = source
    return body


class TestApiSource:
    def test_extract_api_default_source(self, gene_client):
        resp = gene_client.post("/api/gene/skill/extract", json=_extract_payload("API默认来源任务"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "extract"
        listing = gene_client.get("/api/gene/skill").json()
        row = next(r for r in listing["skills"] if r["skill_id"] == data["skill_id"])
        assert row["source"] == "extract"

    def test_extract_api_test_source_visible_in_list(self, gene_client):
        resp = gene_client.post(
            "/api/gene/skill/extract",
            json=_extract_payload("API测试来源任务", source="test"),
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "test"
        listing = gene_client.get("/api/gene/skill").json()
        row = next(r for r in listing["skills"] if r["skill_id"] == resp.json()["skill_id"])
        assert row["source"] == "test"

    def test_extract_api_bad_source_400(self, gene_client):
        resp = gene_client.post(
            "/api/gene/skill/extract",
            json=_extract_payload("API非法来源任务", source="wire解码测试-乱写"),
        )
        assert resp.status_code == 400
        assert "bad source" in resp.json()["detail"]

    def test_purge_api_removes_test_rows(self, gene_client):
        r_test = gene_client.post(
            "/api/gene/skill/extract",
            json=_extract_payload("API purge测试行", source="test"),
        ).json()
        r_real = gene_client.post(
            "/api/gene/skill/extract", json=_extract_payload("API purge真实行")
        ).json()

        resp = gene_client.post(
            "/api/gene/skill/purge-source", json={"source": "test", "reason": "E2E清理"}
        )
        assert resp.status_code == 200
        assert resp.json()["deleted_skills"] == 1
        listing = gene_client.get("/api/gene/skill").json()
        ids = {r["skill_id"] for r in listing["skills"]}
        assert r_test["skill_id"] not in ids
        assert r_real["skill_id"] in ids

    def test_purge_api_refuses_extract_400(self, gene_client):
        resp = gene_client.post("/api/gene/skill/purge-source", json={"source": "extract"})
        assert resp.status_code == 400
        assert "purge refused" in resp.json()["detail"]
        resp = gene_client.post(
            "/api/gene/skill/purge-source", json={"source": "wire解码测试-乱写"}
        )
        assert resp.status_code == 400

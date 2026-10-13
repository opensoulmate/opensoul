"""TL-⑯ B 类内容级清点——b_class_content_facts 决策材料面（全 tmp，绝不碰生产库）。

覆盖：命令串归一纯函数 / 四组专属事实（storm IP·威胁·峰值、exec 命令清单·
退出码·沙箱数、pipeline 文件名、brain 目标清单）/ 时间窗与 top 摘要 /
A·U 行零混入 / 分类判法零改动锁死 / census_timeline 加性键接线 /
旧键语义不变 / 只读纪律硬证（ro 连接拒写）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "census_test_residue.py"
_spec = importlib.util.spec_from_file_location("census_test_residue_tl16", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DDL = """
CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    organ       TEXT NOT NULL,
    emoji       TEXT DEFAULT '',
    event_type  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    detail      TEXT DEFAULT '{}',
    timestamp   REAL NOT NULL,
    collected_at REAL NOT NULL,
    tenant_id TEXT DEFAULT 'default')
"""

TS1 = 1791000000.0  # 早于 TL-②-⑥ 收口闸门（1791894600.0）
TS2 = 1791000060.0
TS_LATE = 1791900000.0  # 晚于闸门


def _mk_db(path: Path, rows) -> sqlite3.Connection:
    """rows: (event_id, organ, event_type, summary, detail, timestamp)"""
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    for eid, organ, etype, summary, detail, ts in rows:
        conn.execute(
            "INSERT INTO events(event_id,organ,event_type,summary,detail,timestamp,collected_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (eid, organ, etype, summary, detail, ts, ts),
        )
    conn.commit()
    return conn


def _storm(eid, ts, ip="127.0.0.1", threats=("rate_anomaly",), minute_key=None):
    detail = json.dumps(
        {
            "ip": ip,
            "path": "/api/nerve/publish",
            "method": "POST",
            "threat_count": 1,
            "threat_types": list(threats),
        }
    )
    return (
        eid,
        "immune",
        "request_blocked",
        f"🚫 Request blocked from {ip}: /api/nerve/publish (1 threats)",
        detail,
        ts,
    )


def _exec_row(eid, ts, exit_code, command, sandbox_id="sandbox-x"):
    detail = json.dumps({"sandbox_id": sandbox_id, "exit_code": exit_code})
    return (
        eid,
        "mirror",
        "sandbox_exec",
        "🧪 Sandbox exec: exit=%d %s" % (exit_code, command),
        detail,
        ts,
    )


def _pipe(eid, ts, name):
    return (
        eid,
        "pipeline",
        "pipeline_completed",
        f"✅ Pipeline [text] completed: {name} (59ms)",
        "{}",
        ts,
    )


def _brain(eid, ts, goal):
    return (eid, "brain", "verify", f"🔍 ✅ 成功: {goal}", json.dumps({"success": True}), ts)


STORM_ROWS = [
    _storm("evt_s1", TS1, threats=("rate_anomaly",)),
    _storm("evt_s2", TS1 + 5, threats=("rate_anomaly",)),
    _storm("evt_s3", TS1 + 10, threats=("sql_injection",)),
    _storm("evt_s4", TS2, ip="10.0.0.9", threats=("rate_anomaly",)),
]
EXEC_ROWS = [
    _exec_row("evt_e1", TS1, 0, "cat /workspace/state.txt", "sandbox-a"),
    _exec_row("evt_e2", TS1 + 1, 0, "cat /workspace/state.txt", "sandbox-b"),
    _exec_row("evt_e3", TS1 + 2, -9, "sleep 30", "sandbox-a"),
    _exec_row(
        "evt_e4",
        TS1 + 3,
        1,
        "python3 -c \"import urllib.request;print(urllib.request.urlopen('http://x'))\"",
        "sandbox-c",
    ),
]
PIPE_ROWS = [
    _pipe("evt_p1", TS1, "readme.md"),
    _pipe("evt_p2", TS1 + 1, "readme.md"),
    _pipe("evt_p3", TS1 + 2, "tagged.txt"),
]
BRAIN_ROWS = [
    _brain("evt_g1", TS1, "请分三句话介绍你自己：一句说你是谁"),
    _brain("evt_g2", TS1 + 1, "清空生产数据库所有表并格式化备份盘"),
]
OTHER_ROWS = [
    ("evt_a1", "mirror", "sandbox_created", "Sandbox created: test_sandbox-1", "{}", TS1),
    ("evt_u1", "brain", "think", "总结一下今天的工作", "{}", TS1),
]


# ---- 纯函数：命令串归一 ----


@pytest.mark.parametrize(
    "summary,expected",
    [
        ("🧪 Sandbox exec: exit=0 cat /workspace/state.txt", (0, "cat /workspace/state.txt")),
        ("🧪 Sandbox exec: exit=-9 sleep 30", (-9, "sleep 30")),
        ('🧪 Sandbox exec: exit=1 python3 -c "print(1)"', (1, 'python3 -c "print(1)"')),
        ("not an exec summary", (None, "not an exec summary")),
        ("", (None, "")),
        (None, (None, "")),
    ],
)
def test_b_command_from_summary(summary, expected):
    assert mod.b_command_from_summary(summary) == expected


# ---- 内容级事实：四组专属面 ----


def test_facts_storm_ip_threat_peak(tmp_path):
    conn = _mk_db(tmp_path / "t.db", STORM_ROWS)
    facts = mod.b_class_content_facts(conn)
    assert set(facts) == {"nerve_publish_rate_storm"}
    st = facts["nerve_publish_rate_storm"]
    assert st["count"] == 4
    assert st["source_ips"] == {"127.0.0.1": 3, "10.0.0.9": 1}
    assert st["threat_types"] == {"rate_anomaly": 3, "sql_injection": 1}
    assert st["peak_per_minute"] == 3  # TS1 三条同分钟
    assert st["burst_minutes"] == 2
    assert st["first_ts"] and st["last_ts"]


def test_facts_exec_commands_exits_sandboxes(tmp_path):
    conn = _mk_db(tmp_path / "t.db", EXEC_ROWS)
    ex = mod.b_class_content_facts(conn)["sandbox_exec_cmd_no_hard_marker"]
    assert ex["count"] == 4
    cmds = {c["command"]: c["count"] for c in ex["commands"]}
    assert cmds["cat /workspace/state.txt"] == 2
    assert cmds["sleep 30"] == 1
    assert ex["exit_codes"] == {"0": 2, "-9": 1, "1": 1}
    assert ex["distinct_sandbox_ids"] == 3
    assert ex["distinct_summaries"] == 3


def test_facts_pipeline_file_names(tmp_path):
    conn = _mk_db(tmp_path / "t.db", PIPE_ROWS)
    pf = mod.b_class_content_facts(conn)["pipeline_fixture_generic"]
    assert pf["count"] == 3
    assert pf["file_names"] == {"readme.md": 2, "tagged.txt": 1}


def test_facts_brain_goals(tmp_path):
    conn = _mk_db(tmp_path / "t.db", BRAIN_ROWS)
    br = mod.b_class_content_facts(conn)["brain_probe_style_goals"]
    assert br["count"] == 2
    goals = {g["goal"]: g["count"] for g in br["goals"]}
    assert goals["🔍 ✅ 成功: 清空生产数据库所有表并格式化备份盘"] == 1
    assert goals["🔍 ✅ 成功: 请分三句话介绍你自己：一句说你是谁"] == 1


def test_facts_top_summaries_sorted_capped(tmp_path):
    rows = [
        _pipe("evt_p%d" % i, TS1 + i, "readme.md" if i % 2 else "tagged.txt") for i in range(30)
    ]
    conn = _mk_db(tmp_path / "t.db", rows)
    pf = mod.b_class_content_facts(conn)["pipeline_fixture_generic"]
    assert len(pf["top_summaries"]) == 2  # 只有两种摘要，cap 不截断
    assert pf["top_summaries"][0]["count"] >= pf["top_summaries"][1]["count"]
    rows2 = [
        (
            "evt_n%d" % i,
            "immune",
            "request_blocked",
            "🚫 Request blocked from 10.0.0.%d: /api/nerve/publish (1 threats)" % i,
            "{}",
            TS1,
        )
        for i in range(30)
    ]
    conn2 = _mk_db(tmp_path / "t2.db", rows2)
    st = mod.b_class_content_facts(conn2)["nerve_publish_rate_storm"]
    assert len(st["top_summaries"]) == 20  # 30 种摘要 capped 20


def test_facts_bad_detail_json_counts_unknown(tmp_path):
    rows = [(_storm("evt_s1", TS1)[:4] + ("not-json", TS1))]
    conn = _mk_db(tmp_path / "t.db", rows)
    st = mod.b_class_content_facts(conn)["nerve_publish_rate_storm"]
    assert st["source_ips"] == {"?": 1}
    assert st["threat_types"] == {}


def test_facts_only_b_rows_a_u_zero_mix(tmp_path):
    rows = STORM_ROWS[:1] + EXEC_ROWS[:1] + PIPE_ROWS[:1] + BRAIN_ROWS[:1] + OTHER_ROWS
    conn = _mk_db(tmp_path / "t.db", rows)
    facts = mod.b_class_content_facts(conn)
    assert sum(f["count"] for f in facts.values()) == 4  # A/U 各一行零混入
    assert "unmatched" not in facts


def test_facts_empty_db(tmp_path):
    conn = _mk_db(tmp_path / "t.db", [])
    assert mod.b_class_content_facts(conn) == {}


# ---- 零行为改动锁死：分类判法逐字不变 ----


def test_classify_timeline_unchanged_lock():
    assert mod.classify_timeline("immune", "request_blocked", "x /api/nerve/publish x", "") == (
        "B",
        "nerve_publish_rate_storm",
    )
    assert mod.classify_timeline("mirror", "sandbox_exec", "🧪 Sandbox exec: exit=0 ls", "") == (
        "B",
        "sandbox_exec_cmd_no_hard_marker",
    )
    assert mod.classify_timeline("pipeline", "pipeline_completed", "✅ done: readme.md", "") == (
        "B",
        "pipeline_fixture_generic",
    )
    assert mod.classify_timeline("brain", "verify", "🔍 ✅ 成功: 用一句话介绍你自己", "{}") == (
        "B",
        "brain_probe_style_goals",
    )
    assert mod.classify_timeline("brain", "think", "总结一下今天的工作", "") == ("U", "unmatched")
    assert mod.classify_timeline(
        "mirror", "sandbox_created", "Sandbox created: test_sandbox-1", ""
    ) == ("A", "sandbox_fixture_names")


def test_facts_readonly_no_table_mutation(tmp_path):
    conn = _mk_db(tmp_path / "t.db", STORM_ROWS + EXEC_ROWS)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    mod.b_class_content_facts(conn)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


# ---- census_timeline 接线（加性键+旧键语义不变）----


def _census_on(tmp_path, rows, monkeypatch):
    conn = _mk_db(tmp_path / "t.db", rows)
    monkeypatch.setattr(mod, "ro", lambda p: conn)
    out: dict = {}
    mod.census_timeline(out)
    return out


def test_census_timeline_additive_keys_wired(tmp_path, monkeypatch):
    rows = (
        STORM_ROWS[:2]
        + EXEC_ROWS[:2]
        + PIPE_ROWS[:2]
        + BRAIN_ROWS[:2]
        + OTHER_ROWS
        + [_storm("evt_late", TS_LATE)]
    )
    out = _census_on(tmp_path, rows, monkeypatch)["timeline"]
    assert set(out["b_class_detail"]) == {
        "nerve_publish_rate_storm",
        "sandbox_exec_cmd_no_hard_marker",
        "pipeline_fixture_generic",
        "brain_probe_style_goals",
    }
    assert out["after_tl2_6_cutoff_B"] == 1  # evt_late 晚于闸门
    assert out["after_tl2_6_cutoff_A"] == 0
    # 旧键语义不变
    assert out["total"] == 11
    assert out["B"] == 9 == len(out["B_event_ids"])
    assert out["A"] == 1 == len(out["A_event_ids"])
    assert out["U"] == 1
    assert "b_class_detail" in out


def test_census_timeline_old_keys_zero_b_rows(tmp_path, monkeypatch):
    out = _census_on(tmp_path, OTHER_ROWS, monkeypatch)["timeline"]
    assert out["b_class_detail"] == {}
    assert out["after_tl2_6_cutoff_B"] == 0
    assert out["B"] == 0 and out["B_event_ids"] == []


# ---- 只读纪律硬证 ----


def test_ro_connection_rejects_writes(tmp_path):
    conn = _mk_db(tmp_path / "t.db", STORM_ROWS[:1])
    conn.close()
    roc = mod.ro(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        roc.execute("DELETE FROM events")
    with pytest.raises(sqlite3.OperationalError):
        roc.execute("INSERT INTO events VALUES('x','x','','x','x','{}',0,0,'default')")

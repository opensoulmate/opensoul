"""GATE-③ 探针漏清事件行清理工具契约测试（全 tmp 绝不碰生产库）。

覆盖：差集清单派生（禁发明新判法）、dry-run 零写入、精确删（基线行一行不动）、
删前复核拒删（现判非 A=工件陈旧一行不动）、缺行非致命、备份快照、回读对账。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "cleanup_gate3_probe_event_leak.py"

SCHEMA = """CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    organ       TEXT NOT NULL,
    emoji       TEXT DEFAULT '',
    event_type  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    detail      TEXT DEFAULT '{}',
    timestamp   REAL NOT NULL,
    collected_at REAL NOT NULL
, tenant_id TEXT DEFAULT 'default')"""


def _mk_db(tmp_path, ids) -> Path:
    db = tmp_path / "timeline.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(SCHEMA)
        for eid in ids:
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,'default')",
                (
                    eid,
                    "immune",
                    "",
                    "ip_blacklisted",
                    f"🚫 IP blacklisted: 192.0.2.77 — e2e-probe-{eid[-4:]}",
                    json.dumps({"reason": f"e2e-probe-{eid[-4:]}"}),
                    1.0,
                    1.0,
                ),
            )
    conn.close()
    return db


def _mk_artifact(tmp_path, name, ids) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"timeline": {"A_event_ids": list(ids)}}), encoding="utf-8")
    return p


def _run(tmp_path, db, pre, post, apply=False, report="report.json", backup="backups"):
    cmd = [
        sys.executable,
        str(TOOL),
        "--db",
        str(db),
        "--pre",
        str(pre),
        "--post",
        str(post),
        "--backup-dir",
        str(tmp_path / backup),
        "--report",
        str(tmp_path / report),
    ]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def _a_ids(db: Path) -> list[str]:
    if not db.exists():
        return []
    sys.path.insert(0, str(ROOT / "scripts"))
    from census_test_residue import classify_timeline

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = []
        for row in conn.execute("SELECT organ, event_type, summary, detail FROM events"):
            verdict, _ = classify_timeline(row[0], row[1], row[2], row[3] or "")
            if verdict == "A":
                out.append(1)
        return out
    finally:
        conn.close()


def test_dry_run_零写入(tmp_path):
    db = _mk_db(tmp_path, ["evt_base", "evt_new"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base", "evt_new"])
    r = _run(tmp_path, db, pre, post)
    assert r.returncode == 0, r.stderr
    assert "[dry-run]" in r.stdout
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    finally:
        conn.close()


def test_apply_精确删差集_基线行不动(tmp_path):
    db = _mk_db(tmp_path, ["evt_base", "evt_new"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base", "evt_new"])
    r = _run(tmp_path, db, pre, post, apply=True)
    assert r.returncode == 0, r.stderr
    conn = sqlite3.connect(db)
    try:
        rows = [x[0] for x in conn.execute("SELECT event_id FROM events ORDER BY event_id")]
    finally:
        conn.close()
    assert rows == ["evt_base"]  # 基线行一行不动
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["applied"] is True
    assert rep["post_total"] == 1
    assert rep["remain_a_count"] == 1
    assert rep["remain_a_not_in_pre"] == []
    assert rep["pre_rows_missing_after"] == []


def test_差集为空_零动作(tmp_path):
    db = _mk_db(tmp_path, ["evt_base"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base"])
    r = _run(tmp_path, db, pre, post, apply=True)
    assert r.returncode == 0, r.stderr
    assert "差集为空" in r.stdout
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        conn.close()


def test_现判非A拒删一行不动(tmp_path):
    # evt_new 行内容不含 e2e 硬标识（真实用户事件）→ classify_timeline 判 U → 拒删
    db = _mk_db(tmp_path, ["evt_base"])
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,'default')",
            (
                "evt_new",
                "immune",
                "",
                "ip_blacklisted",
                "🚫 IP blacklisted: 10.0.0.5 — manual block",
                "{}",
                1.0,
                1.0,
            ),
        )
    conn.close()
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base", "evt_new"])
    r = _run(tmp_path, db, pre, post, apply=True)
    assert r.returncode != 0
    assert "拒删" in (r.stderr + r.stdout)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    finally:
        conn.close()


def test_缺行非致命如实报(tmp_path):
    db = _mk_db(tmp_path, ["evt_base"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base", "evt_gone"])
    r = _run(tmp_path, db, pre, post, apply=True)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["missing"] == ["evt_gone"]
    assert rep["matched"] == []


def test_备份快照含删前全行(tmp_path):
    db = _mk_db(tmp_path, ["evt_base", "evt_new"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    post = _mk_artifact(tmp_path, "post.json", ["evt_base", "evt_new"])
    r = _run(tmp_path, db, pre, post, apply=True)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    backup = Path(rep["backup"]["path"])
    assert backup.exists() and rep["backup"]["sha256"]
    conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert n == 2  # 删前全量快照可回滚


def test_工件A_ids形态非法拒动(tmp_path):
    db = _mk_db(tmp_path, ["evt_base"])
    pre = _mk_artifact(tmp_path, "pre.json", ["evt_base"])
    bad = tmp_path / "post.json"
    bad.write_text(json.dumps({"timeline": {"A_event_ids": {"not": "a list"}}}), encoding="utf-8")
    r = _run(tmp_path, db, pre, bad, apply=True)
    assert r.returncode != 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        conn.close()


def test_判法单一源_工具真消费classify_timeline(tmp_path):
    src = TOOL.read_text(encoding="utf-8")
    assert "from census_test_residue import classify_timeline" in src
    assert "classify_timeline(" in src
    # 禁手抄第二套判法（工具内不得出现 e2e 字面判据）
    body = src.split('"""', 2)[2]
    assert '"e2e' not in body and "'e2e" not in body

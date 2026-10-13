#!/usr/bin/env python3
"""历史测试残留清点（TL-③）——只读审计工具。

对生产数据面做测试残留清点：按显式分类规则把每一行/每一项归入
  A=确证测试残留（对象级硬标识：test_sandbox / test.txt / stub-agent / e2e-* 等）
  B=疑似测试残留（形态像测试但无硬标识，需人工裁决）
  U=未归类（默认按真实事件对待，不动）
零删除零写入：所有连接 file:...?mode=ro，产出仅为计数/清单 JSON + 文本报告。
删除动作需用户拍板（本工具只提供决策材料）。

路径取自真源（禁手抄漂移）：
  data/timeline.db            -> src/timeline/store.py default_db_path
  data/opensoul.db            -> src/db/pool（SQLitePool 缺省）
  data/nest.db                -> src/nest/store.py NestStore
  data/enterprise.db          -> src/services/tenant_binding.py 双库自愈面
  data/ai_groups.db           -> src/api/ai_groups.py
  ~/.hermes/opensoul/immune/permission.db -> src/immune/permission_engine.py:1121
  ~/.opensoul/backups|exports|sandboxes   -> src/marrow/backup.py:52 / migrator.py:30 / src/mirror/sandbox.py:45

用法：
  python3 scripts/census_test_residue.py [--json data/test_residue_census.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()

# ---- timeline.db 事件级分类规则（单一源=下方 classify_timeline 纯函数；先匹配先得，未命中=U）----
# A=确证测试残留（对象级硬标识：test_sandbox / test.txt / stub-agent / e2e 等）
# B=疑似测试残留（形态像测试但无硬标识，需人工裁决）


def classify_timeline(organ: str, etype: str, summary: str, detail: str) -> tuple[str, str]:
    """timeline 事件分类（纯函数单一源，禁在别处手抄判法）。"""
    s, d = summary or "", detail or ""
    if ("Sandbox created: test_sandbox" in s or "Sandbox created: var_test" in s
            or "Sandbox created: api-" in s or "Sandbox created: live-" in s
            or "Sandbox forked from snapshot: api-fork-child" in s
            or "Sandbox forked from snapshot: live-fork" in s
            or "echo api-roundtrip" in s or "echo live-exec-ok" in s
            or "echo forked-state" in s or "echo fork-state" in s or "echo mutated" in s):
        return "A", "sandbox_fixture_names"
    if (organ in ("vein", "pipeline") and (
            "test.txt" in s or "dup1.txt" in s or "dup2.txt" in s
            or "test-promote.txt" in s or "timeline-test.txt" in s
            or "test.png" in s)):
        return "A", "file_fixture_names"
    if organ == "mirror" and ("🧪 Sandbox exec" in s or "🍴 Sandbox forked" in s):
        return "B", "sandbox_exec_cmd_no_hard_marker"
    if ("Test Node" in s or "HB Test" in s or "Push Test" in s
            or "test-push-node" in s or "test-delete-me" in s):
        return "A", "soma_fixture_components"
    if "test_schedule" in s or "test_backup_integration" in s or "test-sense-backup" in s:
        return "A", "marrow_fixture"
    if "Test Message" in s or "test_connector" in s:
        return "A", "echo_link_fixture"
    if ("192.168.99.99" in s or "test_dangerous_tool" in s
            or "/api/llm/test" in s or "from testclient" in s):
        return "A", "immune_fixture"
    if "Test event from timeline API" in s or "e2e" in s or "e2e" in d or "sentinel" in d:
        return "A", "timeline_api_probe"
    if organ == "brain" and etype == "verify" and "stub-agent" in d:
        return "A", "brain_stub_agent"
    if organ == "brain" and ("A1认知层接线live验证" in s or "逐块推送验证目标" in s
                             or "口算17加25" in s or "echo桩" in d):
        return "A", "brain_probe_goals"
    if etype == "request_blocked" and "/api/nerve/publish" in s:
        return "B", "nerve_publish_rate_storm"
    if organ == "pipeline" and ("readme.md" in s or "tagged.txt" in s):
        return "B", "pipeline_fixture_generic"
    if organ == "brain" and ("用一句话" in s or "请分三句话" in s
                             or "列出3个Linux" in s or "清空生产数据库" in s):
        return "B", "brain_probe_style_goals"
    return "U", "unmatched"

# 非 timeline 面的硬标识（A 类）
A_USERNAMES = {"test", "admin2", "autobot", "uiprobe"}
A_SESSIONS = {"t1", "t2", "api-test", "e2e", "py-exfil-probe"}
A_RULE_TOOLS = {"test_dangerous_tool"}
A_RULE_SOURCES = {"p09-probe"}
A_KNOWLEDGE_TITLES_PREFIX = ("[Pipeline] test", "[Pipeline] dup", "[Pipeline] timeline-test")
B_KNOWLEDGE_TITLES_PREFIX = ("[Pipeline] readme.md", "[Pipeline] tagged.txt")
A_TENANT_PREFIX = "e2e-"
A_GOAL_MARKERS = ("冒烟测试", "A1认知层接线live验证", "逐块推送验证目标", "清空生产数据库", "口算17加25")
B_GOAL_MARKERS = ("用一句话", "请分三句话", "列出3个Linux", "介绍一下Python", "介绍Python的GIL")
A_MANIFEST_NAME_MARKERS = ("test_backup_integration", "test-sense-backup")


def classify_rule(tool_name: str, source: str):
    """perm_rules 残留分类（单一源词表，禁手抄；TL-⑤ 失效化工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（对象级硬标识）、other=不动。
    """
    if tool_name in A_RULE_TOOLS:
        return "A", "A_RULE_TOOLS"
    if source in A_RULE_SOURCES:
        return "A", "A_RULE_SOURCES"
    return "other", "unmatched"


def classify_knowledge(title: str):
    """knowledge 行残留分类（单一源词表，禁手抄；TL-⑥ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（fixture 标题前缀）、B=疑似（需人工裁决）、
    other=不动。
    """
    t = title or ""
    if t.startswith(A_KNOWLEDGE_TITLES_PREFIX):
        return "A", "A_KNOWLEDGE_TITLES_PREFIX"
    if t.startswith(B_KNOWLEDGE_TITLES_PREFIX):
        return "B", "B_KNOWLEDGE_TITLES_PREFIX"
    return "other", "unmatched"


def classify_tenant(name):
    """nest.db tenants 行残留分类（单一源词表，禁手抄；TL-⑦ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（e2e 探针租户名前缀）、other=不动。
    """
    if (name or "").startswith(A_TENANT_PREFIX):
        return "A", "A_TENANT_PREFIX"
    return "other", "unmatched"


def ro(path: Path) -> sqlite3.Connection:
    assert path.exists(), f"missing: {path}"
    return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)


def census_timeline(out: dict) -> None:
    c = ro(REPO / "data" / "timeline.db")
    total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    counts = {"A": Counter(), "B": Counter(), "U": Counter()}
    assigned: dict[str, set[str]] = {"A": set(), "B": set(), "U": set()}
    late_a = 0
    cutoff = 1791894600.0  # 2026-10-13 20:30 CST = TL-②-⑥ 收口时刻
    for eid, organ, etype, summary, detail, ts in c.execute(
            "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events"):
        cls, rule = classify_timeline(organ, etype, summary, detail)
        counts[cls][f"{organ}/{etype}:{rule}"] += 1
        if cls in ("A", "B"):
            assigned[cls].add(eid)
        if cls == "A" and ts > cutoff:
            late_a += 1
    out["timeline"] = {
        "total": total,
        "A": sum(counts["A"].values()),
        "B": sum(counts["B"].values()),
        "U": sum(counts["U"].values()),
        "A_by_rule": dict(counts["A"].most_common()),
        "B_by_rule": dict(counts["B"].most_common()),
        "U_by_face": dict(counts["U"].most_common(40)),
        "A_event_ids": sorted(assigned["A"]),
        "B_event_ids": sorted(assigned["B"]),
    }
    # 残留增长闸门：TL-②-⑥ 收口（2026-10-13 20:30 CST）之后新增的 A 类行=隔离仍在漏
    out["timeline"]["after_tl2_6_cutoff_A"] = late_a


def census_sqlite(out: dict) -> None:
    res: dict = {}
    # opensoul.db：users / knowledge / metacognition_log
    c = ro(REPO / "data" / "opensoul.db")
    users = c.execute("SELECT id,username,email FROM users").fetchall()
    res["opensoul_users"] = {
        "total": len(users),
        "A": [{"id": u[0], "username": u[1]} for u in users if u[1] in A_USERNAMES or "test" in (u[2] or "")],
    }
    kn = c.execute("SELECT id,title FROM knowledge").fetchall()
    a_kn = [k for k in kn if classify_knowledge(k[1])[0] == "A"]
    b_kn = [k for k in kn if classify_knowledge(k[1])[0] == "B"]
    res["opensoul_knowledge"] = {
        "total": len(kn), "A": len(a_kn), "B": len(b_kn),
        "A_titles": dict(Counter(k[1] for k in a_kn)),
        "B_titles": dict(Counter(k[1] for k in b_kn)),
    }
    meta = c.execute("SELECT COUNT(*) FROM metacognition_log").fetchone()[0]
    res["opensoul_metacognition_log"] = {"total": meta, "note": "单行 goal=fix_bug 疑似探针（B）"}
    # nest.db
    c = ro(REPO / "data" / "nest.db")
    tenants = c.execute("SELECT tenant_id,name FROM tenants").fetchall()
    res["nest_tenants"] = {
        "total": len(tenants),
        "A": [{"tenant_id": t[0], "name": t[1]} for t in tenants if classify_tenant(t[1])[0] == "A"],
    }
    # enterprise.db
    c = ro(REPO / "data" / "enterprise.db")
    eusers = c.execute("SELECT id,username FROM users").fetchall()
    res["enterprise_users"] = {
        "total": len(eusers),
        "A": [{"id": u[0], "username": u[1]} for u in eusers if u[1] in A_USERNAMES],
    }
    # ai_groups.db：goal 级分类（父任务归类，子任务随父）
    c = ro(REPO / "data" / "ai_groups.db")
    tasks = c.execute("SELECT id,goal FROM ai_group_tasks").fetchall()
    a_t = [t for t in tasks if any(m in (t[1] or "") for m in A_GOAL_MARKERS)]
    b_t = [t for t in tasks if t not in a_t and any(m in (t[1] or "") for m in B_GOAL_MARKERS)]
    groups = c.execute("SELECT id,name FROM ai_groups").fetchall()
    res["ai_groups"] = {
        "groups_total": len(groups),
        "groups": [{"group_id": g[0], "name": g[1]} for g in groups],
        "tasks_total": len(tasks),
        "tasks_A": len(a_t), "tasks_B": len(b_t),
        "tasks_U": len(tasks) - len(a_t) - len(b_t),
    }
    # job_queue.db
    c = ro(REPO / "data" / "job_queue.db")
    jobs = c.execute("SELECT id,name,idempotency_key FROM jobs").fetchall()
    b_jobs = [j for j in jobs if "test" in (j[2] or "").lower()]
    res["jobs"] = {"total": len(jobs), "B_idempotency_test": [{"id": j[0], "key": j[2]} for j in b_jobs]}
    # will_checkpoints / agent_collaboration 存量
    for name, db in (("will_checkpoints", "data/will_checkpoints.db"),
                     ("agent_collaboration", "data/agent_collaboration.db")):
        p = REPO / db
        if p.exists():
            cc = ro(p)
            tables = [r[0] for r in cc.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            res[name] = {t: cc.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    out["sqlite"] = res


def census_permission(out: dict) -> None:
    p = HOME / ".hermes" / "opensoul" / "immune" / "permission.db"
    c = ro(p)
    sess = c.execute("SELECT session_id,COUNT(*) FROM perm_decision_audit GROUP BY 1").fetchall()
    a = {s: n for s, n in sess if (s or "") in A_SESSIONS or (s or "").startswith(("p0", "py-"))}
    u = {s: n for s, n in sess if s not in a and not (s or "").startswith("om-")}
    real = {s: n for s, n in sess if (s or "").startswith("om-")}
    rules = c.execute("SELECT rule_id,tool_name,source FROM perm_rules WHERE is_deleted=0").fetchall()
    a_rules = [r for r in rules if classify_rule(r[1], r[2])[0] == "A"]
    out["permission"] = {
        "decision_audit_total": sum(n for _, n in sess),
        "A_by_session": a, "A_total": sum(a.values()),
        "U_by_session": u, "U_total": sum(u.values()),
        "real_om_sessions_total": sum(real.values()),
        "rules_total_active": len(rules),
        "rules_A": [{"rule_id": r[0], "tool": r[1], "source": r[2]} for r in a_rules],
        "rules_by_source": dict(Counter(r[2] for r in rules)),
    }


def census_files(out: dict) -> None:
    res: dict = {}
    bdir = HOME / ".opensoul" / "backups"
    manifests = json.loads((bdir / "manifests.json").read_text())
    items = manifests if isinstance(manifests, list) else list(manifests.values())
    a_items = [it for it in items
               if isinstance(it, dict) and any(m in str(it.get("name", "")) for m in A_MANIFEST_NAME_MARKERS)]
    tars = sorted(x.name for x in bdir.glob("*.tar.gz"))
    res["backups"] = {
        "manifest_entries": len(items),
        "A_dangling_test_entries": [{"backup_id": it.get("backup_id"), "name": it.get("name")} for it in a_items],
        "tar_files_on_disk": len(tars),
        "tar_name_range": [tars[0], tars[-1]] if tars else [],
    }
    edir = HOME / ".opensoul" / "exports"
    exports = sorted(x.name for x in edir.glob("*.json")) if edir.is_dir() else []
    res["exports"] = {
        "total": len(exports),
        "B_uniform_export_export": sum(1 for e in exports if e.startswith("export_export_")),
        "sample": exports[:3],
    }
    sdir = HOME / ".opensoul" / "sandboxes"
    sandboxes = sorted(x.name for x in sdir.iterdir()) if sdir.is_dir() else []
    res["sandboxes"] = {"total": len(sandboxes), "U_names": sandboxes, "note": "随机 id 目录，无法从名字判测试/真实"}
    out["files"] = res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="把清点结果落成 JSON 工件")
    args = ap.parse_args()
    out: dict = {"tool": "census_test_residue", "read_only": True}
    census_timeline(out)
    census_sqlite(out)
    census_permission(out)
    census_files(out)
    text = json.dumps(out, ensure_ascii=False, indent=1, default=str)
    print(text)
    if args.json:
        Path(args.json).write_text(text)
        print(f"[written] {args.json}")


if __name__ == "__main__":
    main()

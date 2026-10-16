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
  data/proactive.db            -> src/will/proactive.py:45 _default_events_db（PROACTIVE_DB env 优先）

用法：
  python3 scripts/census_test_residue.py [--json data/test_residue_census.json]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()

# ---- timeline.db 事件级分类规则（单一源=下方 classify_timeline 纯函数；先匹配先得，未命中=U）----
# A=确证测试残留（对象级硬标识：test_sandbox / test.txt / stub-agent / e2e 等）
# B=疑似测试残留（形态像测试但无硬标识，需人工裁决）

# B 类分组 rule 名单一源（classify_timeline 返回值即此四处常量，禁字面手抄漂移；
# TL-⑰ 分组处置工具按此消费——storm 保留、exec/pipeline/brain 三组按 TL-⑯ 建议删）
B_RULE_STORM = "nerve_publish_rate_storm"
B_RULE_EXEC = "sandbox_exec_cmd_no_hard_marker"
B_RULE_PIPELINE = "pipeline_fixture_generic"
B_RULE_BRAIN = "brain_probe_style_goals"
B_RULES_ALL = (B_RULE_STORM, B_RULE_EXEC, B_RULE_PIPELINE, B_RULE_BRAIN)


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
        return "B", B_RULE_EXEC
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
        return "B", B_RULE_STORM
    if organ == "pipeline" and ("readme.md" in s or "tagged.txt" in s):
        return "B", B_RULE_PIPELINE
    if organ == "brain" and ("用一句话" in s or "请分三句话" in s
                             or "列出3个Linux" in s or "清空生产数据库" in s):
        return "B", B_RULE_BRAIN
    return "U", "unmatched"

# 非 timeline 面的硬标识（A 类）
A_USERNAMES = {"test", "admin2", "autobot", "uiprobe"}
A_SESSIONS = {"t1", "t2", "api-test", "e2e", "py-exfil-probe"}
A_RULE_TOOLS = {"test_dangerous_tool"}
A_RULE_SOURCES = {"p09-probe"}
A_KNOWLEDGE_TITLES_PREFIX = ("[Pipeline] test", "[Pipeline] dup", "[Pipeline] timeline-test")
B_KNOWLEDGE_TITLES_PREFIX = ("[Pipeline] readme.md", "[Pipeline] tagged.txt")
# TL-⑱ knowledge 夹具内容单一源：文件名→上传夹具逐字节内容（出处 tests/test_pipeline.py
# :93 data.json / :104 notes.md 上传夹具；标题+内容双逐字钉=对象级硬标识）。
# 仅内容逐字节相同才判 A（宁窄勿滥：同名真实用户上传不动）。
A_KNOWLEDGE_FIXTURE_CONTENT = {
    "data.json": '{"key": "value", "nested": {"a": 1}}',
    "notes.md": "# Title\n\nSome content here with enough text for processing.",
}
# TL-⑳ pipe_test.txt 取舍拍板（2026-10-14）：文件名→自述测试探针逐字节内容。证据链
# （TL-⑭ 出处比对同法）：①内容自述「test file for knowledge import validation」
# ②上传时刻 2026-09-19 08:11:33/08:13:22 与 commit bed38f84「feat(search):
# Meilisearch知识库索引管道全链接线」08:10:59 秒级吻合=该功能的导入验证探针
# ③repo 全历史 git log -S 零出处=一次性 e2e 探针非仓内夹具。仅内容逐字节相同才判 A
# （宁窄勿滥：同名真实用户上传内容不同不动）。出处不可钉于仓内测试文件，故独立于
# A_KNOWLEDGE_FIXTURE_CONTENT（那是 tests/test_pipeline.py 夹具）。
A_KNOWLEDGE_SELF_DECLARED_CONTENT = {
    "pipe_test.txt": "Hello pipeline test file for knowledge import validation 20260919.",
}
# TL-㉑ 「反馈层测试」4/「test」1 取舍拍板（2026-10-14）：(标题, 内容) 双逐字钉
# 探针对。证据链（TL-⑳ 三重旁证同法）：①自述——标题「反馈层测试」自述测试+行
# 内容逐字「这是自动提取的知识条目，用于验证反馈层写入机制」自述验证用途；
# 「test」行 title/source/content 全字面测试标识（'test'/'test'/'test content'，
# 与 A_USERNAMES{'test'}、A_EXPORT_ENTRY_NAMES 同族字面）②时刻——2026-08-30
# 02:21:33~02:22:26 60 秒簇 5 行（3 种 metadata 形状×2 user_id=写入路径验证行为
# 非知识生产），落在反馈层功能（src/services/feedback.py 首提交 0c49266e
# 2026-08-31 00:56:58）前 22.6h 的开发空窗（cf2fff9d 2026-08-28 08:21 后全仓零
# 提交）正中=反馈层开发期写入验证探针（时距如实记，非 TL-⑳ 的秒级吻合）
# ③repo 全历史 git log -S「这是自动提取的知识条目」「自动提取的知识条目」零出处；
# 「test content」仅作 tests/test_vein.py 更长夹具子串存在（逐字节不同）=非仓内
# 夹具。仅 (标题,内容) 双逐字节相同才判 A（宁窄勿滥：同题真实内容/同内容异题不动）。
A_KNOWLEDGE_PROBE_TITLE_CONTENT = {
    ("反馈层测试", "这是自动提取的知识条目"),
    ("反馈层测试", "这是自动提取的知识条目，用于验证反馈层写入机制"),
    ("反馈层测试", "自动提取的知识条目"),
    ("test", "test content"),
}
A_TENANT_PREFIX = "e2e-"
A_GOAL_MARKERS = ("冒烟测试", "A1认知层接线live验证", "逐块推送验证目标", "清空生产数据库", "口算17加25")
B_GOAL_MARKERS = ("用一句话", "请分三句话", "列出3个Linux", "介绍一下Python", "介绍Python的GIL")
A_MANIFEST_NAME_MARKERS = ("test_backup_integration", "test-sense-backup")
A_GROUP_NAMES = {"A群组", "B群组"}
A_EXPORT_NAME_PREFIX = "test-"
A_EXPORT_ENTRY_NAMES = {"test", "test2"}
A_SANDBOX_NAME_MARKERS = ("test", "e2e", "sentinel", "probe", "fixture")


def classify_rule(tool_name: str, source: str):
    """perm_rules 残留分类（单一源词表，禁手抄；TL-⑤ 失效化工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（对象级硬标识）、other=不动。
    """
    if tool_name in A_RULE_TOOLS:
        return "A", "A_RULE_TOOLS"
    if source in A_RULE_SOURCES:
        return "A", "A_RULE_SOURCES"
    return "other", "unmatched"


def classify_knowledge(title: str, content: str | None = None):
    """knowledge 行残留分类（单一源词表，禁手抄；TL-⑥ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（fixture 标题前缀，或标题+内容双逐字钉
    A_KNOWLEDGE_FIXTURE_CONTENT——TL-⑱，或自述测试探针内容逐字钉
    A_KNOWLEDGE_SELF_DECLARED_CONTENT——TL-⑳，或 (标题,内容) 双逐字钉探针对
    A_KNOWLEDGE_PROBE_TITLE_CONTENT——TL-㉑）、B=疑似（需人工裁决）、other=不动。
    内容判据宁窄勿滥：仅内容与夹具/探针逐字节相同才判 A（同名真实用户上传不动）；
    content 未提供（旧调用面）不猜——标题词表语义逐字不变（契约锁死面）。
    """
    t = title or ""
    if t.startswith(A_KNOWLEDGE_TITLES_PREFIX):
        return "A", "A_KNOWLEDGE_TITLES_PREFIX"
    if t.startswith(B_KNOWLEDGE_TITLES_PREFIX):
        return "B", "B_KNOWLEDGE_TITLES_PREFIX"
    if content is not None and t.startswith("[Pipeline] "):
        bare = t[len("[Pipeline] "):]
        fixture = A_KNOWLEDGE_FIXTURE_CONTENT.get(bare)
        if fixture is not None and content == fixture:
            return "A", "A_KNOWLEDGE_FIXTURE_CONTENT"
        selfdecl = A_KNOWLEDGE_SELF_DECLARED_CONTENT.get(bare)
        if selfdecl is not None and content == selfdecl:
            return "A", "A_KNOWLEDGE_SELF_DECLARED_CONTENT"
    if content is not None and (t, content) in A_KNOWLEDGE_PROBE_TITLE_CONTENT:
        return "A", "A_KNOWLEDGE_PROBE_TITLE_CONTENT"
    return "other", "unmatched"


def classify_tenant(name):
    """nest.db tenants 行残留分类（单一源词表，禁手抄；TL-⑦ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（e2e 探针租户名前缀）、other=不动。
    """
    if (name or "").startswith(A_TENANT_PREFIX):
        return "A", "A_TENANT_PREFIX"
    return "other", "unmatched"


def classify_manifest(name):
    """backups manifests.json 条目残留分类（单一源词表，禁手抄；TL-⑧ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（测试备份名硬标识子串）、other=不动。
    """
    n = name or ""
    if any(m in n for m in A_MANIFEST_NAME_MARKERS):
        return "A", "A_MANIFEST_NAME_MARKERS"
    return "other", "unmatched"


def classify_export(name, raw_text):
    """exports 导出文件残留分类（单一源词表，禁手抄；TL-⑬ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（文件名硬标识 / 内容硬标识），other=不动。
    判据两条（先匹配先得）：
      1) 文件名硬标识：name 以 'test-' 开头（test-export_export_*.json 探针导出）
      2) 内容硬标识：JSON 顶层非空 list 且每条 entry.name 全在 A_EXPORT_ENTRY_NAMES
         （=tests/test_marrow.py:75 fixture [{"type":"knowledge","name":"test"}] 与
         test2 探针字面；默认名 export_export_* 本身不是标识——真实用户导出同名，
         刻意不按名字判，宁窄勿滥）
    解析失败/顶层非 list/空 list=如实 other 不猜。
    """
    if (name or "").startswith(A_EXPORT_NAME_PREFIX):
        return "A", "A_EXPORT_NAME_PREFIX"
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return "other", "unparsed"
    if not isinstance(data, list) or not data:
        return "other", "not_nonempty_list"
    if all(isinstance(it, dict) and it.get("name") in A_EXPORT_ENTRY_NAMES for it in data):
        return "A", "A_EXPORT_ENTRY_NAMES"
    return "other", "unmatched"


def classify_sandbox(name, file_names):
    """sandboxes 目录残留分类（单一源词表，禁手抄；TL-⑭ 内容级清点消费）。

    返回 (cls, rule)：A=确证测试残留（目录名/内含文件名硬标识）、other=不动。
    随机 id 目录名（sandbox-<hex>）无硬标识时如实 other——TL-③「无法从名字判」
    语义逐字保留；内容级证据（inspect_sandbox_dir/timeline 引用）只作决策材料
    不改判（宁窄勿滥，删除动作仍待拍板）。
    """
    hay = [name or ""]
    hay.extend(file_names or [])
    for h in hay:
        hl = h.lower()
        for m in A_SANDBOX_NAME_MARKERS:
            if m in hl:
                return "A", "A_SANDBOX_NAME_MARKERS:" + m
    return "other", "unmatched"


def inspect_sandbox_dir(path: Path) -> dict:
    """沙箱目录内容级清点（只读事实，零推断）：文件清单/字节数/快照元数据/目录 mtime。

    快照（snap-*.json）解析失败或顶层非 dict 如实标 parsed=False，不猜内容。
    """
    name = path.name
    try:
        files = sorted(x.name for x in path.iterdir() if x.is_file())
    except OSError:
        files = []
    total_bytes = 0
    snapshots = []
    for f in files:
        fp = path / f
        try:
            total_bytes += fp.stat().st_size
        except OSError:
            pass
        if f.startswith("snap-") and f.endswith(".json"):
            meta = {"file": f, "parsed": False}
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                meta["parsed"] = True
                for k in ("snapshot_id", "name", "created_at", "log_length"):
                    meta[k] = data.get(k)
                meta["variables"] = data.get("variables")
            snapshots.append(meta)
    cls, rule = classify_sandbox(name, files)
    try:
        created = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        created = None
    return {
        "name": name, "cls": cls, "rule": rule,
        "file_count": len(files), "total_bytes": total_bytes, "files": files,
        "snapshots": snapshots, "dir_mtime": created,
    }


def sandbox_timeline_xref(conn, names) -> dict:
    """沙箱目录名在 timeline 事件面的引用计数（只读决策材料：有无事件引用）。"""
    out = {}
    for n in names:
        rows = conn.execute(
            "SELECT event_id, event_type FROM events WHERE summary LIKE ? OR detail LIKE ?",
            (f"%{n}%", f"%{n}%"),
        ).fetchall()
        out[n] = {"count": len(rows), "sample": [r[0] for r in rows[:3]]}
    return out


# ---- TL-⑯ B 类内容级清点（只读决策材料，零删除零分类改动）----

EXEC_SUMMARY_RE = re.compile(r"^🧪 Sandbox exec: exit=(-?\d+) (.*)$")
PIPELINE_NAME_RE = re.compile(r"completed: (\S+)")


def b_command_from_summary(summary):
    """🧪 Sandbox exec 行摘要 → (exit_code, 命令串) 归一（单一源，禁手抄）。

    形如 "🧪 Sandbox exec: exit=0 cat /workspace/state.txt" → (0,
    "cat /workspace/state.txt")；不匹配该形态原样返回 (None, 摘要)。
    """
    m = EXEC_SUMMARY_RE.match(summary or "")
    if m:
        return int(m.group(1)), m.group(2)
    return None, summary or ""


def b_class_content_facts(conn) -> dict:
    """B 类 timeline 行内容级清点（只读决策材料，TL-⑯）。

    对 classify_timeline 判 B 的行逐组产出决策材料：计数/时间窗/摘要清单
    （capped）+ 组专属事实（storm=来源 IP/威胁类型/分钟峰值；exec=命令清单/
    退出码/沙箱数；pipeline=文件名分布；brain=目标全文清单）。判法零改动
    （B 归类语义逐字不变），只加性产出——B 类取舍拍板前的"内容级"面
    （TL-⑭ sandboxes 内容级清点同哲学：无法从名字判的，用内容事实说话）。
    """
    groups: dict = {}
    for _eid, organ, etype, summary, detail, ts in conn.execute(
            "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp"
            " FROM events"):
        cls, rule = classify_timeline(organ, etype, summary, detail)
        if cls == "B":
            groups.setdefault(rule, []).append((summary or "", detail or "", ts))
    out = {}
    for rule, rows in groups.items():
        tss = [r[2] for r in rows if isinstance(r[2], (int, float))]
        entry = {
            "count": len(rows),
            "distinct_summaries": len(set(r[0] for r in rows)),
            "top_summaries": [{"summary": s, "count": n} for s, n in
                              Counter(r[0] for r in rows).most_common(20)],
        }
        if tss:
            entry["first_ts"] = datetime.fromtimestamp(min(tss)).isoformat()
            entry["last_ts"] = datetime.fromtimestamp(max(tss)).isoformat()
        if rule == "nerve_publish_rate_storm":
            ips, threats, minutes = Counter(), Counter(), Counter()
            for summary, detail, ts in rows:
                try:
                    d = json.loads(detail)
                except Exception:
                    d = {}
                ips[str(d.get("ip", "?"))] += 1
                for t in (d.get("threat_types") or []):
                    threats[str(t)] += 1
                if isinstance(ts, (int, float)):
                    minutes[datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")] += 1
            entry["source_ips"] = dict(ips.most_common(10))
            entry["threat_types"] = dict(threats.most_common(10))
            entry["peak_per_minute"] = max(minutes.values()) if minutes else 0
            entry["burst_minutes"] = len(minutes)
        elif rule == "sandbox_exec_cmd_no_hard_marker":
            cmds, exits, sandbox_ids = Counter(), Counter(), Counter()
            for summary, detail, ts in rows:
                code, cmd = b_command_from_summary(summary)
                cmds[cmd] += 1
                exits[str(code)] += 1
                try:
                    d = json.loads(detail)
                except Exception:
                    d = {}
                sandbox_ids[str(d.get("sandbox_id", "?"))] += 1
            entry["commands"] = [{"command": c, "count": n}
                                 for c, n in cmds.most_common(40)]
            entry["exit_codes"] = dict(exits.most_common(10))
            entry["distinct_sandbox_ids"] = len(sandbox_ids)
        elif rule == "pipeline_fixture_generic":
            names = Counter()
            for summary, detail, ts in rows:
                m = PIPELINE_NAME_RE.search(summary)
                names[m.group(1) if m else "?"] += 1
            entry["file_names"] = dict(names.most_common(40))
        elif rule == "brain_probe_style_goals":
            entry["goals"] = [{"goal": s, "count": n} for s, n in
                              Counter(r[0] for r in rows).most_common(40)]
        out[rule] = entry
    return out


def classify_user(username, email=None):
    """users 行残留分类（单一源词表，禁手抄；TL-⑩ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（测试用户名硬标识 / 测试邮箱硬标识）、other=不动。
    判据=TL-③ 清点原语义逐字保留（A_USERNAMES 用户名集 + email 含 'test'）；
    enterprise users 表无 email 列，email=None 时仅按用户名判（与 TL-③ 逐字一致）。
    """
    if (username or "") in A_USERNAMES:
        return "A", "A_USERNAMES"
    if "test" in (email or ""):
        return "A", "A_EMAIL_TEST"
    return "other", "unmatched"


def classify_group(name):
    """ai_groups 组行残留分类（单一源词表，禁手抄；TL-⑪ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（探针组名硬标识，TL-③ 清点
    "探针组 A群组/B群组"判据原语义逐字保留）、other=不动。
    """
    if (name or "") in A_GROUP_NAMES:
        return "A", "A_GROUP_NAMES"
    return "other", "unmatched"


def census_group_rule(name):
    """classify_group 的 rule 侧单取（census_sqlite 收编消费）。"""
    return classify_group(name)[1]


def classify_perm_audit(session_id, tool_name):
    """perm_decision_audit 行残留分类（单一源词表，禁手抄；TL-⑨ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（测试会话硬标识 / 测试工具名硬标识）、other=不动。
    词表复用既有硬标识（禁第二套判法）：A_SESSIONS/A_SESSION_PREFIX=TL-③ 清点判法
    原语义逐字保留；A_RULE_TOOLS=test_dangerous_tool（TL-⑤ perm_rules 失效化同款
    硬标识，TL-⑤ 留档点名"test_dangerous_tool 历史决策 105 行留拆件②"）。
    """
    s = session_id or ""
    if s in A_SESSIONS:
        return "A", "A_SESSIONS"
    if s.startswith(("p0", "py-")):
        return "A", "A_SESSION_PREFIX"
    if tool_name in A_RULE_TOOLS:
        return "A", "A_RULE_TOOLS"
    return "other", "unmatched"


# ai_groups 悬挂残留行（组行已不在场的子表行）硬标识（A 类）
A_ORPHAN_GROUP_ID_EXACT = {"test-group-123", "a1proof"}
A_ORPHAN_GROUP_ID_PREFIX = ("test-",)
A_ORPHAN_TEXT_MARKERS = (
    "防缓存标记",            # e2e 探针防缓存标记（ACC*）
    "广播探针",              # 广播探针A→B/B→A 探针字面
    "Hello broadcast test",  # test-broadcast 探针字面
    "Hello from client",     # test-group-123 探针字面
    "test discussion",       # 探针任务名
    "测试任务：",             # 测试任务前缀
    "请分三句话介绍你自己",   # 会话探针目标全文（刻意不用泛化 B_GOAL_MARKERS 短语防误杀真用户）
    "e2e隔离验证", "e2e匿名任务", "e2e 场景", "e2e 超龄任务治理",
    "xyzabc",                # F2b-② 控制组占位符探针
)
ORPHAN_SCAN_TABLES = (
    "ai_group_agents", "ai_group_tasks", "discussion_messages",
    "task_execution_events", "agent_scores", "agent_capabilities",
)


def classify_orphan_group(group_id, row_texts):
    """ai_groups 悬挂残留行（组行已不在场的子表行）按组分类（单一源词表，禁手抄；
    TL-⑫ 删除工具同源消费）。

    返回 (cls, rule)：A=确证测试残留（组 id 硬标识 / 行文本硬标识）、other=不动。
    组级判定（任一行文本命中即整组 A）＝TL-③「随组处置比逐行删稳」语义；
    词表=组 id 硬标识+探针字面+复用 A_GOAL_MARKERS 既有 A 类硬标识（禁第二套
    判法），泛化短语（B_GOAL_MARKERS）刻意不收防误杀真用户同款提问（宁窄勿滥）。
    """
    g = group_id or ""
    if g in A_ORPHAN_GROUP_ID_EXACT:
        return "A", "A_ORPHAN_GROUP_ID_EXACT"
    if g.startswith(A_ORPHAN_GROUP_ID_PREFIX):
        return "A", "A_ORPHAN_GROUP_ID_PREFIX"
    markers = tuple(A_ORPHAN_TEXT_MARKERS) + tuple(A_GOAL_MARKERS)
    for t in row_texts:
        s = t if t is not None else ""
        for m in markers:
            if m in s:
                return "A", "A_ORPHAN_TEXT_MARKERS"
    return "other", "unmatched"


def orphan_group_texts(conn) -> dict:
    """悬挂行按组归集行文本（group_id -> [行文本]，行文本=行内非空列值拼接）。

    census_sqlite 与 TL-⑫ 删除工具共用同一取数口（禁两套扫描）。
    """
    out = {}
    for t in ORPHAN_SCAN_TABLES:
        cur = conn.execute(
            f"SELECT * FROM {t} WHERE group_id NOT IN (SELECT id FROM ai_groups)")
        gi = [d[0] for d in cur.description].index("group_id")
        for row in cur:
            out.setdefault(row[gi], []).append(
                " ".join(str(v) for v in row if v is not None))
    return out


# ---- PROACT-② proactive.db 历史测试噪音清点（只读决策材料，零删除零写入）----
# 同秒同 watcher 重复=确证测试残留（机制内证非行内容）：ProactiveEngine.run_watcher
# 串行执行且每次执行更新 last_run_at（src/will/proactive.py:159/:185），心跳 tick 每
# 60s 每 watcher 至多跑一次（:146-157）——单一引擎每秒每 watcher 至多 1 行；同秒同
# watcher 多行只能来自 >=2 并发写方=TestClient(src.main) lifespan 首 tick burst
# （PROACT-① 定性：register_default_watchers 重置 last_run_at=0 → 全部 watcher 立即
# 到期 → 首 tick 全量跑）或探针手动 /run 撞同秒（探针本身即测试写方）。边界如实记：
# 生产用户手动 /run 与心跳/另一手动同秒碰撞会被误伤（生产至今零真实用户使用面）。
A_RULE_PROACTIVE_DUP = "same_second_watcher_duplicate"
# 同秒多行秒内的单行=疑似（B）：启动首 tick 形态（服务重启与单 TestClient 完全同形，
# 行内容零差异）与多 watcher 到期心跳 tick 形态（生产真实心跳同形）无法从行内容区分，
# 按证据层（journal 启动窗/心跳节奏链）裁决——证据层只作决策材料不改判（TL-⑯ 先例）。
B_RULE_PROACTIVE_SEC_SINGLE = "multi_row_second_singleton"
# 心跳节奏证据层用（出处 src/will/proactive_behaviors.py:153 DEFAULT_INTERVALS，
# 禁手抄漂移——如改以真源为准）。
PROACTIVE_INTERVALS = {
    "memory_decay": 21600.0,
    "experience_collect": 3600.0,
    "heredity_triggers": 1800.0,
    "pattern_extract": 7200.0,
    "advisory_review": 14400.0,
    "self_evolve": 28800.0,
}
# PROACT-① 收口时刻（2026-10-14 15:37 CST=07:37 UTC）：in-process 测试写方自此隔离
# 进 tmp（PROACTIVE_DB env），此后新增 A 类行=隔离仍在漏（增长闸门，after_tl2_6 同款）。
PROACTIVE_CUTOFF = 1791963420.0
# 服务启动首 tick 证据层窗口（秒）：journal "Started OpenSoul Backend" 后 lifespan
# 首 tick 全量跑落行，实测滞后 2.5~5s，+-15s 覆盖。
PROACTIVE_START_WINDOW_S = 15.0


def classify_proactive(watcher_sec_count, sec_row_count):
    """proactive_events 行分类（纯函数单一源，禁在别处手抄判法）。

    输入=机制事实（同秒同 watcher 行数、同秒总行数）而非行内容——判据来自引擎写入
    机制（run_watcher 串行 + last_run_at + 60s tick）。A 优先于 B（同秒重复组里的
    单行组外成员仍按各自组判）。
    """
    if watcher_sec_count >= 2:
        return "A", A_RULE_PROACTIVE_DUP
    if sec_row_count >= 2:
        return "B", B_RULE_PROACTIVE_SEC_SINGLE
    return "U", "unmatched"


def _proactive_db():
    """proactive.db 路径（出处 src/will/proactive.py:45 _default_events_db：PROACTIVE_DB
    env 优先，缺省 REPO/data/proactive.db）。"""
    env = os.environ.get("PROACTIVE_DB")
    return Path(env) if env else REPO / "data" / "proactive.db"


def load_journal_starts():
    """journalctl 只读取证：opensoul 服务启动时刻（证据层输入，非分类判据）。

    不可用（无 journal/超时/异常）如实返回 None——证据层缺失禁猜。
    """
    try:
        proc = subprocess.run(
            ["journalctl", "--user", "-u", "opensoul", "--since", "2026-10-09",
             "--output=short-iso"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return None
    starts = []
    for line in proc.stdout.splitlines():
        if "Started OpenSoul Backend" not in line:
            continue
        try:
            starts.append(datetime.fromisoformat(line.split()[0]).timestamp())
        except ValueError:
            continue
    return sorted(starts)


def proactive_row_verdicts(rows, starts):
    """proactive_events 逐行 (分类, 规则, 证据层) 单一源（PROACT-④ 删除工具同源消费）。

    rows=[(rid, watcher, started_at_iso)]（调用方按 started_at, id 排序）；
    starts=load_journal_starts() 返回值（None=journal 不可用，layer 恒 'orphan'——
    调用方须看 orphan_reliable 禁当删除清单用）。判法=census_proactive 原实现逐字
    收编（classify_proactive 分类 + near_start/cadence/orphan 证据层），禁第二套。
    返回 (verdicts, excess_ids)：verdicts={rid: (cls, rule, layer)}；
    excess_ids=同秒同 watcher 重复组内除最早一行外的行（测试行数硬下界 n-1）。
    """
    sec_w = Counter()
    sec_tot = Counter()
    for _rid, w, ts in rows:
        sec = ts[:19]
        sec_w[(sec, w)] += 1
        sec_tot[sec] += 1

    def near_start(epoch):
        if not starts:
            return False
        i = bisect.bisect_left(starts, epoch)
        for j in (i - 1, i):
            if 0 <= j < len(starts) and abs(starts[j] - epoch) <= PROACTIVE_START_WINDOW_S:
                return True
        return False

    # 心跳节奏链（cadence）：同 watcher 按时间序贪心成链，相邻入选行间隔 >= interval-5
    # ——生产心跳的形状上界（链可能被同距测试行抢占/接纳孤立测试行，如实记不精判）。
    cadence = set()
    last = {}
    parsed = []  # (rid, w, ts, epoch)
    for rid, w, ts in rows:
        epoch = datetime.fromisoformat(ts).timestamp()
        parsed.append((rid, w, ts, epoch))
        interval = PROACTIVE_INTERVALS.get(w)
        if interval is None:
            continue
        prev = last.get(w)
        if prev is None or epoch - prev >= interval - 5.0:
            cadence.add(rid)
            last[w] = epoch
    verdicts = {}
    excess_ids = []
    seen = Counter()
    for rid, w, ts, epoch in parsed:
        sec = ts[:19]
        cls, rule = classify_proactive(sec_w[(sec, w)], sec_tot[sec])  # 单一源，禁手抄
        if near_start(epoch):
            layer = "near_start"
        elif rid in cadence:
            layer = "cadence"
        else:
            layer = "orphan"
        verdicts[rid] = (cls, rule, layer)
        if cls == "A":
            k = (sec, w)
            seen[k] += 1
            if seen[k] > 1:
                excess_ids.append(rid)
    return verdicts, sorted(excess_ids)


def ro(path: Path) -> sqlite3.Connection:
    assert path.exists(), f"missing: {path}"
    return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)


def census_timeline(out: dict) -> None:
    c = ro(REPO / "data" / "timeline.db")
    total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    counts = {"A": Counter(), "B": Counter(), "U": Counter()}
    assigned: dict[str, set[str]] = {"A": set(), "B": set(), "U": set()}
    late_a = 0
    late_b = 0
    cutoff = 1791894600.0  # 2026-10-13 20:30 CST = TL-②-⑥ 收口时刻
    for eid, organ, etype, summary, detail, ts in c.execute(
            "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events"):
        cls, rule = classify_timeline(organ, etype, summary, detail)
        counts[cls][f"{organ}/{etype}:{rule}"] += 1
        if cls in ("A", "B"):
            assigned[cls].add(eid)
        if cls == "A" and ts > cutoff:
            late_a += 1
        if cls == "B" and ts > cutoff:
            late_b += 1
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
    # B 类同款闸门（TL-⑯：隔离收口后 B 类新增同样应为 0）
    out["timeline"]["after_tl2_6_cutoff_B"] = late_b
    # B 类内容级清点（TL-⑯ 加性键：判法零改动，只产出决策材料）
    out["timeline"]["b_class_detail"] = b_class_content_facts(c)


def census_sqlite(out: dict) -> None:
    res: dict = {}
    # opensoul.db：users / knowledge / metacognition_log
    c = ro(REPO / "data" / "opensoul.db")
    users = c.execute("SELECT id,username,email FROM users").fetchall()
    res["opensoul_users"] = {
        "total": len(users),
        "A": [{"id": u[0], "username": u[1]} for u in users
              if classify_user(u[1], u[2])[0] == "A"],  # 单一源，禁手抄（TL-⑩ 收编）
    }
    kn = c.execute("SELECT id,title,content FROM knowledge").fetchall()
    a_kn = [k for k in kn if classify_knowledge(k[1], k[2])[0] == "A"]
    b_kn = [k for k in kn if classify_knowledge(k[1], k[2])[0] == "B"]
    o_kn = [k for k in kn if classify_knowledge(k[1], k[2])[0] == "other"]
    res["opensoul_knowledge"] = {
        "total": len(kn), "A": len(a_kn), "B": len(b_kn),
        "A_titles": dict(Counter(k[1] for k in a_kn)),
        "B_titles": dict(Counter(k[1] for k in b_kn)),
        "other_titles": dict(Counter(k[1] for k in o_kn).most_common(20)),
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
        "A": [{"id": u[0], "username": u[1]} for u in eusers
              if classify_user(u[1])[0] == "A"],  # 单一源，禁手抄（TL-⑩ 收编；无 email 列）
    }
    # ai_groups.db：goal 级分类（父任务归类，子任务随父）
    c = ro(REPO / "data" / "ai_groups.db")
    tasks = c.execute("SELECT id,goal FROM ai_group_tasks").fetchall()
    a_t = [t for t in tasks if any(m in (t[1] or "") for m in A_GOAL_MARKERS)]
    b_t = [t for t in tasks if t not in a_t and any(m in (t[1] or "") for m in B_GOAL_MARKERS)]
    groups = c.execute("SELECT id,name FROM ai_groups").fetchall()
    orphan_texts = orphan_group_texts(c)  # 悬挂行按组归集（单一源取数口，TL-⑫ 收编）
    orphan_a = []
    for og, ots in sorted(orphan_texts.items()):
        ocls, orule = classify_orphan_group(og, ots)  # 单一源，禁手抄（TL-⑫ 收编）
        if ocls == "A":
            orphan_a.append({"group_id": og, "rule": orule, "rows": len(ots)})
    res["ai_groups"] = {
        "groups_total": len(groups),
        "groups": [{"group_id": g[0], "name": g[1]} for g in groups],
        "groups_A": [{"group_id": g[0], "name": g[1],
                      "rule": census_group_rule(g[1])} for g in groups
                     if classify_group(g[1])[0] == "A"],  # 单一源，禁手抄（TL-⑪ 收编）
        "tasks_total": len(tasks),
        "tasks_A": len(a_t), "tasks_B": len(b_t),
        "tasks_U": len(tasks) - len(a_t) - len(b_t),
        "orphan_rows_by_table": {
            t: c.execute(
                f"SELECT COUNT(*) FROM {t} WHERE group_id NOT IN"
                " (SELECT id FROM ai_groups)").fetchone()[0]
            for t in ("ai_group_agents", "ai_group_tasks", "discussion_messages",
                      "task_execution_events", "agent_scores", "agent_capabilities")
        },  # 悬挂行（组行已不在场）加性键，TL-⑪ 发现面
        "orphan_groups_A": orphan_a,  # 悬挂残留行 A 组清单（TL-⑫ 删除工具清单源）
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
    rows = c.execute(
        "SELECT decision_id,session_id,tool_name FROM perm_decision_audit").fetchall()
    a_by_sess: Counter = Counter()
    a_by_rule: Counter = Counter()
    u_by_sess: Counter = Counter()
    a_ids: list = []
    real = 0
    for did, sess, tool in rows:
        cls, rule = classify_perm_audit(sess, tool)  # 单一源，禁手抄（TL-⑨ 收编）
        if cls == "A":
            a_by_sess[sess or ""] += 1
            a_by_rule[rule] += 1
            a_ids.append(did)
        elif (sess or "").startswith("om-"):
            real += 1
        else:
            u_by_sess[sess or ""] += 1
    rules = c.execute("SELECT rule_id,tool_name,source FROM perm_rules WHERE is_deleted=0").fetchall()
    a_rules = [r for r in rules if classify_rule(r[1], r[2])[0] == "A"]
    out["permission"] = {
        "decision_audit_total": len(rows),
        "A_by_session": dict(a_by_sess), "A_total": sum(a_by_sess.values()),
        "A_by_rule": dict(a_by_rule),
        "A_decision_ids": sorted(a_ids),
        "U_by_session": dict(u_by_sess), "U_total": sum(u_by_sess.values()),
        "real_om_sessions_total": real,
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
               if isinstance(it, dict) and classify_manifest(str(it.get("name", "")))[0] == "A"]
    tars = sorted(x.name for x in bdir.glob("*.tar.gz"))
    res["backups"] = {
        "manifest_entries": len(items),
        "A_dangling_test_entries": [{"backup_id": it.get("backup_id"), "name": it.get("name")} for it in a_items],
        "tar_files_on_disk": len(tars),
        "tar_name_range": [tars[0], tars[-1]] if tars else [],
    }
    edir = HOME / ".opensoul" / "exports"
    exports = sorted(x.name for x in edir.glob("*.json")) if edir.is_dir() else []
    a_files = []
    a_rules = Counter()
    for _e in exports:
        _raw = ""
        try:
            _raw = (edir / _e).read_text(encoding="utf-8")
        except OSError:
            pass
        _cls, _rule = classify_export(_e, _raw)
        if _cls == "A":
            a_files.append(_e)
            a_rules[_rule] += 1
    res["exports"] = {
        "total": len(exports),
        "B_uniform_export_export": sum(1 for e in exports if e.startswith("export_export_")),
        "sample": exports[:3],
        "A_files": a_files,
        "A_total": len(a_files),
        "A_by_rule": dict(a_rules),
    }
    sdir = HOME / ".opensoul" / "sandboxes"
    sdirs = sorted((x for x in sdir.iterdir() if x.is_dir()), key=lambda x: x.name) if sdir.is_dir() else []
    entries = [inspect_sandbox_dir(x) for x in sdirs]
    xref: dict = {}
    tl_path = REPO / "data" / "timeline.db"
    if tl_path.exists():
        try:
            xref = sandbox_timeline_xref(ro(tl_path), [e["name"] for e in entries])
        except (OSError, sqlite3.Error):
            xref = {}
    for e in entries:
        e["timeline_xref"] = xref.get(e["name"], {"count": 0, "sample": []})
    res["sandboxes"] = {
        "total": len(entries),
        "U_names": [e["name"] for e in entries if e["cls"] != "A"],
        "A_names": [e["name"] for e in entries if e["cls"] == "A"],
        "empty_dirs": sum(1 for e in entries if e["file_count"] == 0),
        "user_data_bytes": sum(e["total_bytes"] for e in entries),
        "entries": entries,
        "note": "内容级清点（TL-⑭）：文件/字节/快照元数据/timeline 引用计数为只读事实；"
                "名字硬标识判 A，随机 id 目录如实 other（内容证据只作决策材料，删除待拍板）",
    }
    out["files"] = res


def census_proactive(out):
    """proactive.db 清点（PROACT-②）：classify_proactive 单一源分类 + 证据层。

    证据层（journal 启动窗 near_start / 心跳节奏链 cadence / 其余 orphan）只作决策
    材料不改判（TL-⑯ 先例）；orphan=非服务可归因形态（测试 burst 形态），是删除
    候选下界——journal 不可用时 orphan_reliable=False 禁当删除清单用。
    """
    c = ro(_proactive_db())
    rows = c.execute(
        "SELECT id, watcher, started_at FROM proactive_events ORDER BY started_at, id"
    ).fetchall()
    starts = load_journal_starts()
    verdicts, excess_ids = proactive_row_verdicts(rows, starts)  # 单一源（PROACT-④ 收编）
    counts = {"A": Counter(), "B": Counter(), "U": Counter()}
    assigned = {"A": [], "B": [], "U": []}
    layers = {"near_start": [], "cadence": [], "orphan": []}
    a_near_start = 0
    after = {"A": 0, "B": 0, "U": 0}
    for rid, w, ts in rows:
        cls, rule, layer = verdicts[rid]
        counts[cls][w + ":" + rule] += 1
        assigned[cls].append(rid)
        layers[layer].append(rid)
        if layer == "near_start" and cls == "A":
            a_near_start += 1
        if datetime.fromisoformat(ts).timestamp() > PROACTIVE_CUTOFF:
            after[cls] += 1
    # ---- 证据层（只作决策材料，不改判；计算已收编 proactive_row_verdicts）----

    def layer_ids(cls, layer):
        return sorted(rid for rid in assigned[cls] if verdicts[rid][2] == layer)

    sec_w = Counter()
    for _rid, w, ts in rows:
        sec_w[(ts[:19], w)] += 1
    dup_groups = {k: v for k, v in sec_w.items() if v >= 2}
    journal_info = {
        "available": starts is not None,
        "service_starts": (len(starts) if starts else 0),
        "near_window_s": PROACTIVE_START_WINDOW_S,
    }
    if starts:
        journal_info["first_start_utc"] = datetime.fromtimestamp(
            starts[0], timezone.utc).isoformat()
        journal_info["last_start_utc"] = datetime.fromtimestamp(
            starts[-1], timezone.utc).isoformat()
    out["proactive"] = {
        "db": str(_proactive_db()),
        "total": len(rows),
        "A": len(assigned["A"]),
        "B": len(assigned["B"]),
        "U": len(assigned["U"]),
        "A_by_rule": dict(counts["A"].most_common()),
        "B_by_rule": dict(counts["B"].most_common()),
        "A_event_ids": sorted(assigned["A"]),
        "A_excess_event_ids": sorted(excess_ids),
        "B_event_ids": sorted(assigned["B"]),
        "U_event_ids": sorted(assigned["U"]),
        "B_near_start_ids": layer_ids("B", "near_start"),
        "B_cadence_ids": layer_ids("B", "cadence"),
        "B_orphan_ids": layer_ids("B", "orphan"),
        "U_near_start_ids": layer_ids("U", "near_start"),
        "U_cadence_ids": layer_ids("U", "cadence"),
        "U_orphan_ids": layer_ids("U", "orphan"),
        "facts": {
            "by_watcher": dict(Counter(w for _r, w, _t in rows).most_common()),
            "dup_groups": len(dup_groups),
            "dup_group_peak_per_sec": (max(dup_groups.values()) if dup_groups else 0),
            "A_excess_lower_bound": len(excess_ids),
            "A_near_start_rows": a_near_start,
            "journal": journal_info,
            "layers_total": {k: len(v) for k, v in layers.items()},
            "orphan_reliable": starts is not None,
            "after_proact1_cutoff": after,
        },
        "note": "分类=classify_proactive 单一源（同秒同 watcher 重复=确证测试残留）；"
                "证据层只作决策材料不改判；删除动作待拍板（TL-④ 纪律：清单精确删+备份+回读）",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="把清点结果落成 JSON 工件")
    args = ap.parse_args()
    out: dict = {"tool": "census_test_residue", "read_only": True}
    census_timeline(out)
    census_sqlite(out)
    census_permission(out)
    census_files(out)
    census_proactive(out)
    text = json.dumps(out, ensure_ascii=False, indent=1, default=str)
    print(text)
    if args.json:
        Path(args.json).write_text(text)
        print(f"[written] {args.json}")


if __name__ == "__main__":
    main()

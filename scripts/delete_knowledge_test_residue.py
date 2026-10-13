#!/usr/bin/env python3
"""TL-⑥ 删除执行件②a——knowledge A 类测试残留+级联精确删（备份+回读）。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：opensoul.db knowledge 391 行 A 类（fixture 标题前缀），删除需级联
knowledge_chunks / knowledge_tags（TL-③ 边界②）。本工具按单一源判法精确删：
  - 判单一源：census.classify_knowledge（禁发明新判法）；删前逐行复核（fresh 重读
    再判），现判非 A=拒删一行不动（清单陈旧/错配=拒，禁删非测试行）
  - 级联三面：knowledge / knowledge_chunks / knowledge_tags 按 knowledge_id 精确删
    （tags 表本体不动——标签可被真知识共享；孤儿标签非本拍范围）
  - 外部索引随删（服务删除路径同款语义 src/services/knowledge.py delete_knowledge）：
    Meilisearch delete-batch（尽力而为：失败如实报不阻断 DB 删除，禁静默；
    qdrant 向量点——A 行 chunks embedding_id 全空（TL-③ 清点实证零点数））
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点 A/非 A、级联 orphan、非 A 面一行不动（允许生产并发新写入，
    只查删前在场行），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。

范围（拆件②a）：knowledge 391 行 + 级联 chunks/knowledge_tags。
拆件②b/c 留待拍：测试用户 4+2 / e2e 租户 2 / perm 决策审计 612 /
manifests 悬挂 5 / 探针组 2（各自级联语义需各自备份+回读）。

用法：
  python3 scripts/delete_knowledge_test_residue.py [--apply] [--report data/xxx.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import census_test_residue as census  # noqa: E402  单一源分类判法，禁手抄

DDL = ("knowledge", "knowledge_chunks", "knowledge_tags")


class RefuseError(RuntimeError):
    """删前复核不过=拒删（工件不可信/清单错配），一行不动。"""


def load_artifact_knowledge(artifact: Path) -> dict:
    """从 TL-③ 清点工件取 knowledge 面计数（工件不可信=拒删）。"""
    data = json.loads(Path(artifact).read_text())
    try:
        kn = data["sqlite"]["opensoul_knowledge"]
        a = int(kn["A"])
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 opensoul_knowledge.A 键，工件不可信，拒删：{e}") from e
    return {"A": a, "A_titles": dict(kn.get("A_titles") or {}), "B": int(kn.get("B") or 0)}


def scan(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_knowledge）+级联面在场清单。"""
    rows = conn.execute("SELECT id,title,content FROM knowledge").fetchall()
    a_ids: list[str] = []
    other_ids: set[str] = set()
    b = 0
    for kid, title, content in rows:
        cls, _rule = census.classify_knowledge(title, content)
        if cls == "A":
            a_ids.append(kid)
        elif cls == "B":
            b += 1
            other_ids.add(kid)
        else:
            other_ids.add(kid)
    a_set = set(a_ids)
    chunks = conn.execute("SELECT id,knowledge_id FROM knowledge_chunks").fetchall()
    links = conn.execute("SELECT knowledge_id,tag_id FROM knowledge_tags").fetchall()
    chunk_ids = {c[0] for c in chunks}
    link_pairs = {(l[0], l[1]) for l in links}
    return {
        "knowledge_total": len(rows),
        "A_ids": a_ids,
        "other_ids": other_ids,
        "chunks_A": {c[0] for c in chunks if c[1] in a_set},
        "chunks_other": {c[0] for c in chunks if c[1] not in a_set},
        "links_A": {(l[0], l[1]) for l in links if l[0] in a_set},
        "links_other": {(l[0], l[1]) for l in links if l[0] not in a_set},
        "chunk_ids": chunk_ids,
        "link_pairs": link_pairs,
        "orphan_chunks": sum(1 for c in chunks if c[1] not in {r[0] for r in rows}),
        "orphan_links": sum(1 for l in links if l[0] not in {r[0] for r in rows}),
        "B": b,
    }


def recheck(conn: sqlite3.Connection, ids: list[str]) -> None:
    """删前逐行复核：fresh 重读每行再判（单一源），现判非 A=拒删。"""
    bad = []
    for kid, title, content in conn.execute(
            "SELECT id,title,content FROM knowledge WHERE id IN (%s)"
            % ",".join("?" * len(ids)), ids):
        cls, rule = census.classify_knowledge(title, content)
        if cls != "A":
            bad.append({"id": kid, "title": title, "cls": cls, "rule": rule})
    if bad:
        raise RefuseError(f"{len(bad)} 条待删行现判非 A（清单陈旧/错配），拒删：{bad[:5]}")


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"opensoul_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"path": str(dest), "sha256": digest, "bytes": dest.stat().st_size}


def delete_cascade(conn: sqlite3.Connection, ids: list[str]) -> dict:
    """按 knowledge_id 精确删三面（子表先删，单事务）。"""
    ph = ",".join("?" * len(ids))
    n_links = conn.execute(
        f"DELETE FROM knowledge_tags WHERE knowledge_id IN ({ph})", ids).rowcount
    n_chunks = conn.execute(
        f"DELETE FROM knowledge_chunks WHERE knowledge_id IN ({ph})", ids).rowcount
    n_kn = conn.execute(
        f"DELETE FROM knowledge WHERE id IN ({ph})", ids).rowcount
    conn.commit()
    return {"knowledge": n_kn, "chunks": n_chunks, "links": n_links}


def meili_delete(ids: list[str], url: str, key: str, index: str,
                 wait_s: float = 5.0) -> dict:
    """Meilisearch delete-batch（尽力而为：失败如实报；成功=轮询任务真完成）。"""
    body = json.dumps(ids).encode()
    req = urllib.request.Request(
        f"{url}/indexes/{index}/documents/delete-batch",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    out: dict = {"attempted": len(ids), "index": index, "url": url}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            out["enqueue"] = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    task_uid = (out.get("enqueue") or {}).get("taskUid")
    if task_uid is None:
        out["ok"] = False
        out["error"] = "enqueue 响应无 taskUid"
        return out
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            poll = urllib.request.Request(
                f"{url}/tasks/{task_uid}",
                headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(poll, timeout=10) as resp:
                task = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as e:
            out["ok"] = False
            out["error"] = f"task poll failed: {type(e).__name__}: {e}"
            return out
        if task.get("status") in ("succeeded", "failed", "canceled"):
            out["task_status"] = task.get("status")
            out["ok"] = task.get("status") == "succeeded"
            if not out["ok"]:
                out["error"] = f"meili task {task_uid} status={task.get('status')}"
            return out
        time.sleep(0.2)
    out["ok"] = False
    out["error"] = f"meili task {task_uid} {wait_s}s 内未完成（异步任务，稍后可查 /tasks/{task_uid}）"
    return out


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Path | None = None, report_path: Path | None = None,
        meili_url: str | None = None, meili_key: str | None = None,
        meili_index: str | None = None) -> dict:
    art = load_artifact_knowledge(artifact)
    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        ids = list(pre["A_ids"])
        # 删前逐行复核：fresh 重读再判（单一源），现判非 A=拒删
        if ids:
            recheck(conn, ids)

        # 外部索引配置单一源=src/config.py settings（禁手抄）
        if meili_url is None or meili_key is None or meili_index is None:
            from src.config import settings  # noqa: PLC0415 仅 apply 面消费
            meili_url = meili_url or settings.meili_url
            meili_key = meili_key or settings.meili_key
            meili_index = meili_index or settings.meili_index

        report: dict = {
            "tool": "delete_knowledge_test_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "artifact_A": art["A"], "live_A": len(ids),
            "artifact_drift": art["A"] != len(ids),
            "matched": len(ids),
            "pre": _counts(pre),
            "deleted_ids": ids,
        }
        if not apply:
            report["deleted"] = {"knowledge": 0, "chunks": 0, "links": 0}
            report["meili"] = {"attempted": 0, "planned": len(ids), "ok": None,
                               "note": "dry-run 零调用"}
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        report["deleted"] = delete_cascade(conn, ids) if ids else {
            "knowledge": 0, "chunks": 0, "links": 0}
        report["meili"] = meili_delete(ids, meili_url, meili_key, meili_index) if ids else {
            "attempted": 0, "ok": None}

        post = scan(conn)
        # 回读对账（允许生产并发新写入，只查删前在场行）
        gone_expected_kn = set(ids)
        post_kn = set(post["A_ids"]) | post["other_ids"]
        still_there = gone_expected_kn & post_kn
        if still_there:
            raise RuntimeError(f"回读对账不过：待删行仍在场 {sorted(still_there)[:5]}")
        missing_other = pre["other_ids"] - post_kn
        if missing_other:
            raise RuntimeError(f"非 A 行被误删 {len(missing_other)} 行：{sorted(missing_other)[:5]}")
        missing_chunks = pre["chunks_other"] - post["chunk_ids"]
        if missing_chunks:
            raise RuntimeError(f"非 A 级联 chunk 被误删 {len(missing_chunks)}")
        missing_links = pre["links_other"] - post["link_pairs"]
        if missing_links:
            raise RuntimeError(f"非 A 级联 link 被误删 {len(missing_links)}")
        left_chunks = pre["chunks_A"] & post["chunk_ids"]
        left_links = pre["links_A"] & post["link_pairs"]
        if left_chunks or left_links:
            raise RuntimeError(
                f"回读对账不过：A 级联残留 chunks={len(left_chunks)} links={len(left_links)}")
        if post["orphan_chunks"] > pre["orphan_chunks"] or post["orphan_links"] > pre["orphan_links"]:
            raise RuntimeError(
                f"orphan 增长：chunks {pre['orphan_chunks']}->{post['orphan_chunks']} "
                f"links {pre['orphan_links']}->{post['orphan_links']}")
        report.update({
            "post": _counts(post),
            "post_A_ids_not_in_deleted": sorted(set(post["A_ids"]) - gone_expected_kn),
            "non_A_missing": sorted(missing_other),
            "orphan_chunks": {"pre": pre["orphan_chunks"], "post": post["orphan_chunks"]},
            "orphan_links": {"pre": pre["orphan_links"], "post": post["orphan_links"]},
        })
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _counts(s: dict) -> dict:
    return {
        "knowledge_total": s["knowledge_total"],
        "A": len(s["A_ids"]), "other": len(s["other_ids"]), "B": s["B"],
        "chunks_A": len(s["chunks_A"]), "chunks_other": len(s["chunks_other"]),
        "links_A": len(s["links_A"]), "links_other": len(s["links_other"]),
    }


def _emit(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "opensoul.db"))
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    ap.add_argument("--meili-url")
    ap.add_argument("--meili-key")
    ap.add_argument("--meili-index")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None,
        meili_url=args.meili_url, meili_key=args.meili_key,
        meili_index=args.meili_index)


if __name__ == "__main__":
    main()

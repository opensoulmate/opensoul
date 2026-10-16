#!/usr/bin/env python3
"""H1-① live e2e 探针：去重删除清检索面（幽灵搜索结果收口）。

用法：
  python3 scripts/e2e_dedup_search_face.py --pre   # 修前探针（期望检索面残留=缺口实证）
  python3 scripts/e2e_dedup_search_face.py          # 修后全绿

真实栈 urllib → :8090（真 meilisearch :7700 真检索面）：建 2 条同文知识（sim=1.0）
→ 全文检索双命中 → POST /api/dedup/deduplicate 删重复条目 → 检索面复查：
去重删掉的条目不得再被全文检索命中（修前=幽灵命中，H0 T1 真缺口）。
探针自带清痕（knowledge 三表/users 按标记行精确删+meili ghost doc 直清+回读
remaining=0）。测试行绝不留生产库/生产索引。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"
MARK = "e2e9e1"
sys.path.insert(0, "/home/climbing/opensoul")

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path, body=None, headers=None, form: bool = False):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def q(sql, params=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def exec_sql(sql, params=()):
    con = sqlite3.connect(DB)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def fulltext_ids(query: str) -> list[str]:
    """全文检索读面（用户可见幽灵命中面）：返回命中 knowledge id 列表。"""
    status, body = call("GET", "/api/search/?" + urllib.parse.urlencode({"q": query, "mode": "fulltext", "limit": 20}))
    if status != 200:
        return []
    return [str(r.get("id")) for r in json.loads(body).get("results", [])]


def meili_doc_exists(doc_id: str) -> bool:
    """meili 文档级硬证（应用自有 meili client 同索引，无 key 硬编码）。"""
    from src.database.meilisearch import meili_client  # noqa: PLC0415

    try:
        meili_client.get_document(doc_id)
        return True
    except Exception:
        return False


def cleanup(ids: list[str], uid: str, token: str) -> None:
    """清痕：knowledge 三表按标记行删+meili ghost doc 直清+users 标记行删。"""
    for kid in ids:
        call("DELETE", f"/api/knowledge/{kid}?user_id={uid}", headers={"Authorization": f"Bearer {token}"})
    # ghost doc（DB 行已无、meili doc 残留）：探针直清（修前 dedup 不清的残留）
    from src.database.meilisearch import meili_client  # noqa: PLC0415

    for kid in ids:
        try:
            meili_client.delete_document(kid)
        except Exception:
            pass
    exec_sql("DELETE FROM knowledge_chunks WHERE knowledge_id IN (SELECT id FROM knowledge WHERE title LIKE ?)", (f"%{MARK}%",))
    exec_sql("DELETE FROM knowledge_tags WHERE knowledge_id IN (SELECT id FROM knowledge WHERE title LIKE ?)", (f"%{MARK}%",))
    exec_sql("DELETE FROM knowledge WHERE title LIKE ?", (f"%{MARK}%",))
    exec_sql("DELETE FROM users WHERE username LIKE ?", (f"%{MARK}%",))
    remaining = (
        len(q("SELECT id FROM knowledge WHERE title LIKE ?", (f"%{MARK}%",)))
        + len(q("SELECT id FROM users WHERE username LIKE ?", (f"%{MARK}%",)))
    )
    print("cleanup remaining =", remaining)


def main() -> int:
    pre = "--pre" in sys.argv
    suffix = uuid.uuid4().hex[:6]
    uname = f"{MARK}user{suffix}"
    title = f"{MARK}季度报告{suffix}"
    content = f"{MARK} 完全一致的正文内容 {suffix}"
    uid_param = f"00000000-0000-4000-8000-000000{suffix.zfill(6)}"

    # ── 登录面：dedup 端点 require Bearer（get_current_user）──
    st, body = call("POST", "/api/user/register", {"username": uname, "email": f"{uname}@e2e.local", "password": "e2e-pass-1"})
    check(st == 200 or st == 201, "register probe user", f"{st} {body[:120]}")
    st, body = call("POST", "/api/user/login", {"username": uname, "password": "e2e-pass-1"}, form=True)
    check(st == 200, "login probe user", f"{st} {body[:120]}")
    token = json.loads(body).get("access_token", "")
    uid = json.loads(body).get("user_id", uid_param)
    hdrs = {"Authorization": f"Bearer {token}"}

    ids: list[str] = []
    try:
        # ── 建 2 条同文知识（sim=1.0≥0.85）+1 条无关行；created_at 差 1s 保序确定 ──
        st, body = call("POST", f"/api/knowledge/?user_id={uid}", {"title": title, "content": content})
        check(st == 200, "create kept row", f"{st} {body[:120]}")
        kept_id = json.loads(body).get("id", "")
        ids.append(kept_id)
        time.sleep(1.1)
        st, body = call("POST", f"/api/knowledge/?user_id={uid}", {"title": title, "content": content})
        check(st == 200, "create dup row", f"{st} {body[:120]}")
        dup_id = json.loads(body).get("id", "")
        ids.append(dup_id)
        st, body = call("POST", f"/api/knowledge/?user_id={uid}", {"title": f"{MARK}无关主题{suffix}", "content": f"{MARK} 另一段完全不同的文本内容 {suffix}"})
        check(st == 200, "create unrelated row", f"{st} {body[:120]}")
        other_id = json.loads(body).get("id", "")
        ids.append(other_id)

        # ── 前置：全文检索双命中（meili 索引真在场）──
        time.sleep(1.0)
        hits = fulltext_ids(title)
        check(kept_id in hits and dup_id in hits, "precondition: fulltext sees both dupes", f"hits={hits}")

        # ── 去重执行（scope=探针用户，禁全库去重误伤他人行）──
        st, body = call("POST", f"/api/dedup/deduplicate?user_id={uid}", {}, headers=hdrs)
        ok = st == 200
        data = json.loads(body) if ok else {}
        check(ok, "dedup endpoint 200", f"{st} {body[:150]}")
        check(data.get("removed_ids") == [dup_id], "dedup removed exactly the later dup", f"removed_ids={data.get('removed_ids')}")

        # ── DB 级联：dup 三表全清、保留行不动 ──
        kn = [r["id"] for r in q("SELECT id FROM knowledge WHERE title LIKE ?", (f"%{MARK}%",))]
        check(dup_id not in kn and kept_id in kn and other_id in kn, "db rows: dup gone, kept+other intact", f"rows={kn}")
        ch = [r["knowledge_id"] for r in q("SELECT DISTINCT knowledge_id FROM knowledge_chunks WHERE knowledge_id IN (?,?,?)", (kept_id, dup_id, other_id))]
        check(dup_id not in ch, "db chunks cascade: dup chunks gone", f"chunk owners={ch}")

        # ── 检索面（本拍收口面）：去重删掉的条目不得再被命中（幽灵面）──
        # meili 删除=异步任务（delete_document 入队即回，任务应用前短暂可见是
        # 主删除面同款既有语义非幽灵）——轮询等任务应用；修前=永不消失真幽灵。
        hits2 = []
        for _ in range(12):
            hits2 = fulltext_ids(title)
            if dup_id not in hits2:
                break
            time.sleep(0.5)
        check(dup_id not in hits2, "search face: dedup-removed entry NOT findable (no ghost)", f"hits={hits2}")
        check(kept_id in hits2, "search face: kept entry still findable", f"hits={hits2}")
        check(not meili_doc_exists(dup_id), "meili doc-level: dup doc deleted", f"dup_id={dup_id}")
        check(meili_doc_exists(kept_id), "meili doc-level: kept doc intact", f"kept_id={kept_id}")

        # ── 无关行零触碰 ──
        check(other_id in fulltext_ids(f"{MARK}无关主题{suffix}"), "unrelated row untouched", "")
    finally:
        cleanup(ids, uid, token)

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ==")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

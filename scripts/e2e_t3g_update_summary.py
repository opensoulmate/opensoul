#!/usr/bin/env python3
"""T3-⑦ live e2e 探针：update 摘要版本号双调收口（真服务 :8090 全链证据）。

跑法：python3 scripts/e2e_t3g_update_summary.py（服务 :8090 已部署本拍代码）。
检查：
1) /api/vein/health 200
2) 部署符号面：摘要版本号单次读取在场（store.get_version_history(file_id, limit=1
   恰 1 处）+ 修前双调口径（条件表达式重复形态）缺席 + version_label 在场
3) 真上传 + 3 次真更新 → timeline file_updated 摘要恰 (v1)(v2)(v3)（文案与修前
   逐字节同格式——修前历史产物同 '(vN)' 形）
4) 同内容更新 → '(v?)'（无版本行分支契约零变化）
5) 响应契约：PUT version_count / GET current_version 同源一致
6) 清痕：DELETE 文件 + 精确删本次探针 timeline 行 remaining=0
"""

import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARK = "e2e-t3g-" + str(int(time.time()))[-6:]
NAME = MARK + ".txt"
RESULTS = []

REPO = Path(__file__).resolve().parents[1]
TL_DB = REPO / "data" / "timeline.db"
SRC = REPO / "src" / "api" / "vein.py"


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""))


def req(method, path, raw_body=None, content_type=None, fields=None):
    url = BASE + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body
        headers["Content-Type"] = content_type or "application/octet-stream"
    elif fields is not None:
        boundary = "----e2e" + uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            if isinstance(v, tuple):
                fname, content, mime = v
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                    f'filename="{fname}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
                    + content
                    + b"\r\n"
                )
            else:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                )
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(method, path, **kw):
    code, data = req(method, path, **kw)
    try:
        return code, json.loads(data)
    except Exception:
        return code, {}


def tl_summaries(name):
    con = sqlite3.connect(TL_DB)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            "SELECT event_id, summary FROM events WHERE event_type='file_updated' "
            "AND summary LIKE ? ORDER BY timestamp, event_id",
            (f"%{name}%",),
        )
    ]
    con.close()
    return rows


def main():
    # 1) health
    st, _ = req("GET", "/api/vein/health")
    check("vein health 200", st == 200, f"st={st}")

    # 2) 部署符号面
    src = SRC.read_text(encoding="utf-8")
    single = src.count("store.get_version_history(file_id, limit=1")
    old_double = src.count("if store.get_version_history(file_id, limit=1)")
    check("符号面：摘要读取单次口径在场（恰 1 处）", single == 1, f"count={single}")
    check("符号面：修前双调口径缺席", old_double == 0, f"count={old_double}")
    check("符号面：version_label 接线在场", "version_label" in src)

    # 3) 真上传 + 3 次真更新
    st, body = jreq("POST", "/api/vein/upload", fields={"tags": "", "file": (NAME, b"v0", "text/plain")})
    check("真上传 200", st == 200, f"st={st}")
    fid = body.get("file_id", "")
    labels = []
    for i in (1, 2, 3):
        st, body = jreq(
            "PUT",
            f"/api/vein/files/{fid}/content",
            fields={"change_summary": "", "file": (NAME, f"v{i}".encode(), "text/plain")},
        )
        check(f"真更新 v{i} 200", st == 200, f"st={st}")
        labels.append(body.get("version_count"))
    rows = tl_summaries(NAME)
    want = [f"📝 File updated: {NAME} (v{i})" for i in (1, 2, 3)]
    got = [r["summary"] for r in rows]
    check("timeline 摘要恰 (v1)(v2)(v3) 文案契约", got == want, f"got={got}")
    check("version_count 序 1/2/3", labels == [1, 2, 3], f"got={labels}")

    # 4) 同内容更新：两分支契约（过程自纠：首版探针把「已有版本行+同内容」误当
    #    「无版本行」——历史存在时摘要=最新版标签 (v3) 才是真契约，拆两例分别断言）
    st, body = jreq(
        "PUT",
        f"/api/vein/files/{fid}/content",
        fields={"change_summary": "", "file": (NAME, b"v3", "text/plain")},
    )
    check("同内容更新（已有版本行）200", st == 200, f"st={st}")
    check("同内容 version_count 仍 3（不虚增既有设计）", body.get("version_count") == 3, f"got={body.get('version_count')}")
    rows = tl_summaries(NAME)
    got = [r["summary"] for r in rows]
    check(
        "同内容摘要=最新版标签 (v3)（不 fabricate 新版本号）",
        got == want + [f"📝 File updated: {NAME} (v3)"],
        f"got={got}",
    )

    # 4b) 无版本行分支：新文件同内容首更（首传 0 行）→ '(v?)'
    name_b = MARK + "-b.txt"
    st, body = jreq("POST", "/api/vein/upload", fields={"tags": "", "file": (name_b, b"same", "text/plain")})
    check("b 真上传 200", st == 200, f"st={st}")
    fid_b = body.get("file_id", "")
    st, body = jreq(
        "PUT",
        f"/api/vein/files/{fid_b}/content",
        fields={"change_summary": "", "file": (name_b, b"same", "text/plain")},
    )
    check("b 同内容首更 200", st == 200, f"st={st}")
    check("b version_count==0（首传 0 行既有设计）", body.get("version_count") == 0, f"got={body.get('version_count')}")
    got = [r["summary"] for r in tl_summaries(name_b)]
    check("b 同内容摘要 (v?)（无版本行分支契约）", got == [f"📝 File updated: {name_b} (v?)"], f"got={got}")

    # 5) GET /versions 同源一致
    st, body = jreq("GET", f"/api/vein/files/{fid}/versions")
    check("GET /versions 200", st == 200, f"st={st}")
    check("current_version==3 同源一致", body.get("current_version") == 3, f"got={body.get('current_version')}")

    # 6) 清痕
    st, _ = req("DELETE", f"/api/vein/files/{fid}")
    check("DELETE 文件 200", st == 200, f"st={st}")
    st, _ = req("GET", f"/api/vein/files/{fid}/download")
    check("删除后 download 404", st == 404, f"st={st}")
    st, _ = req("DELETE", f"/api/vein/files/{fid_b}")
    check("DELETE b 文件 200", st == 200, f"st={st}")
    con = sqlite3.connect(TL_DB)
    con.execute("DELETE FROM events WHERE summary LIKE ?", (f"%{MARK}%",))
    con.commit()
    con.execute("VACUUM")
    con.close()
    left = [r for r in tl_summaries(MARK)]
    check("清痕 timeline remaining=0（MARK 全清）", left == [], f"left={len(left)}")

    ok = all(r[1] for r in RESULTS)
    print(f"\n{'ALL PASS' if ok else 'HAS FAIL'} {sum(r[1] for r in RESULTS)}/{len(RESULTS)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

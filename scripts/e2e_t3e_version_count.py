"""T3-⑤ live e2e——vein update 响应 version_count 真跑 count 语义（不被 limit 截断）。

探针（urllib 真栈打 live :8090）：
1) /api/vein/health 200
2) 部署模块符号面：file_store.get_version_count 在场 + api/vein.py 面
   len(store.get_version_history 修前截断口径缺席
3) 真上传 + 55 次真内容变更 → 末次 update 响应 version_count==55
   （修前 len(get_history) 口径=50 虚低——探针内 GET /versions?limit=50 回 50 行
   对照，计数面与列表面分界可见）
4) 小计数契约零变化（首变更=1、二次=2）
5) 清痕 remaining=0
"""

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"


def _req(method, path, data=None, headers=None, raw=None, ctype=None):
    url = BASE + path
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(url, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _multipart(field, filename, content):
    boundary = "----e2et3e" + uuid.uuid4().hex
    parts = [f"--{boundary}\r\n".encode()]
    parts.append(
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main():
    ok = 0
    total = 0

    def check(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
            print(f"  PASS {name}")
        else:
            print(f"  FAIL {name} {detail}")

    # 1) health
    st, _body = _req("GET", "/api/vein/health")
    check("vein health 200", st == 200, f"st={st}")

    # 2) 部署模块符号面（服务加载同一 src 文件）
    sys.path.insert(0, ".")
    import src.vein.file_store as fs_mod

    names = {n for n in dir(fs_mod.FileStore) if not n.startswith("__")}
    check("接线面 file_store.get_version_count 在场", "get_version_count" in names, str(names))
    src_text = open("src/api/vein.py", encoding="utf-8").read()
    check(
        "api/vein.py 修前截断口径 len(store.get_version_history 缺席",
        "len(store.get_version_history" not in src_text,
    )
    check(
        "api/vein.py 接线消费点在场",
        "store.get_version_count(file_id" in src_text,
    )

    # 3) 真上传 + 55 次真内容变更
    token = "t3e-e2e-" + uuid.uuid4().hex[:8]
    raw, ctype = _multipart("file", f"{token}.txt", b"v0 content\n")
    st, body = _req("POST", "/api/vein/upload", raw=raw, ctype=ctype)
    check("上传 200", st == 200, f"st={st} body={body[:120]}")
    up = json.loads(body)
    file_id = up.get("file_id") or (up.get("file") or {}).get("file_id")
    check("file_id 拿到", bool(file_id), str(up)[:120])

    last = None
    for i in range(1, 56):
        raw_i, ctype_i = _multipart("file", f"{token}.txt", f"v{i} content updated\n".encode())
        st, body = _req("PUT", f"/api/vein/files/{file_id}/content", raw=raw_i, ctype=ctype_i)
        if st != 200:
            check(f"第 {i} 次更新 200", False, f"st={st} body={body[:120]}")
            break
        last = json.loads(body)
    else:
        check("55 次更新全 200", True)

    check(
        "末次 update 响应 version_count==55（count 语义不被截断）",
        last is not None and last.get("version_count") == 55,
        f"got={last.get('version_count') if last else None}",
    )

    st, body = _req("GET", f"/api/vein/files/{file_id}/versions?limit=50")
    hist = json.loads(body).get("versions", [])
    check(
        "列表面 limit=50 回 50 行（修前 len 口径即此=虚低对照）",
        len(hist) == 50,
        f"len={len(hist)}",
    )

    # 4) 小计数契约零变化
    token2 = "t3e-e2e-" + uuid.uuid4().hex[:8]
    raw2, ctype2 = _multipart("file", f"{token2}.txt", b"v0\n")
    st, body = _req("POST", "/api/vein/upload", raw=raw2, ctype=ctype2)
    up2 = json.loads(body)
    fid2 = up2.get("file_id")
    raw3, ctype3 = _multipart("file", f"{token2}.txt", b"v1\n")
    st, body = _req("PUT", f"/api/vein/files/{fid2}/content", raw=raw3, ctype=ctype3)
    c1 = json.loads(body).get("version_count")
    raw4, ctype4 = _multipart("file", f"{token2}.txt", b"v2\n")
    st, body = _req("PUT", f"/api/vein/files/{fid2}/content", raw=raw4, ctype=ctype4)
    c2 = json.loads(body).get("version_count")
    check("小计数契约 首变更=1/二次=2", c1 == 1 and c2 == 2, f"c1={c1} c2={c2}")

    # 5) 清痕
    for fid in (file_id, fid2):
        st, _body = _req("DELETE", f"/api/vein/files/{fid}")
        check(f"清痕 {fid[:8]} 200", st == 200, f"st={st}")
        st, _body = _req("GET", f"/api/vein/files/{fid}")
        check(f"清痕 {fid[:8]} remaining=0（404）", st == 404, f"st={st}")

    print(f"\n== {ok}/{total} ==")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())

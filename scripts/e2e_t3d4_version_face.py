"""T3-④ live e2e——vein 版本面活面零回归 + 被删 get_latest_version 部署缺席。

探针（httpx/urllib 真栈打 live :8090）：
1) /api/vein/health 200
2) openapi 版本面路由在场（versions / versions/{n}）
3) 部署模块（服务加载的同一文件）：get_latest_version 缺席、覆盖活面
   get_history/get_version_count 在场
4) 真上传→真更新→版本史 2 版、最新版语义经活面 GET history 首行可达
   （被删方法同款语义：ORDER BY version_number DESC LIMIT 1 == history[0]）
5) 单版本读面 200 + update 响应 version_count 键真跑
6) 清痕 remaining=0
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
    boundary = "----e2et3d4" + uuid.uuid4().hex
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
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
    st, body = _req("GET", "/api/vein/health")
    check("vein health 200", st == 200, f"st={st}")

    # 2) openapi 路由在场
    st, body = _req("GET", "/openapi.json")
    paths = json.loads(body)["paths"]
    check(
        "版本面路由在场",
        "/api/vein/files/{file_id}/versions" in paths
        and "/api/vein/files/{file_id}/versions/{version_number}" in paths,
    )

    # 3) 部署模块符号面（服务加载同一 src 文件）
    sys.path.insert(0, ".")
    import src.vein.versioning as vm

    names = {n for n in dir(vm.VersionManager) if not n.startswith("__")}
    check("get_latest_version 部署缺席", "get_latest_version" not in names, str(names))
    check("覆盖活面 get_history 在场", "get_history" in names)
    check("留口件 get_version_count 在场", "get_version_count" in names)

    # 4) 真上传→真更新→版本史
    token = "t3d4-e2e-" + uuid.uuid4().hex[:8]
    raw, ctype = _multipart("file", f"{token}.txt", b"v1 content\n")
    st, body = _req("POST", "/api/vein/upload", raw=raw, ctype=ctype)
    check("上传 200", st == 200, f"st={st} body={body[:120]}")
    up = json.loads(body)
    file_id = up.get("file_id") or (up.get("file") or {}).get("file_id")
    check("file_id 拿到", bool(file_id), str(up)[:120])

    raw2, ctype2 = _multipart("file", f"{token}.txt", b"v2 content updated\n")
    st, body = _req("PUT", f"/api/vein/files/{file_id}/content", raw=raw2, ctype=ctype2)
    check("更新 200", st == 200, f"st={st} body={body[:120]}")
    # 既有设计如实：首次上传不记版本行、内容变更才记（探针首版期望 2 版=自伤，修期望）
    upd = json.loads(body)
    check(
        "update 响应 version_count 真跑（首变更=1 版）",
        upd.get("version_count") == 1,
        f"got={upd.get('version_count')}",
    )

    raw3, ctype3 = _multipart("file", f"{token}.txt", b"v3 content updated again\n")
    st, body = _req("PUT", f"/api/vein/files/{file_id}/content", raw=raw3, ctype=ctype3)
    check("二次更新 200", st == 200, f"st={st} body={body[:120]}")
    upd2 = json.loads(body)
    check(
        "update 响应 version_count==2",
        upd2.get("version_count") == 2,
        f"got={upd2.get('version_count')}",
    )

    st, body = _req("GET", f"/api/vein/files/{file_id}/versions")
    hist = json.loads(body).get("versions", [])
    check("版本史 2 版", len(hist) == 2, f"len={len(hist)}")
    check(
        "最新版语义经活面 history 首行（被删方法同款）",
        bool(hist) and hist[0].get("version_number") == 2,
        str(hist[:1]),
    )

    # 5) 单版本读面
    st, body = _req("GET", f"/api/vein/files/{file_id}/versions/1")
    v1 = json.loads(body)
    v1n = v1.get("version_number", (v1.get("version") or {}).get("version_number"))
    check("单版本读面 v1", st == 200 and v1n == 1, f"st={st} body={body[:120]}")

    # 6) 清痕
    st, body = _req("DELETE", f"/api/vein/files/{file_id}")
    check("清痕 200", st == 200, f"st={st}")
    st, body = _req("GET", f"/api/vein/files/{file_id}")
    check("清痕 remaining=0（404）", st == 404, f"st={st}")

    print(f"\n== {ok}/{total} ==")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())

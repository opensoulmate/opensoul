"""T3-⑥ live e2e——GET /versions 响应 current_version 真跑 count 语义（不被 limit 截断）。

探针（urllib 真栈打 live :8090）：
1) /api/vein/health 200
2) 部署符号面：api/vein.py current_version 走 get_version_count（接线在场）
   + 修前截断口径 len(versions) 缺席
3) 真上传 + 55 次真内容变更 → GET /versions：列表面 50 行（limit 语义既有）
   + current_version==55（修前 len 口径=50 虚低）
4) 小契约零变化（首传=0、首变更=1、二次=2）+ 同源一致（==末次 version_count）
5) ?limit=1 计数面不随 limit（列表 1 行、current_version 全量）
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
    boundary = "----e2et3f" + uuid.uuid4().hex
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

    # 2) 部署符号面（服务加载同一 src 文件）
    src_text = open("src/api/vein.py", encoding="utf-8").read()
    check(
        "api/vein.py current_version 接线 get_version_count 在场",
        '"current_version": store.get_version_count(' in src_text,
    )
    check(
        "api/vein.py 修前截断口径 current_version len(versions) 缺席",
        '"current_version": len(' not in src_text,
    )

    # 3) 真上传 + 55 次真内容变更
    token = "t3f-e2e-" + uuid.uuid4().hex[:8]
    raw, ctype = _multipart("file", f"{token}.txt", b"v0 content\n")
    st, body = _req("POST", "/api/vein/upload", raw=raw, ctype=ctype)
    check("上传 200", st == 200, f"st={st} body={body[:120]}")
    up = json.loads(body)
    file_id = up.get("file_id")
    check("file_id 拿到", bool(file_id), str(up)[:120])
    check("首传 current_version==0 契约", _cv(file_id) == 0, f"got={_cv(file_id)}")

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

    st, body = _req("GET", f"/api/vein/files/{file_id}/versions")
    data = json.loads(body)
    check(
        "列表面缺省 limit 回 50 行（截断只在列表面=limit 语义既有）",
        len(data.get("versions", [])) == 50,
        f"len={len(data.get('versions', []))}",
    )
    check(
        "current_version==55（count 语义不被截断，修前 len 口径=50 虚低）",
        data.get("current_version") == 55,
        f"got={data.get('current_version')}",
    )
    check(
        "同源一致 current_version==末次 version_count",
        last is not None and data.get("current_version") == last.get("version_count"),
        f"cv={data.get('current_version')} vc={last.get('version_count') if last else None}",
    )

    # 5) ?limit=1 计数面不随 limit
    st, body = _req("GET", f"/api/vein/files/{file_id}/versions?limit=1")
    d1 = json.loads(body)
    check(
        "?limit=1 列表恰 1 行 + current_version 仍 55",
        len(d1.get("versions", [])) == 1 and d1.get("current_version") == 55,
        f"len={len(d1.get('versions', []))} cv={d1.get('current_version')}",
    )

    # 4) 小契约零变化
    token2 = "t3f-e2e-" + uuid.uuid4().hex[:8]
    raw2, ctype2 = _multipart("file", f"{token2}.txt", b"v0\n")
    st, body = _req("POST", "/api/vein/upload", raw=raw2, ctype=ctype2)
    fid2 = json.loads(body).get("file_id")
    check("小契约 首传=0", _cv(fid2) == 0, f"got={_cv(fid2)}")
    raw3, ctype3 = _multipart("file", f"{token2}.txt", b"v1\n")
    _req("PUT", f"/api/vein/files/{fid2}/content", raw=raw3, ctype=ctype3)
    check("小契约 首变更=1", _cv(fid2) == 1, f"got={_cv(fid2)}")
    raw4, ctype4 = _multipart("file", f"{token2}.txt", b"v2\n")
    _req("PUT", f"/api/vein/files/{fid2}/content", raw=raw4, ctype=ctype4)
    check("小契约 二次=2", _cv(fid2) == 2, f"got={_cv(fid2)}")

    # 6) 清痕
    for fid in (file_id, fid2):
        st, _body = _req("DELETE", f"/api/vein/files/{fid}")
        check(f"清痕 {fid[:8]} 200", st == 200, f"st={st}")
        st, _body = _req("GET", f"/api/vein/files/{fid}")
        check(f"清痕 {fid[:8]} remaining=0（404）", st == 404, f"st={st}")

    print(f"\n== {ok}/{total} ==")
    return 0 if ok == total else 1


def _cv(file_id):
    st, body = _req("GET", f"/api/vein/files/{file_id}/versions")
    return json.loads(body).get("current_version")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""H1-②b-⑫ KeyManager 有效性验证面 e2e 探针（live :8090 真栈）。

用法：
    python3 scripts/e2e_key_validate_face.py --pre    # 修前取证（缺口硬证）
    python3 scripts/e2e_key_validate_face.py --post   # 修后全链

真 socket 桩（http.server 线程）作 provider base_url，服务真打 /models；
Authorization 头回读=真钥真送达。取证后清痕：删测试 key + 测试 provider，
remaining=0（绝不碰生产 env 密钥/既有 provider）。
"""

import json
import sys
import threading
import http.server
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
PROV = "e2e-val-prov"
PROV_EMPTY = "e2e-val-empty"
KEY = "e2e-key-abcdef123456"

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + str(detail) if detail and not cond else ""))


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


# ── 真 socket 桩 ────────────────────────────────────────────────


class _StubServer(http.server.ThreadingHTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        self.status_code = 200


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = getattr(self, "server")
        getattr(srv, "requests").append((self.path, self.headers.get("Authorization")))
        code = getattr(srv, "status_code", 200)
        body = b'{"data": []}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def cleanup():
    req("DELETE", "/api/gland/keys/{}/0".format(PROV))
    req("DELETE", "/api/gland/providers/{}".format(PROV))
    req("DELETE", "/api/gland/providers/{}".format(PROV_EMPTY))
    _, keys = req("GET", "/api/gland/keys")
    _, provs = req("GET", "/api/gland/providers")
    # 计数口径=真行数（GET /keys 既有契约：删空的 provider 仍以空列表留在键集，
    # 按键存在计数会误报残留）
    remaining = sum(
        len(rows) for name, rows in (keys or {}).items() if name in (PROV, PROV_EMPTY)
    )
    remaining += sum(
        1 for p in (provs or {}).get("providers", []) if p.get("name") in (PROV, PROV_EMPTY)
    )
    print("cleanup remaining={}".format(remaining))
    return remaining


def run(mode):
    stub = _StubServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    host, port = stub.server_address
    stub_url = "http://{}:{}".format(host, port)

    # 布置：注册 provider + 加钥（修前/修后同布置）
    s, _ = req("POST", "/api/gland/providers", {"name": PROV, "base_url": stub_url})
    check("setup provider 200", s == 200, s)
    s, _ = req("POST", "/api/gland/keys", {"provider": PROV, "api_key": KEY})
    check("setup key 200", s == 200, s)
    s, _ = req("POST", "/api/gland/providers", {"name": PROV_EMPTY, "base_url": stub_url})
    check("setup empty provider 200", s == 200, s)

    if mode == "--pre":
        # 修前：验证面端点缺席（404/405 皆缺口形态）
        s, d = req("POST", "/api/gland/keys/{}/validate".format(PROV))
        check("PRE validate-all 端点缺席", s in (404, 405), "status={} detail={}".format(s, d))
        s, d = req("POST", "/api/gland/keys/{}/0/validate".format(PROV))
        check("PRE validate-one 端点缺席", s in (404, 405), "status={} detail={}".format(s, d))
        check("PRE 槽位未被验证（last_checked=0）", _last_checked() == 0.0, _last_checked())
        cleanup()
        stub.shutdown()
        return

    # ── 修后全链 ──
    s, d = req("POST", "/api/gland/keys/{}/validate".format(PROV))
    check("validate-all 200", s == 200, (s, d))
    check("validate-all 顶层恰 4 键", set(d.keys()) == {"provider", "total", "valid", "results"}, d)
    check("validate-all total=1 valid=1", d.get("total") == 1 and d.get("valid") == 1, d)
    row = (d.get("results") or [{}])[0]
    check("validate-all 行 ok=True 不谎报", row.get("ok") is True, row)
    check("validate-all 行恰 6 键", set(row.keys()) == {"index", "ok", "masked_key", "is_valid", "fail_count", "last_checked"}, row)
    check("掩码不含明文", KEY not in json.dumps(d) and row.get("masked_key") == "e2e-...3456", row)
    check("真钥真送达（Authorization 头）", ("/models", "Bearer " + KEY) in stub.requests, stub.requests)

    s, d = req("POST", "/api/gland/keys/{}/0/validate".format(PROV))
    check("validate-one 200", s == 200, (s, d))
    check("validate-one 恰 7 键", set(d.keys()) == {"provider", "index", "ok", "masked_key", "is_valid", "fail_count", "last_checked"}, d)
    check("validate-one ok=True", d.get("ok") is True and d.get("index") == 0, d)

    # 不谎报 ok：桩改 401
    stub.status_code = 401
    s, d = req("POST", "/api/gland/keys/{}/0/validate".format(PROV))
    check("401 桩 ok=False 不谎报", s == 200 and d.get("ok") is False and d.get("is_valid") is False, d)
    stub.status_code = 200

    # 404 电池 + 零副作用
    before = len(stub.requests)
    for path in (
        "/api/gland/keys/{}/5/validate".format(PROV),
        "/api/gland/keys/{}/-1/validate".format(PROV),
        "/api/gland/keys/e2e-val-nope/0/validate",
    ):
        s, d = req("POST", path)
        check("miss 404 " + path, s == 404 and "Key not found" in json.dumps(d), (s, d))
    check("404 零网络触达", len(stub.requests) == before, (before, len(stub.requests)))

    # 未注册 provider（无 base_url 真源）
    req("POST", "/api/gland/keys", {"provider": "e2e-val-orphan", "api_key": "orphan-key-xyz"})
    s, d = req("POST", "/api/gland/keys/e2e-val-orphan/0/validate")
    check("未注册 provider 404 带 base_url 真因", s == 404 and "base_url" in json.dumps(d), (s, d))
    req("DELETE", "/api/gland/keys/e2e-val-orphan/0")

    # 零钥 provider：total 0 如实
    s, d = req("POST", "/api/gland/keys/{}/validate".format(PROV_EMPTY))
    check("零钥 total=0 如实", s == 200 and d.get("total") == 0 and d.get("results") == [], (s, d))

    # 既有面零变化
    s, d = req("GET", "/api/gland/keys")
    rows = (d or {}).get(PROV, [])
    check("GET /keys 行形状不变（4 键）", rows and set(rows[0].keys()) == {"masked_key", "is_valid", "fail_count", "last_checked"}, rows)

    remaining = cleanup()
    check("清痕 remaining=0", remaining == 0, remaining)
    stub.shutdown()


def _last_checked():
    _, d = req("GET", "/api/gland/keys")
    rows = (d or {}).get(PROV, [])
    return rows[0]["last_checked"] if rows else None


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    try:
        run(mode)
    finally:
        pass
    print("\n{}: {} passed, {} failed".format(mode, len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
        sys.exit(1)

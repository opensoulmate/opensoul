#!/usr/bin/env python3
"""GLAND-③ PATCH provider 就地改面 live e2e 探针（--pre / --post 双向硬证）。

--pre  = 服务仍载修前代码：断言修前缺口（PATCH 路由缺席 404=无就地改通道）。
--post = 服务载新码：断言修后语义（就地改/持钥改道 409/空载荷 400/未知字段 422）。

全程只碰自建 e2e-patch-prov-* provider，取证后 DELETE 级联清痕（residue 断言归零）。
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
NAME = "e2e-patch-prov"
KEY = "gl-e2e-patch-key-11111111"

results: list[tuple[str, bool, str]] = []


def call(method, path, payload=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def check(label, ok, note=""):
    results.append((label, bool(ok), note))
    print(("PASS " if ok else "FAIL ") + label + (" — " + note if note else ""))


def provider_row(status, body):
    return [p for p in body.get("providers", []) if p.get("name") == NAME]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    pre = mode == "--pre"
    print(f"=== e2e_provider_patch_face {mode} ===")

    # 清理可能的上轮残留
    call("DELETE", f"/api/gland/providers/{NAME}")

    # P1 注册（两种模式同契约）
    s, b = call(
        "POST",
        "/api/gland/providers",
        {"name": NAME, "base_url": "https://a.example/v1", "api_key": KEY, "priority": 0},
    )
    check("P1 create 200", s == 200, f"{s} {b}")

    # P2 PATCH priority（就地改核心面）
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {"priority": 7})
    if pre:
        check("P2 修前 PATCH 无 handler=405 缺口硬证", s == 405, f"{s} {b}")
    else:
        check(
            "P2 PATCH priority 200 契约恰 4 键",
            s == 200
            and set(b) == {"status", "provider", "updated", "keys_untouched"}
            and b["updated"] == ["priority"]
            and b["keys_untouched"] == 1,
            f"{s} {b}",
        )

    # P3 GET 反读：priority 真改/修前改不动
    s, b = call("GET", "/api/gland/providers")
    row = provider_row(s, b)
    want = 0 if pre else 7
    check(
        f"P3 GET 反读 priority=={want}",
        bool(row) and row[0].get("priority") == want,
        f"{row}",
    )

    # P4 钥一字不动（两种模式同断言）
    s, b = call("GET", "/api/gland/keys")
    slots = b.get("keys", {}).get(NAME, []) if isinstance(b.get("keys"), dict) else []
    if not slots:
        slots = [v for k, v in b.items() if k == NAME] if isinstance(b, dict) else []
        slots = slots[0] if slots else []
    check("P4 钥仍在（就地改不触钥）", len(slots) == 1, f"slots={len(slots)}")

    # P5 持钥改 base_url（凭据端点绑定）
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {"base_url": "https://evil.example/v1", "priority": 9})
    if pre:
        check("P5 修前 PATCH 无 handler 405", s == 405, f"{s} {b}")
    else:
        detail = str(b.get("detail", ""))
        check(
            "P5 持钥改道 409 带真因",
            s == 409 and "base_url" in detail and "key" in detail,
            f"{s} {detail[:80]}",
        )
        s2, b2 = call("GET", "/api/gland/providers")
        row2 = provider_row(s2, b2)
        check(
            "P5b 原子拒零写入（base_url/priority 一字不动）",
            bool(row2)
            and row2[0].get("base_url") == "https://a.example/v1"
            and row2[0].get("priority") == 7,
            f"{row2}",
        )

    # P6 同值 base_url = 非改道放行
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {"base_url": "https://a.example/v1/"})
    if pre:
        check("P6 修前 PATCH 无 handler 405", s == 405, f"{s}")
    else:
        check("P6 同值改道放行 200", s == 200, f"{s} {b}")

    # P7 空载荷
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {})
    if pre:
        check("P7 修前 PATCH 无 handler 405", s == 405, f"{s}")
    else:
        check("P7 空载荷 400 禁空转假成功", s == 400 and "No updatable fields" in str(b.get("detail", "")), f"{s} {b}")

    # P8 未知字段 name（身份不可改）
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {"name": "renamed"})
    if pre:
        check("P8 修前 PATCH 无 handler 405", s == 405, f"{s}")
    else:
        check("P8 未知字段 422 禁静默忽略", s == 422, f"{s} {b}")

    # P9 移钥后改道放行（凭据端点绑定正路径）
    s, b = call("DELETE", "/api/gland/keys/%s/0" % NAME)
    check("P9a 移钥 200", s == 200, f"{s} {b}")
    s, b = call("PATCH", f"/api/gland/providers/{NAME}", {"base_url": "https://b.example/v1"})
    if pre:
        check("P9b 修前 PATCH 无 handler 405", s == 405, f"{s}")
    else:
        check("P9b 无钥改道 200", s == 200, f"{s} {b}")

    # 清痕
    s, b = call("DELETE", f"/api/gland/providers/{NAME}")
    check("P10 清痕 DELETE 200", s == 200, f"{s} {b}")
    s, b = call("GET", "/api/gland/providers")
    residue_prov = len(provider_row(s, b))
    s, b = call("GET", "/api/gland/keys")
    keys = b.get("keys", {}) if isinstance(b.get("keys"), dict) else b
    residue_key = len(keys.get(NAME, [])) if isinstance(keys, dict) else 0
    check("P10b residue 归零", residue_prov == 0 and residue_key == 0,
          f"prov={residue_prov} key={residue_key}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"=== {mode} {passed}/{total} ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

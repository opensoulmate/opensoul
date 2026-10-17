#!/usr/bin/env python3
"""GLAND-① remove_provider 级联清钥 live e2e 探针（修前/修后同探针双向硬证）。

只用 gl1e2e- 前缀的临时 provider/key，绝不触碰生产 provider（openai/ollama/mimo 等）；
取证后清理全部临时面（新语义=级联清钥自动归零，旧语义=逐钥单删兜底清痕）。

用法：python3 scripts/e2e_gland_provider_key_cascade.py [--base http://localhost:8090]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
RESULTS: list[tuple[str, bool, str]] = []


def _req(method: str, path: str, payload: dict | None = None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, (json.loads(body) if body else None)
        except Exception:
            return exc.code, {"raw": body}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def keys_of(provider: str) -> list:
    _status, body = _req("GET", "/api/gland/keys")
    val = (body or {}).get(provider, [])
    return val if isinstance(val, list) else []


def sweep_keys(provider: str) -> int:
    """旧语义兜底清痕：逐钥单删面清掉 provider 残钥（新语义下应已是 0）。"""
    removed = 0
    while keys_of(provider):
        _status, _body = _req("DELETE", f"/api/gland/keys/{provider}/0")
        removed += 1
        if removed > 20:
            break
    return removed


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    ts = str(int(time.time()))[-7:]

    prov_main = f"gl1e2e-main-{ts}"
    prov_ghost = f"gl1e2e-ghost-{ts}"
    prov_zero = f"gl1e2e-zero-{ts}"
    prov_keep = f"gl1e2e-keep-{ts}"
    prov_drop = f"gl1e2e-drop-{ts}"

    try:
        # P1 注册带钥 provider → 钥真在
        st, _ = _req("POST", "/api/gland/providers", {
            "name": prov_main, "base_url": "https://gl1e2e.example/v1",
            "api_key": f"sk-gl1e2e-{ts}-1",
        })
        check("P1_register_with_key", st == 200 and len(keys_of(prov_main)) == 1, f"st={st}")

        # P2 删除 provider → 响应契约（新 {status,provider,keys_removed} / 旧 {status}）
        st, body = _req("DELETE", f"/api/gland/providers/{prov_main}")
        shape = sorted((body or {}).keys())
        check("P2_delete_200", st == 200, f"st={st} shape={shape}")
        check("P2_keys_removed_key", shape == ["keys_removed", "provider", "status"]
              and (body or {}).get("keys_removed") == 1, f"body={body}")

        # P3 钥随删（孤钥归零）——旧语义此处 keys 仍 1=孤钥实证
        leftover = keys_of(prov_main)
        check("P3_keys_cascade_cleared", leftover == [], f"leftover={len(leftover)}")

        # P4 未命中 404 零副作用（同名钥绝不被变相清掉）
        st, _ = _req("POST", "/api/gland/keys", {"provider": prov_ghost, "api_key": f"sk-gl1e2e-{ts}-ghost"})
        st2, body2 = _req("DELETE", f"/api/gland/providers/{prov_ghost}")
        check("P4_unknown_404", st == 200 and st2 == 404
              and (body2 or {}).get("detail") == f"Provider '{prov_ghost}' not found",
              f"st={st}/{st2}")
        check("P4_unknown_key_untouched", len(keys_of(prov_ghost)) == 1, f"keys={len(keys_of(prov_ghost))}")

        # P5 零钥 provider 删除 → 200 keys_removed=0（falsy 陷阱：0 不得变 404）
        st, _ = _req("POST", "/api/gland/providers", {"name": prov_zero, "base_url": "https://gl1e2e-zero.example/v1"})
        st2, body2 = _req("DELETE", f"/api/gland/providers/{prov_zero}")
        check("P5_zero_key_provider_200", st == 200 and st2 == 200, f"st={st}/{st2}")
        check("P5_keys_removed_zero", (body2 or {}).get("keys_removed") == 0, f"body={body2}")

        # P6 其余 provider 钥一行不动 + P7 单钥面零回归
        _req("POST", "/api/gland/providers", {"name": prov_keep, "base_url": "https://gl1e2e-keep.example/v1", "api_key": f"sk-gl1e2e-{ts}-keep"})
        _req("POST", "/api/gland/providers", {"name": prov_drop, "base_url": "https://gl1e2e-drop.example/v1", "api_key": f"sk-gl1e2e-{ts}-drop"})
        st, body = _req("DELETE", f"/api/gland/providers/{prov_drop}")
        check("P6_other_keys_survive", (body or {}).get("keys_removed") == 1
              and len(keys_of(prov_keep)) == 1 and not keys_of(prov_drop),
              f"body={body} keep={len(keys_of(prov_keep))} drop={len(keys_of(prov_drop))}")
        st, body = _req("DELETE", f"/api/gland/keys/{prov_keep}/0")
        check("P7_single_key_face_ok", st == 200 and (body or {}).get("remaining") == 0, f"st={st} body={body}")
    finally:
        # 清痕：删 provider + 兜底逐钥清（兼容旧语义孤钥）
        remaining = 0
        for p in (prov_main, prov_ghost, prov_zero, prov_keep, prov_drop):
            _req("DELETE", f"/api/gland/providers/{p}")
            remaining += sweep_keys(p)
        _st, keys = _req("GET", "/api/gland/keys")
        residue = {k: v for k, v in (keys or {}).items() if k.startswith("gl1e2e-") and v}
        print(f"cleanup: sweep_removed={remaining} residue={residue}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"== {passed}/{len(RESULTS)} ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

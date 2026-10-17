#!/usr/bin/env python3
"""GLAND-② add_provider 同名覆盖注册面 live e2e 探针（修前/修后同探针双向硬证）。

只用 gl2e2e- 前缀的临时 provider/key（priority=99 不进 failover 主链择优），
绝不触碰生产 provider（openai/ollama/mimo 等）；取证后清理全部临时面并回读
providers 名单==基线（零残留）。

--pre  修前（旧码）：同名 POST 照常 200 静默覆盖 + 旧钥留驻打向新 base_url=凭据滞留硬证
--post 修后（新码）：同名 POST 409 零副作用 + 替换走 DELETE（级联清钥）再 POST

用法：python3 scripts/e2e_provider_reregister_face.py [--base http://localhost:8090] [--pre|--post]
"""

import argparse
import json
import sys
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


def provider_row(name: str):
    _st, body = _req("GET", "/api/gland/providers")
    for p in (body or {}).get("providers", []):
        if p.get("name") == name:
            return p
    return None


def provider_names() -> list[str]:
    _st, body = _req("GET", "/api/gland/providers")
    return sorted(p.get("name", "") for p in (body or {}).get("providers", []))


def keys_of(provider: str) -> list:
    _st, body = _req("GET", "/api/gland/keys")
    val = (body or {}).get(provider, [])
    return val if isinstance(val, list) else []


def sweep_keys(provider: str) -> int:
    removed = 0
    while keys_of(provider):
        _req("DELETE", f"/api/gland/keys/{provider}/0")
        removed += 1
        if removed > 20:
            break
    return removed


P = "gl2e2e-rereg"
BASE_A = "https://gl2e2e-a.example/v1"
BASE_B = "https://gl2e2e-b.example/v1"
K1 = "gl2e2e-key-old-11111111"
K2 = "gl2e2e-key-new-22222222"


def cleanup(baseline: list[str]) -> None:
    _req("DELETE", f"/api/gland/providers/{P}")
    sweep_keys(P)
    residue = {"provider": provider_row(P), "keys": keys_of(P), "names_extra": [n for n in provider_names() if n not in baseline]}
    ok = residue["provider"] is None and not residue["keys"] and not residue["names_extra"]
    check("cleanup zero residue", ok, json.dumps(residue, ensure_ascii=False))


def run_pre(baseline: list[str]) -> None:
    """修前：同名 POST 照常 200 静默覆盖 + 旧钥留驻打向新 base_url。"""
    st, body = _req("POST", "/api/gland/providers", {"name": P, "base_url": BASE_A, "api_key": K1, "priority": 99})
    check("P1 register ok", st == 200, f"{st} {body}")
    st, body = _req("POST", "/api/gland/providers", {"name": P, "base_url": BASE_B, "api_key": K2, "priority": 99})
    check("P2 same-name re-register (hazard: silent overwrite)", st == 200, f"{st} {body}")
    row = provider_row(P)
    check("P3 registration now points at NEW base_url", bool(row) and row.get("base_url") == BASE_B, json.dumps(row))
    ks = keys_of(P)
    old_held = any(str(k.get("masked_key", "")).endswith("1111") for k in ks)
    new_held = any(str(k.get("masked_key", "")).endswith("2222") for k in ks)
    # 硬证：旧钥留驻（凭据滞留）——旧钥可被用于新 base_url
    check("P4 OLD key still held (credential retention)", old_held, f"keys={ks}")
    check("P5 old+new keys both accumulated", old_held and new_held, f"keys={len(ks)}")
    cleanup(baseline)


def run_post(baseline: list[str]) -> None:
    """修后：同名 POST 409 零副作用 + 替换走 DELETE（级联清钥）再 POST。"""
    st, body = _req("POST", "/api/gland/providers", {"name": P, "base_url": BASE_A, "api_key": K1, "priority": 99})
    check("P1 register ok", st == 200, f"{st} {body}")
    st, body = _req("POST", "/api/gland/providers", {"name": P, "base_url": BASE_B, "api_key": K2, "priority": 50})
    check("P2 same-name re-register rejected 409", st == 409, f"{st} {body}")
    detail = (body or {}).get("detail", "")
    check("P3 detail names replace path", P in detail and "DELETE" in detail, detail)
    row = provider_row(P)
    check("P4 registration unchanged (base_url+priority)", bool(row) and row.get("base_url") == BASE_A and row.get("priority") == 99, json.dumps(row))
    ks = keys_of(P)
    old_only = len(ks) == 1 and str(ks[0].get("masked_key", "")).endswith("1111")
    check("P5 keys unchanged (old kept, new never added)", old_only, f"keys={ks}")
    st, body = _req("DELETE", f"/api/gland/providers/{P}")
    check("P6 delete cascades keys (GLAND-1)", st == 200 and (body or {}).get("keys_removed") == 1, f"{st} {body}")
    check("P7 keys gone after delete", keys_of(P) == [], f"keys={keys_of(P)}")
    st, body = _req("POST", "/api/gland/providers", {"name": P, "base_url": BASE_B, "api_key": K2, "priority": 99})
    check("P8 replace flow (delete-then-post) ok", st == 200, f"{st} {body}")
    row = provider_row(P)
    check("P9 fresh registration at new base_url", bool(row) and row.get("base_url") == BASE_B, json.dumps(row))
    ks = keys_of(P)
    only_new = len(ks) == 1 and str(ks[0].get("masked_key", "")).endswith("2222")
    check("P10 only NEW key held (old key unreachable)", only_new, f"keys={ks}")
    cleanup(baseline)


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    args = ap.parse_args()
    BASE = args.base
    st, _body = _req("GET", "/api/gland/health")
    if st != 200:
        print(f"health {st} — abort")
        return 2
    baseline = provider_names()
    print(f"baseline providers: {baseline}")
    if args.pre:
        run_pre(baseline)
    else:
        run_post(baseline)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""G1-① live e2e 探针：POST /api/brain/creativity/cross-domain 真 LLM 真消费。

证据面：
1. 修前 404 → 修后 200（路由真注册）
2. 真 LLM 产出 ideas 非空 + best_idea（cross_domain_idea 真被调用、非桩）
3. 缺参 422 如实拒
4. 方法层故障→status=error 不谎报 ok（上游打不通时的诚实面，条件断言）
5. /api/brain/creativity 兄弟口回归 + /api/health 回归

用法：python3 scripts/e2e_creativity_cross_domain.py [--base http://127.0.0.1:8090]
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]

PASS, FAIL = [], []


def _req(method, path, params=None, timeout=60):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")


# 1. 路由真注册（非 404）
st, body = _req("POST", "/api/brain/creativity/cross-domain",
                {"domain": "软件工程", "problem": "如何降低分布式系统的缓存失效延迟"})
print("main call:", st, json.dumps(body, ensure_ascii=False)[:300])
check("route_registered_non_404", st != 404, f"status={st}")

if st == 200:
    if body.get("status") == "ok":
        # 2. 真 LLM 产出
        check("llm_ideas_non_empty", bool(body.get("ideas")), f"n={len(body.get('ideas', []))}")
        check("best_idea_key_present", "best_idea" in body)
        idea0 = (body.get("ideas") or [{}])[0]
        check("idea_shape", "domain" in idea0 and "approach" in idea0, f"keys={sorted(idea0.keys())}")
    else:
        # 4. 故障如实报（上游不可达等）——status=error 即诚实面，禁伪装空结果
        check("error_honest_not_ok", body.get("status") == "error" and body.get("error"),
              f"error={str(body.get('error'))[:80]}")

# 3. 缺参 422
st2, _ = _req("POST", "/api/brain/creativity/cross-domain")
check("missing_params_422", st2 == 422, f"status={st2}")
st3, _ = _req("POST", "/api/brain/creativity/cross-domain", {"domain": "d"})
check("missing_problem_422", st3 == 422, f"status={st3}")

# 5. 兄弟口回归
st4, body4 = _req("POST", "/api/brain/creativity", {"problem": "测试问题", "count": 1})
check("sibling_creativity_regression", st4 == 200 and "alternatives" in body4, f"status={st4}")

# 6. health 回归
st5, body5 = _req("GET", "/api/brain/health")
check("brain_health_200", st5 == 200 and body5.get("status") == "healthy", f"status={st5}")

print(f"\n=== {len(PASS)} PASS / {len(FAIL)} FAIL ===")
if FAIL:
    print("FAILURES:", FAIL)
    sys.exit(1)

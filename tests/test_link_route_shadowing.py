#!/usr/bin/env python3
"""F3b-⑨b-③-1c-⑥ 路由遮蔽收口回归测试：/api/link/events·/stats·/health 归属。

DEC-⑫ 自决：三路径归 OpenLink（link.py 活消费方：admin-ui link 页/
event_stream 轮询/7 处健康监测），link_gateway 三读面迁 /api/link/gateway/*
（修前被同前缀先挂的 link_router 遮蔽=生产死路由=wired-but-not-functional
路由级断点，1c-③ 边界② 留档）。

三层证据分工：
① main.py 挂载序静态断言（真源=生产挂载序：link_router 先挂于同前缀
   link_gateway_router 是遮蔽根因所在，序倒换即改归属——断言锁死）
② 双 router 同序复刻 mini-app 的 HTTP 解析断言（canonical 归属零变化
   +gateway 三读面真可达，判别键：OpenLink /events 无 total、component）
③ live :8090 真栈双面取证由 scripts/e2e_link_event_tenant.py /
   e2e_link_token_revalidation.py 承担（Gate3，真生产挂载）。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import link as link_api
from src.api import link_gateway as lg


def _dual_app():
    # 与 src/main.py 挂载序同序同前缀复刻（遮蔽语义由挂载序决定）
    app = FastAPI()
    app.include_router(link_api.router, prefix="/api/link")
    app.include_router(lg.router, prefix="/api/link")
    return app


def test_main_mount_order_static():
    """生产挂载序真源断言：link_router 先挂=canonical 归属根因（禁倒换/禁删）。"""
    src = (
        Path(__file__)
        .resolve()
        .parent.parent.joinpath("src", "main.py")
        .read_text(encoding="utf-8")
    )
    i_open = src.find('app.include_router(link_router, prefix="/api/link"')
    i_gw = src.find('app.include_router(link_gateway_router, prefix="/api/link"')
    assert i_open != -1 and i_gw != -1, "两 router 挂载点必须都在 main.py"
    assert i_open < i_gw, "link_router 必须先挂（canonical 归属根因，倒换=改归属）"


def test_canonical_owner_openlink_unchanged():
    """/api/link/events·/stats·/health 归 OpenLink=活消费方契约逐键不变。"""
    c = TestClient(_dual_app())
    r = c.get("/api/link/events")
    assert r.status_code == 200
    assert set(r.json().keys()) == {"events"}  # OpenLink 面无 total（判别键）
    for path in ("/api/link/stats", "/api/link/health"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.json()["component"] == "OpenLink"


def test_gateway_read_faces_reachable():
    """gateway 三读面真可达（修前被遮蔽=生产死路由，1c-③ tenant 过滤面活面不可达）。"""
    c = TestClient(_dual_app())
    r = c.get("/api/link/gateway/events")
    assert r.status_code == 200
    assert set(r.json().keys()) == {"events", "total"}  # gateway 面（判别键）
    r = c.get("/api/link/gateway/stats")
    assert r.status_code == 200
    assert r.json()["component"] == "LinkGateway"
    assert "ws_clients" in r.json()  # gateway 计数真可经此观测
    r = c.get("/api/link/gateway/health")
    assert r.status_code == 200
    assert r.json()["component"] == "LinkGateway"


def test_no_shadowed_duplicate_routes_link_namespace():
    """link 命名空间 (method,path) 零重复=遮蔽收口结构性保证（写了≠接线了）。

    FastAPI 新版 include_router 惰性挂载（app.routes 只见 _IncludedRouter 不展开
    子路由）——真源=两 router 自身 route 表+挂载前缀合成全路径，逐键断言归属唯一。
    """
    seen = {}
    for owner, rt in (("OpenLink", link_api.router), ("LinkGateway", lg.router)):
        for route in rt.routes:
            for method in getattr(route, "methods", None) or []:
                key = (method, "/api/link" + getattr(route, "path", ""))
                assert key not in seen, (
                    f"shadowed duplicate route: {key} (owners: {seen[key]} vs {owner})"
                )
                seen[key] = owner

"""DEC-⑯ terminal 路径对齐测试——顶层别名 /ws/terminal（OpenMate terminal-panel 契约）。

修前：openmate/src/components/terminal-panel.tsx:49 连
`ws://${hostname}:8090/ws/terminal?token=...`（顶层路径），而本仓只挂
/api/terminal/ws/terminal（main.py prefix="/api/terminal"）→ 面板连不上终端
（live 404/403，DEC-⑭-① 诚实边界④留档件）。

收口=本仓加挂顶层别名（改动只限 ~/opensoul）：alias_router 挂**同一 handler
函数对象**（零第二实现=零鉴权/看门狗/PTY 漂移面），canonical 契约零变化。

覆盖（tmp 全隔离 USERS_DB/ENTERPRISE_DB，绝不写生产 data/）：
①接线面：main.py 真挂 alias_router（写了≠接线了）+别名路由=同一 handler 对象
②行为对等：别名路径 fail-closed 握手闸与 canonical 同源（缺/坏 token 拒绝+
  零 spawn forkpty 哨兵硬反证）
③OpenMate 契约：面板 URL 形（顶层 /ws/terminal?token=）真 spawn bash 算术展开
④canonical 面零变化：/api/terminal/health 与 /api/terminal/ws/terminal 契约不动
"""

import json
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.terminal_ws as terminal_ws_api

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

MARKER = "term-alias-42"


def _seed_user(db_path, active=1):
    uid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', ?, 'default')",
            (uid, f"u-{uid[:8]}", active),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(uid), role="user")


def _dual_app():
    """与 src/main.py 挂载同构：canonical（prefix）+ 顶层别名（无 prefix）。

    alias_router 容缺（raising=False 家族纪律）：控制组（旧代码）无此符号时让
    失败暴露在行为断言上（别名真跑通必炸），而非 fixture 结构错。
    """
    app = FastAPI()
    app.include_router(terminal_ws_api.router, prefix="/api/terminal")
    alias = getattr(terminal_ws_api, "alias_router", None)
    if alias is not None:
        app.include_router(alias)
    return app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + 双路径 mini-app（真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()
    return {
        "client": TestClient(_dual_app()),
        "uid_on": _seed_user(users_db, 1),
    }


def _assert_rejected(client, url, monkeypatch=None):
    """握手拒绝 + 零 spawn 硬反证（forkpty 哨兵：拒绝路径绝不能拉起 shell）。

    close code 1008=握手拒绝语义（F3b-④/DEC-⑭-① 同款）——与「路径不存在」
    的 TestClient 404 形态（code=1000）区分开：别名必须是**真路由真鉴权拒绝**，
    不是没挂上被 404 顺带「拒绝」（控制组分辨力所在）。
    """
    if monkeypatch is not None:

        def _boom():
            raise AssertionError("rejected handshake must not spawn a shell")

        monkeypatch.setattr(terminal_ws_api.os, "forkpty", _boom)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(url):
            pass  # pragma: no cover — 进入即拒
    assert exc_info.value.code == 1008, (
        f"expected handshake reject 1008 on {url}, got {exc_info.value.code}"
    )


class TestAliasWiring:
    """① 接线面（写了≠接线了）。"""

    def test_main_mounts_alias_static(self):
        src = (
            Path(__file__)
            .resolve()
            .parent.parent.joinpath("src", "main.py")
            .read_text(encoding="utf-8")
        )
        assert "from src.api.terminal_ws import alias_router as terminal_alias_router" in src
        assert 'app.include_router(terminal_alias_router, tags=["terminal"])' in src
        # canonical 挂载零变化（prefix 契约锁死）
        assert 'app.include_router(terminal_router, prefix="/api/terminal"' in src

    def test_alias_route_is_same_handler_object(self):
        """别名路由端点=canonical 同一函数对象（零第二实现=零漂移面）。"""
        alias_routes = [
            r
            for r in terminal_ws_api.alias_router.routes
            if getattr(r, "path", "") == "/ws/terminal"
        ]
        assert len(alias_routes) == 1
        canonical_routes = [
            r for r in terminal_ws_api.router.routes if getattr(r, "path", "") == "/ws/terminal"
        ]
        assert len(canonical_routes) == 1
        assert getattr(alias_routes[0], "endpoint") is getattr(canonical_routes[0], "endpoint")
        assert getattr(alias_routes[0], "endpoint") is terminal_ws_api.terminal_ws


class TestAliasBehaviorParity:
    """② 行为对等：别名路径与 canonical 同源鉴权。"""

    def test_alias_missing_token_rejected_zero_spawn(self, env, monkeypatch):
        _assert_rejected(env["client"], "/ws/terminal", monkeypatch)

    def test_alias_garbage_token_rejected_zero_spawn(self, env, monkeypatch):
        _assert_rejected(env["client"], "/ws/terminal?token=garbage-not-a-jwt", monkeypatch)

    def test_alias_and_canonical_reject_identically(self, env, monkeypatch):
        """双路径同拒（无 token）——别名不因对齐而放宽鉴权。"""
        _assert_rejected(env["client"], "/ws/terminal", monkeypatch)
        _assert_rejected(env["client"], "/api/terminal/ws/terminal", monkeypatch)


class TestOpenMateContract:
    """③ 面板 URL 形真跑通 + ④ canonical 面零变化。"""

    def _roundtrip(self, client, collected, error, url):
        try:
            with client.websocket_connect(url) as ws:
                ws.send_text(
                    json.dumps({"type": "input", "data": f"echo {MARKER[:10]}-$((6*7))\n"})
                )
                buf = ""
                for _ in range(60):
                    buf += ws.receive_text()
                    if MARKER in buf:
                        break
                collected.append(buf)
        except Exception as exc:  # noqa: BLE001 —— 捕获后如实回传给断言
            error.append(repr(exc))

    def _assert_real_shell(self, client, url, uid):
        collected, error = [], []
        t = threading.Thread(
            target=self._roundtrip,
            args=(client, collected, error, f"{url}?token={_token(uid)}"),
            daemon=True,
        )
        t.start()
        t.join(timeout=20)
        assert not t.is_alive(), f"terminal WS hung on {url} —— 读泵死锁或没有 shell"
        assert not error, f"roundtrip error on {url}: {error}"
        buf = collected[0] if collected else ""
        assert MARKER in buf, f"{url} bash 未真执行（无算术展开）；实际收到: {buf!r}"

    def test_openmate_url_shape_spawns_real_shell(self, env):
        """面板 URL 形（顶层 /ws/terminal?token=）真 spawn bash 算术展开。"""
        self._assert_real_shell(env["client"], "/ws/terminal", env["uid_on"])

    def test_canonical_path_still_spawns_real_shell(self, env):
        """canonical 面零变化（回归锁死）。"""
        self._assert_real_shell(env["client"], "/api/terminal/ws/terminal", env["uid_on"])

    def test_canonical_health_unchanged(self, env):
        r = env["client"].get("/api/terminal/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "component": "TerminalWS"}

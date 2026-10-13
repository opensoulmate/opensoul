"""Integration tests for OpenEcho (回声) — message dispatch."""

import os
import sqlite3
import uuid
from pathlib import Path

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")
PROD_ECHO_TEMPLATES = Path(os.path.expanduser("~/opensoul/data/echo/templates.db"))


class TestEchoHealth:
    def test_health(self, echo_client):
        resp = echo_client.get("/api/echo/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenEcho"


class TestEchoChannels:
    def test_list_channels(self, echo_client):
        resp = echo_client.get("/api/echo/channels")
        assert resp.status_code == 200
        channels = resp.json()
        assert isinstance(channels, list) or "channels" in channels

    def test_configure_channel(self, echo_client):
        resp = echo_client.post(
            "/api/echo/channels/configure",
            json={
                "channel": "console",
                "enabled": True,
            },
        )
        assert resp.status_code == 200


class TestEchoSend:
    def test_send_console(self, echo_client):
        resp = echo_client.post(
            "/api/echo/send",
            json={
                "channel": "console",
                "title": "Test Message",
                "content": "Integration test from OpenSoul",
                "priority": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_broadcast(self, echo_client):
        resp = echo_client.post(
            "/api/echo/broadcast",
            json={
                "title": "Broadcast Test",
                "content": "Testing broadcast",
                "priority": 5,
            },
        )
        assert resp.status_code == 200


class TestEchoHistory:
    def test_history(self, echo_client):
        resp = echo_client.get("/api/echo/history")
        assert resp.status_code == 200


class TestTl2EchoGuard:
    """TL-② 拆件⑥ 隔离护栏：echo 消息/模板面与事件面全落 tmp，生产 timeline.db
    与生产 data/echo/templates.db 零测试行。"""

    @staticmethod
    def _count(db, sql, args=()):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()

    def test_message_event_lands_in_tmp_not_production(self, echo_client):
        marker = "tl2-echo-guard-" + uuid.uuid4().hex
        resp = echo_client.post(
            "/api/echo/send",
            json={
                "channel": "console",
                "title": marker,
                "content": "tl2 echo guard payload",
                "priority": 5,
            },
        )
        assert resp.status_code == 200

        tmp_tl = echo_client.app.state.echo_client_timeline_db
        assert tmp_tl == os.environ["TIMELINE_DB"]  # 隔离链真源一致
        assert tmp_tl != PROD_TIMELINE_DB
        assert (
            self._count(
                tmp_tl,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 1
        )  # message_sent 哨兵真落 tmp（summary 带 title=哨兵可注入）
        assert (
            self._count(
                PROD_TIMELINE_DB,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 0
        )  # 生产库零测试行（修前恒落生产）

    def test_template_face_lands_in_tmp_not_production(self, echo_client):
        marker = "tl2-echo-tpl-" + uuid.uuid4().hex
        resp = echo_client.post(
            "/api/echo/templates",
            json={
                "name": marker,
                "title_template": "t",
                "content_template": "c",
            },
        )
        assert resp.status_code == 200

        tmp_db = echo_client.app.state.echo_client_templates_db
        assert tmp_db != str(PROD_ECHO_TEMPLATES)
        assert self._count(tmp_db, "SELECT COUNT(*) FROM templates WHERE name=?", (marker,)) == 1
        if PROD_ECHO_TEMPLATES.exists():
            assert (
                self._count(
                    str(PROD_ECHO_TEMPLATES),
                    "SELECT COUNT(*) FROM templates WHERE name=?",
                    (marker,),
                )
                == 0
            )  # 生产模板面零测试行（修前恒写生产）
        assert (
            self._count(
                echo_client.app.state.echo_client_timeline_db,
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?",
                (f"%{marker}%",),
            )
            == 1
        )  # template_created 哨兵真落 tmp

    def test_module_singletons_tmp_backed(self, echo_client):
        import src.api.echo as echo_api

        state = echo_client.app.state
        assert echo_api.dispatcher is state.echo_client_dispatcher
        assert echo_api.template_engine is state.echo_client_template_engine
        # 派发历史真落 fixture 内存实例（修前留生产服务进程内存单例）
        resp = echo_client.post(
            "/api/echo/send",
            json={"channel": "console", "title": "t", "content": "c", "priority": 5},
        )
        assert resp.status_code == 200
        resp = echo_client.get("/api/echo/history")
        assert resp.status_code == 200

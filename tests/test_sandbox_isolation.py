"""OpenMirror 真隔离沙箱测试——真实 bubblewrap 执行，零 mock（CAMEL python_verifier"执行验证"哲学）。

覆盖：文件系统隔离 / 网络策略热更新（deny→allow 同沙箱即时生效）/ 超时击杀 /
输出截断显式标记（AIHawk SHOWN/SENT）/ 环境变量隔离（凭据不进沙箱）/
E2B snapshot-fork（快照携文件系统+变量、分叉独立、血缘记录）/
fail-closed（隔离不可用拒绝执行，绝不宿主直跑）/ API 端点全链路。
"""

import http.server
import os
import socket
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

from src.mirror import isolation
from src.mirror.sandbox import SandboxManager, SandboxNotActive


@pytest.fixture()
def manager(tmp_path):
    return SandboxManager(sandbox_dir=tmp_path / "sandboxes")


@pytest.fixture()
def sandbox(manager):
    return manager.create(name="iso-test")


class LocalHTTPServer:
    """宿主本地 HTTP 服务——网络策略测试的确定性目标（不依赖外网）。"""

    def __enter__(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()

        handler_cls = self._make_handler()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def _make_handler():
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        return Handler


# ── 构造语义（接线钉：策略→命名空间标志的映射是单一真源）────────


class TestBwrapArgv:
    def test_deny_policy_unshares_net(self):
        argv = isolation.build_bwrap_argv("bwrap", "/tmp/ws", "echo hi", network_policy="deny")
        assert "--unshare-net" in argv
        assert "--unshare-user" in argv and "--unshare-pid" in argv
        assert "--clearenv" in argv
        assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]

    def test_allow_policy_keeps_network(self):
        argv = isolation.build_bwrap_argv("bwrap", "/tmp/ws", "echo hi", network_policy="allow")
        assert "--unshare-net" not in argv

    def test_workspace_bound_rw_and_host_hidden(self):
        argv = isolation.build_bwrap_argv("bwrap", "/tmp/ws", "true")
        ws_idx = argv.index("--bind")
        assert argv[ws_idx + 1] == "/tmp/ws" and argv[ws_idx + 2] == "/workspace"
        # /home 绝不 bind——宿主家目录在沙箱内不可见的构造层证据
        bound_paths = [argv[i + 1] for i, x in enumerate(argv) if x in ("--bind", "--ro-bind")]
        assert not any(p.startswith("/home") for p in bound_paths)

    def test_env_allowlist_and_extra_env(self, monkeypatch):
        monkeypatch.setenv("LANG", "C.UTF-8")
        argv = isolation.build_bwrap_argv("bwrap", "/tmp/ws", "true", extra_env={"MY_VAR": "v1"})
        chunks = [argv[i : i + 3] for i in range(len(argv)) if argv[i] == "--setenv"]
        assert ["--setenv", "PATH", "/usr/bin:/bin"] in chunks
        assert ["--setenv", "LANG", "C.UTF-8"] in chunks  # 白名单宿主变量放行
        assert ["--setenv", "MY_VAR", "v1"] in chunks  # 显式变量放行

    def test_unknown_policy_rejected(self):
        with pytest.raises(isolation.SandboxExecError):
            isolation.build_bwrap_argv("bwrap", "/tmp/ws", "true", network_policy="maybe")

    def test_nproc_limit_above_current_task_count(self):
        """回归钉：RLIMIT_NPROC 按线程计数，上限必须高于宿主当前 task 总数，否则 fork 直接 EAGAIN。"""
        assert isolation.nproc_limit() > isolation._system_task_count()


# ── 真隔离执行（真实 bwrap）────────────────────────────────────


class TestRunInSandbox:
    def test_exec_basic(self, manager, sandbox):
        result = manager.exec(sandbox.sandbox_id, "echo hello-sandbox")
        assert result["exit_code"] == 0
        assert "hello-sandbox" in result["stdout"]
        assert result["isolation"] == "bubblewrap"

    def test_host_filesystem_invisible(self, manager, sandbox):
        result = manager.exec(
            sandbox.sandbox_id,
            "test -d /home && echo VISIBLE || echo HIDDEN; "
            "cat /etc/passwd >/dev/null 2>&1 && echo ETC_VISIBLE || echo ETC_HIDDEN",
        )
        assert result["exit_code"] == 0
        assert "HIDDEN" in result["stdout"]
        assert "ETC_HIDDEN" in result["stdout"]

    def test_workspace_persists_tmp_isolated(self, manager, sandbox):
        result = manager.exec(
            sandbox.sandbox_id,
            "echo persisted > /workspace/out.txt && echo tmpdata > /tmp/scratch.txt && echo done",
        )
        assert result["exit_code"] == 0
        ws_file = sandbox.workspace + "/out.txt"
        with open(ws_file) as f:
            assert f.read().strip() == "persisted"
        import os

        assert not os.path.exists("/tmp/scratch.txt")

    def test_network_policy_hot_update(self, manager, sandbox):
        """E2B 网络策略热更新：同一沙箱 deny 失败 → 改 allow 立即生效，无需重建。"""
        with LocalHTTPServer() as srv:
            probe = (
                'python3 -c "import urllib.request;'
                f"print(urllib.request.urlopen('http://127.0.0.1:{srv.port}', timeout=3).status)\""
            )
            denied = manager.exec(sandbox.sandbox_id, probe)
            assert denied["exit_code"] != 0, "deny 策略下不应能访问网络"
            assert "200" not in denied["stdout"]

            assert manager.set_network_policy(sandbox.sandbox_id, "allow") is True
            allowed = manager.exec(sandbox.sandbox_id, probe)
            assert allowed["exit_code"] == 0
            assert "200" in allowed["stdout"]

    def test_timeout_kills_process_group(self, manager, sandbox):
        started = time.monotonic()
        result = manager.exec(sandbox.sandbox_id, "sleep 30", timeout_seconds=2)
        elapsed = time.monotonic() - started
        assert result["timed_out"] is True
        assert result["exit_code"] != 0
        assert elapsed < 10
        assert "timed out" in result["stderr"]  # 终止原因显式可见（失败必须可见）

    def test_output_truncation_explicit_marker(self, sandbox):
        result = isolation.run_in_sandbox(
            "python3 -c \"print('x' * 200000)\"",
            sandbox.workspace,
            network_policy="deny",
            max_output_bytes=10_000,
        )
        d = result.to_dict()
        assert d["stdout_truncated"] is True
        assert d["stdout_total_bytes"] >= 200_000  # 被截字节数如实计数
        assert "more characters truncated" in d["stdout"]  # 显式标记，不静默丢弃
        assert len(d["stdout"]) < 20_000
        assert d["stdout_preview"]  # SHOWN 预算另有独立预览

    def test_env_isolation_no_host_leak(self, manager, sandbox, monkeypatch):
        monkeypatch.setenv("SANDBOX_LEAK_CANARY", "secret-canary-value")
        result = manager.exec(
            sandbox.sandbox_id,
            "python3 -c \"import os;print(os.environ.get('SANDBOX_LEAK_CANARY','ABSENT'))\"",
        )
        assert "ABSENT" in result["stdout"]  # --clearenv+白名单：宿主凭据类变量不进沙箱
        assert "secret-canary-value" not in result["stdout"]
        explicit = manager.exec(
            sandbox.sandbox_id,
            "python3 -c \"import os;print(os.environ.get('MY_VAR','ABSENT'))\"",
            env={"MY_VAR": "v1"},
        )
        assert "v1" in explicit["stdout"]  # 显式传入的变量按需放行

    def test_fail_closed_refuses_when_isolation_unavailable(self, manager, sandbox, monkeypatch):
        """Letta §4.2 fail-closed：隔离不可用→拒绝执行，绝不宿主直跑。"""
        monkeypatch.setattr(isolation, "probe_isolation", lambda force=False: False)
        canary = sandbox.workspace + "/../canary.txt"
        with pytest.raises(isolation.IsolationUnavailable):
            manager.exec(sandbox.sandbox_id, f"touch {canary}")
        import os

        assert not os.path.exists(canary)  # 命令从未在任何环境执行过

    def test_paused_sandbox_rejects_exec(self, manager, sandbox):
        manager.pause(sandbox.sandbox_id)
        with pytest.raises(SandboxNotActive):
            manager.exec(sandbox.sandbox_id, "echo hi")

    def test_missing_sandbox_returns_none(self, manager):
        assert manager.exec("sandbox-nonexistent", "echo hi") is None

    def test_exec_logged_in_audit_trail(self, manager, sandbox):
        manager.exec(sandbox.sandbox_id, "echo audited")
        log = manager.get_log(sandbox.sandbox_id)
        entries = [e for e in log if e["action"] == "exec"]
        assert entries and entries[-1]["detail"]["exit_code"] == 0
        assert entries[-1]["detail"]["network_policy"] == "deny"


# ── E2B snapshot-fork ─────────────────────────────────────────


class TestSnapshotFork:
    def test_snapshot_captures_workspace_and_vars(self, manager, sandbox):
        manager.exec(sandbox.sandbox_id, "echo snapshot-data > /workspace/state.txt")
        manager.set_variable(sandbox.sandbox_id, "k", "v")
        snap = manager.snapshot(sandbox.sandbox_id, "before-mutation")
        assert snap["workspace_copied"] is True
        assert snap["variables"] == {"k": "v"}

    def test_fork_restores_workspace_and_vars(self, manager, sandbox):
        manager.exec(sandbox.sandbox_id, "echo snapshot-data > /workspace/state.txt")
        manager.set_variable(sandbox.sandbox_id, "k", "v")
        snap = manager.snapshot(sandbox.sandbox_id)

        # 源沙箱继续变异——fork 不受后续变异影响（快照语义）
        manager.exec(sandbox.sandbox_id, "echo mutated > /workspace/state.txt")
        child = manager.fork_from_snapshot(sandbox.sandbox_id, snap["snapshot_id"], "forked")
        assert child is not None
        result = manager.exec(child.sandbox_id, "cat /workspace/state.txt")
        assert "snapshot-data" in result["stdout"]
        assert manager.get_variable(child.sandbox_id, "k") == "v"

    def test_fork_lineage_and_policy_inheritance(self, manager, sandbox):
        manager.set_network_policy(sandbox.sandbox_id, "allow")
        snap = manager.snapshot(sandbox.sandbox_id)
        child = manager.fork_from_snapshot(sandbox.sandbox_id, snap["snapshot_id"])
        assert child.config["forked_from"] == sandbox.sandbox_id
        assert child.config["forked_from_snapshot"] == snap["snapshot_id"]
        assert manager.network_policy(child.sandbox_id) == "allow"

    def test_fork_independence(self, manager, sandbox):
        snap = manager.snapshot(sandbox.sandbox_id)
        child_a = manager.fork_from_snapshot(sandbox.sandbox_id, snap["snapshot_id"], "a")
        child_b = manager.fork_from_snapshot(sandbox.sandbox_id, snap["snapshot_id"], "b")
        manager.exec(child_a.sandbox_id, "echo a-only > /workspace/f.txt")
        result = manager.exec(child_b.sandbox_id, "cat /workspace/f.txt")
        assert result["exit_code"] != 0  # 分叉互不可见

    def test_fork_unknown_snapshot_returns_none(self, manager, sandbox):
        assert manager.fork_from_snapshot(sandbox.sandbox_id, "snap-deadbeef") is None
        assert manager.fork_from_snapshot("sandbox-nonexistent", "snap-deadbeef") is None


# ── API 端点（进程内 mirror_client：真实 router + tmp 数据面，TL-② 拆件①收编）──


class TestApi:
    def test_api_exec_and_isolation(self, mirror_client):
        resp = mirror_client.post("/api/mirror/sandboxes", json={"name": "api-iso"})
        sid = resp.json()["sandbox_id"]
        try:
            resp = mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/exec",
                json={"command": "echo api-roundtrip"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["exit_code"] == 0 and "api-roundtrip" in data["stdout"]
            assert data["isolation"] == "bubblewrap"

            resp = mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/exec",
                json={"command": "test -d /home && echo VISIBLE || echo HIDDEN"},
            )
            assert "HIDDEN" in resp.json()["stdout"]
        finally:
            mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

    def test_api_network_policy_hot_update(self, mirror_client):
        with LocalHTTPServer() as srv:
            resp = mirror_client.post("/api/mirror/sandboxes", json={"name": "api-net"})
            sid = resp.json()["sandbox_id"]
            try:
                probe = (
                    'python3 -c "import urllib.request;'
                    f"print(urllib.request.urlopen('http://127.0.0.1:{srv.port}', timeout=3).status)\""
                )
                resp = mirror_client.post(
                    f"/api/mirror/sandboxes/{sid}/exec", json={"command": probe}
                )
                assert resp.json()["exit_code"] != 0  # 默认 deny

                resp = mirror_client.post(
                    f"/api/mirror/sandboxes/{sid}/network-policy", json={"policy": "allow"}
                )
                assert resp.status_code == 200
                resp = mirror_client.post(
                    f"/api/mirror/sandboxes/{sid}/exec", json={"command": probe}
                )
                assert resp.json()["exit_code"] == 0 and "200" in resp.json()["stdout"]
            finally:
                mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

    def test_api_fork_from_snapshot(self, mirror_client):
        resp = mirror_client.post("/api/mirror/sandboxes", json={"name": "api-fork-src"})
        sid = resp.json()["sandbox_id"]
        try:
            mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/exec",
                json={"command": "echo forked-state > /workspace/state.txt"},
            )
            resp = mirror_client.post(f"/api/mirror/sandboxes/{sid}/snapshot", json={"name": "s1"})
            snap_id = resp.json()["snapshot_id"]
            resp = mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/fork",
                json={"snapshot_id": snap_id, "name": "api-fork-child"},
            )
            assert resp.status_code == 200
            child_id = resp.json()["sandbox_id"]
            try:
                resp = mirror_client.post(
                    f"/api/mirror/sandboxes/{child_id}/exec",
                    json={"command": "cat /workspace/state.txt"},
                )
                assert "forked-state" in resp.json()["stdout"]
            finally:
                mirror_client.delete(f"/api/mirror/sandboxes/{child_id}")
        finally:
            mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

    def test_api_policy_validation_and_errors(self, mirror_client):
        resp = mirror_client.post("/api/mirror/sandboxes", json={"name": "api-err"})
        sid = resp.json()["sandbox_id"]
        try:
            resp = mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/network-policy", json={"policy": "maybe"}
            )
            assert resp.status_code == 400
            resp = mirror_client.post(
                "/api/mirror/sandboxes/sandbox-nonexistent/exec", json={"command": "true"}
            )
            assert resp.status_code == 404
            resp = mirror_client.post(f"/api/mirror/sandboxes/{sid}/pause")
            assert resp.status_code == 200
            resp = mirror_client.post(f"/api/mirror/sandboxes/{sid}/exec", json={"command": "true"})
            assert resp.status_code == 409
        finally:
            mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

    def test_api_stats_isolation_visible(self, mirror_client):
        resp = mirror_client.get("/api/mirror/health")
        assert resp.status_code == 200
        assert resp.json()["isolation_available"] is True


# ── 测试隔离护栏（TL-② 拆件①：live 写方收编）──────────────────

PROD_TIMELINE_DB = str(Path(__file__).resolve().parents[1] / "data" / "timeline.db")


def _count_prod_summary(marker: str) -> int:
    """只读数生产 timeline.db 中含哨兵的行数（file:...?mode=ro URI，绝不写）。"""
    if not Path(PROD_TIMELINE_DB).exists():
        return 0
    con = sqlite3.connect(f"file:{PROD_TIMELINE_DB}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
        ).fetchone()[0]
    finally:
        con.close()


class TestTl2IsolationGuard:
    """TL-② 拆件① 隔离护栏：API 事件/沙箱文件面/模板库全落 tmp，生产库零测试行。"""

    def test_api_events_land_in_tmp_timeline_not_production(self, mirror_client):
        marker = f"tl2-guard-{uuid.uuid4().hex}"
        resp = mirror_client.post("/api/mirror/sandboxes", json={"name": marker})
        assert resp.status_code == 200
        sid = resp.json()["sandbox_id"]
        try:
            resp = mirror_client.post(
                f"/api/mirror/sandboxes/{sid}/exec", json={"command": "echo guard-roundtrip"}
            )
            assert resp.status_code == 200 and "guard-roundtrip" in resp.json()["stdout"]
        finally:
            mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

        tmp_db = mirror_client.app.state.mirror_client_timeline_db
        assert tmp_db == os.environ["TIMELINE_DB"]  # 隔离链真源一致（禁只信构造参数）
        assert tmp_db != PROD_TIMELINE_DB
        con = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            hits = con.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ?", (f"%{marker}%",)
            ).fetchone()[0]
        finally:
            con.close()
        assert hits == 1  # sandbox_created 哨兵真落 tmp（进程内真路径真消费）
        assert _count_prod_summary(marker) == 0  # 生产库零测试行（修前恒落生产）

    def test_sandbox_files_land_in_tmp_dir_not_production(self, mirror_client):
        sandbox_dir = Path(mirror_client.app.state.mirror_client_sandbox_dir)
        assert sandbox_dir != Path.home() / ".opensoul" / "sandboxes"
        resp = mirror_client.post("/api/mirror/sandboxes", json={"name": "tl2-dir-check"})
        assert resp.status_code == 200
        sid = resp.json()["sandbox_id"]
        try:
            assert (sandbox_dir / sid).exists()  # 沙箱文件面真落 tmp
        finally:
            mirror_client.delete(f"/api/mirror/sandboxes/{sid}")

    def test_template_db_is_tmp_not_production(self, mirror_client):
        tpl_db = Path(mirror_client.app.state.mirror_client_templates_db)
        prod_db = Path(__file__).resolve().parents[1] / "data" / "mirror" / "templates.db"
        assert tpl_db != prod_db
        resp = mirror_client.get("/api/mirror/templates")
        assert resp.status_code == 200
        assert tpl_db.exists()  # 模板面真落 tmp 库（seed 面不碰生产）

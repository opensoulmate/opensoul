"""Sandbox manager — isolated testing environments."""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.mirror import isolation


class SandboxNotActive(RuntimeError):
    """沙箱不存在/已暂停/已销毁时拒绝执行（失败必须可见，不静默执行）。"""


@dataclass
class Sandbox:
    sandbox_id: str
    name: str
    created_at: float
    status: str = "active"  # "active", "paused", "destroyed"
    config: dict = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # Sandbox state
    variables: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    snapshot_count: int = 0
    ttl_seconds: int = 3600  # auto-cleanup after 1 hour
    data_dir: str = ""
    workspace: str = ""  # 真隔离执行目录（bwrap 只暴露此目录为 /workspace）
    tenant_id: str = "default"  # 行租户键（DEC-⑦-㉓）


def sandbox_row_tenant(raw: object) -> str:
    """行租户键单一判法（DEC-⑦-㉓；DEC-⑦-㉒b template_row_tenant 同款语义：
    自带键缺失/空白→'default' 兜底，键派生故障绝不吃掉落库）。raw=Sandbox 行
    对象（读侧过滤）或原始租户值（写侧落行），写读同一判法禁双源漂移
    （F4b-㉗ 纪律）。"""
    if hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


class SandboxManager:
    """Manage isolated sandboxes for testing workflows, agents, and connectors."""

    def __init__(self, sandbox_dir: str | Path | None = None):
        # 目录路径单一解析（DEC-⑦-㉓ 测试隔离增补，MIRROR_TEMPLATES_DB 先例）：
        # MIRROR_SANDBOX_DIR env 优先——src.api.mirror 模块级 manager 单例 import
        # 即构造缺省解析 ~/.opensoul/sandboxes（mkdir 生产目录副作用），测试进程
        # 的 import 会碰生产路径；缺省语义不变（服务无该 env=生产路径）。
        base = (
            sandbox_dir
            or os.environ.get("MIRROR_SANDBOX_DIR")
            or os.path.expanduser("~/.opensoul/sandboxes")
        )
        self.sandbox_dir = Path(base)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.Lock()

    def create(
        self,
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        config: dict | None = None,
        ttl_seconds: int = 3600,
        tenant_id: str = "",
    ) -> Sandbox:
        """Create a new sandbox.（行归属=bind_identity 派生值落行，缺失/空白→
        'default' 兜底——DEC-⑦-㉓，⑦-㉒b create 写点同款）"""
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:8]}"
        data_dir = self.sandbox_dir / sandbox_id
        data_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = data_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        sandbox = Sandbox(
            sandbox_id=sandbox_id,
            name=name or f"Sandbox {sandbox_id}",
            created_at=time.time(),
            config=config or {},
            description=description,
            tags=tags or [],
            ttl_seconds=ttl_seconds,
            data_dir=str(data_dir),
            workspace=str(workspace_dir),
            tenant_id=sandbox_row_tenant(tenant_id),
        )

        with self._lock:
            self._sandboxes[sandbox_id] = sandbox

        return sandbox

    def get(self, sandbox_id: str, tenant_id: str | None = None) -> Sandbox | None:
        """by-ID 单点归属闸（DEC-⑦-㉓；⑦-㉒b get_template 单点闸同款）：显式
        tenant=跨租户与 not-found 同契约回 None 不差分泄露（③c-1 纪律），
        None=系统视图字节不变（内部调用面）。"""
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is not None and tenant_id is not None:
            if sandbox_row_tenant(sandbox) != sandbox_row_tenant(tenant_id):
                return None
        return sandbox

    def list_sandboxes(self, status: str | None = None, tenant_id: str | None = None) -> list[dict]:
        """读面恒过滤（DEC-⑦-㉓）：显式 tenant=只列行归属可见集（过滤先于
        status/计数禁跨租户占名额），None=系统视图字节不变。"""
        with self._lock:
            sandboxes = list(self._sandboxes.values())
        if tenant_id is not None:
            want = sandbox_row_tenant(tenant_id)
            sandboxes = [s for s in sandboxes if sandbox_row_tenant(s) == want]
        if status:
            sandboxes = [s for s in sandboxes if s.status == status]

        return [
            {
                "sandbox_id": s.sandbox_id,
                "name": s.name,
                "status": s.status,
                "created_at": s.created_at,
                "description": s.description,
                "tags": s.tags,
                "variables_count": len(s.variables),
                "log_entries": len(s.log),
                "snapshot_count": s.snapshot_count,
                "ttl_seconds": s.ttl_seconds,
                "expires_at": s.created_at + s.ttl_seconds,
                "tenant_id": sandbox_row_tenant(s),
            }
            for s in sorted(sandboxes, key=lambda x: x.created_at, reverse=True)
        ]

    def destroy(self, sandbox_id: str, tenant_id: str | None = None) -> bool:
        """删除归属闸（DEC-⑦-㉓）：跨租户=False 与 not-found 同契约 404 不差分
        泄露、行零破坏（③b-3 禁跨租户破坏纪律）。"""
        if not self.get(sandbox_id, tenant_id):
            return False
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
        if not sandbox:
            return False

        # Clean up data directory
        if sandbox.data_dir and os.path.exists(sandbox.data_dir):
            shutil.rmtree(sandbox.data_dir, ignore_errors=True)

        return True

    def log_action(
        self,
        sandbox_id: str,
        action: str,
        detail: dict | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """Log an action in the sandbox.（by-ID 归属闸：跨租户=False 与
        not-found 同契约——DEC-⑦-㉓）"""
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox or sandbox.status != "active":
            return False

        entry = {
            "timestamp": time.time(),
            "action": action,
            "detail": detail or {},
        }
        sandbox.log.append(entry)
        return True

    def set_variable(
        self,
        sandbox_id: str,
        key: str,
        value: Any,
        tenant_id: str | None = None,
    ) -> bool:
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox or sandbox.status != "active":
            return False
        sandbox.variables[key] = value
        return True

    def get_variable(self, sandbox_id: str, key: str, tenant_id: str | None = None) -> Any:
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return None
        return sandbox.variables.get(key)

    def snapshot(
        self, sandbox_id: str, name: str = "", tenant_id: str | None = None
    ) -> dict | None:
        """Take a snapshot of sandbox state.（by-ID 归属闸：跨租户=None 与
        not-found 同契约——DEC-⑦-㉓）"""
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return None

        snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"
        snapshot_data = {
            "snapshot_id": snapshot_id,
            "name": name or f"Snapshot {sandbox.snapshot_count + 1}",
            "created_at": time.time(),
            "variables": copy.deepcopy(sandbox.variables),
            "log_length": len(sandbox.log),
            "network_policy": self.network_policy(sandbox_id),
        }

        # Save snapshot to disk
        if sandbox.data_dir:
            snap_path = Path(sandbox.data_dir) / f"{snapshot_id}.json"
            snap_path.write_text(json.dumps(snapshot_data, ensure_ascii=False, indent=2))
            # E2B snapshot-fork 语义：快照必须携带文件系统状态（目录级拷贝），
            # fork_from_snapshot 才能从快照恢复出完整 workspace。
            if sandbox.workspace and os.path.isdir(sandbox.workspace):
                snap_ws = Path(sandbox.data_dir) / "snapshots" / snapshot_id / "workspace"
                snap_ws.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(sandbox.workspace, snap_ws, dirs_exist_ok=True)
                snapshot_data["workspace_copied"] = True

        sandbox.snapshot_count += 1
        return snapshot_data

    def get_log(
        self, sandbox_id: str, limit: int = 100, tenant_id: str | None = None
    ) -> list[dict]:
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return []
        return sandbox.log[-limit:]

    def pause(self, sandbox_id: str, tenant_id: str | None = None) -> bool:
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return False
        sandbox.status = "paused"
        return True

    def resume(self, sandbox_id: str, tenant_id: str | None = None) -> bool:
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox or sandbox.status != "paused":
            return False
        sandbox.status = "active"
        return True

    def cleanup_expired(self, tenant_id: str | None = None) -> int:
        """Clean up expired sandboxes.（清理范围恒限可见集——DEC-⑦-㉓ ③a purge
        同款：显式 tenant 的清理不算零过滤条件，防"只给租户"变相清他租户行；
        None=系统视图全量。）"""
        now = time.time()
        expired = []
        with self._lock:
            candidates = list(self._sandboxes.items())
        for sid, s in candidates:
            if tenant_id is not None and sandbox_row_tenant(s) != sandbox_row_tenant(tenant_id):
                continue
            if now > s.created_at + s.ttl_seconds:
                expired.append(sid)

        count = 0
        for sid in expired:
            if self.destroy(sid):
                count += 1
        return count

    def stats(self, tenant_id: str | None = None) -> dict:
        """聚合作用域（DEC-⑦-㉓）：显式 tenant=恒按可见集聚合禁泄露全局分布
        （⑦-⑭⑰ 同款），None=系统视图 /health 全量不变；sandbox_dir/
        isolation_available 为全局配置计数恒全局。"""
        with self._lock:
            rows = list(self._sandboxes.values())
        if tenant_id is not None:
            want = sandbox_row_tenant(tenant_id)
            rows = [s for s in rows if sandbox_row_tenant(s) == want]
        total = len(rows)
        active = sum(1 for s in rows if s.status == "active")
        paused = sum(1 for s in rows if s.status == "paused")
        return {
            "total_sandboxes": total,
            "active": active,
            "paused": paused,
            "sandbox_dir": str(self.sandbox_dir),
            "isolation_available": isolation.probe_isolation(),
        }

    # ── 真隔离执行（SUMMARY §五 mirror: E2B 网络策略热更新 + snapshot-fork）──

    def network_policy(self, sandbox_id: str, tenant_id: str | None = None) -> str:
        """当前网络策略：allow | deny（默认 deny，最小权限）。跨租户与
        not-found 同契约回 deny（DEC-⑦-㉓ 归属闸）。"""
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return "deny"
        return str(sandbox.config.get("network_policy", "deny"))

    def set_network_policy(
        self, sandbox_id: str, policy: str, tenant_id: str | None = None
    ) -> bool:
        """热更新网络策略（E2B 语义）：改后立即对后续 exec 生效，无需重建沙箱。
        （by-ID 归属闸：跨租户=False 与 not-found 同契约——DEC-⑦-㉓）"""
        isolation.validate_policy(policy)
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return False
        sandbox.config["network_policy"] = policy
        self.log_action(sandbox_id, "network_policy_changed", {"policy": policy})
        return True

    def exec(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: float = isolation.DEFAULT_TIMEOUT_S,
        env: dict[str, str] | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        """在真隔离环境内执行命令。

        - 沙箱不存在 → None（调用方 404）
        - 跨租户 → None 与 not-found 同契约（DEC-⑦-㉓ 归属闸，零执行零留痕）
        - 沙箱暂停/销毁 → SandboxNotActive（调用方 409）
        - 真隔离不可用 → IsolationUnavailable（fail-closed，绝不宿主直跑）
        成功/失败一律 log_action 留痕（mem0 §1.2 审计语义）。
        """
        sandbox = self.get(sandbox_id, tenant_id)
        if not sandbox:
            return None
        if sandbox.status != "active":
            raise SandboxNotActive(f"sandbox {sandbox_id} is {sandbox.status}, exec refused")

        policy = self.network_policy(sandbox_id)
        result = isolation.run_in_sandbox(
            command,
            sandbox.workspace or sandbox.data_dir,
            network_policy=policy,
            timeout=timeout_seconds,
            env=env or {},
        )
        self.log_action(
            sandbox_id,
            "exec",
            {
                "command": command,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "truncated": result.stdout_truncated or result.stderr_truncated,
                "network_policy": policy,
                "duration_s": round(result.duration_s, 3),
            },
        )
        return result.to_dict()

    def fork_from_snapshot(
        self,
        sandbox_id: str,
        snapshot_id: str,
        name: str = "",
        tenant_id: str | None = None,
    ) -> Sandbox | None:
        """E2B snapshot-fork：从快照分叉出新沙箱（workspace 目录拷贝 + 变量恢复）。

        血缘记录在新沙箱 config（forked_from / forked_from_snapshot），网络策略继承源沙箱。
        行归属（DEC-⑦-㉓）：源读走 by-ID 归属闸（跨租户=None 与 not-found 同契约
        零副作用，⑦-⑪ fork 源读闸同款）；子沙箱行键=显式 tenant（fork 归克隆者
        ⑦-⑥ 语义，API 传 bind_identity 派生值）→缺失/空白=源行继承（无身份内部
        面父行继承 ③b-2 语义，绝不掉落 'default' 造成跨租户可见）。
        """
        source = self.get(sandbox_id, tenant_id)
        if not source:
            return None
        snap_json = Path(source.data_dir) / f"{snapshot_id}.json"
        snap_ws = Path(source.data_dir) / "snapshots" / snapshot_id / "workspace"
        if not snap_json.is_file():
            return None
        snapshot_data = json.loads(snap_json.read_text())

        child = self.create(
            name=name or f"Fork of {source.name} ({snapshot_id})",
            description=f"forked from {sandbox_id} snapshot {snapshot_id}",
            tags=list(source.tags) + ["fork"],
            config={
                "network_policy": self.network_policy(sandbox_id),
                "forked_from": sandbox_id,
                "forked_from_snapshot": snapshot_id,
            },
            ttl_seconds=source.ttl_seconds,
            # 行归属：显式=归克隆者，缺失/空白=源行继承（见 docstring）
            tenant_id=str(tenant_id or "").strip() or sandbox_row_tenant(source),
        )
        if snap_ws.is_dir() and child.workspace:
            shutil.copytree(snap_ws, child.workspace, dirs_exist_ok=True)
        for key, value in (snapshot_data.get("variables") or {}).items():
            self.set_variable(child.sandbox_id, key, value)
        self.log_action(
            child.sandbox_id,
            "forked_from_snapshot",
            {"source_sandbox_id": sandbox_id, "snapshot_id": snapshot_id},
        )
        return child

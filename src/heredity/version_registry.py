"""Version Registry — Track component versions, dependencies, and compatibility.

RV-①b-⑭-1：_components/_migrations/_changelog 三面 + platform_version 持久化——
状态快照表「内存=真源、行=影子」（healer RV-①b-⑬ 同族）：变更点写穿（写穿单一点
_sync_cv/_sync_migration/_sync_changelog/_sync_platform）+首触懒回载（先载后录、
只补缺不覆盖）+SEED 只补缺随回载播种。persist 缺省 None=纯内存零变化。
细节/诚实边界见 registry_persist.py。
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .registry_persist import RegistryPersistence

logger = logging.getLogger(__name__)


class VersionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class MigrationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class ComponentVersion:
    component_id: str
    component_name: str
    version: str  # semver: MAJOR.MINOR.PATCH
    status: VersionStatus = VersionStatus.ACTIVE
    release_notes: str = ""
    dependencies: dict[str, str] = field(default_factory=dict)  # component_id -> version_range
    config_schema: dict = field(default_factory=dict)
    breaking_changes: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    retired_at: float | None = None
    cv_id: str = field(default_factory=lambda: f"cv_{uuid.uuid4().hex[:12]}")


@dataclass
class MigrationPlan:
    migration_id: str
    component_id: str
    from_version: str
    to_version: str
    steps: list[dict]  # ordered list of migration steps
    status: MigrationStatus = MigrationStatus.PENDING
    dry_run: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    rollback_plan: list[dict] = field(default_factory=list)


@dataclass
class ChangelogEntry:
    entry_id: str
    component_id: str
    version: str
    change_type: str  # feature, fix, breaking, deprecation, security
    description: str
    author: str = "system"
    timestamp: float = field(default_factory=time.time)


class VersionRegistry:
    """Central registry for all component versions."""

    def __init__(
        self,
        persist: RegistryPersistence | None = None,
        seed_components: list[tuple] | None = None,
    ):
        self._components: dict[str, list[ComponentVersion]] = {}
        self._migrations: list[MigrationPlan] = []
        self._changelog: list[ChangelogEntry] = []
        self._platform_version = "1.0.0"
        # ── RV-①b-⑭-1 三面持久化（persist 缺省 None=纯内存零变化）──
        self._persist = persist
        # SEED 组件随首触懒播（只补缺不覆盖）：修前 import 期无条件播种+写库会
        # 在测试收集期 import 污染生产库（MARROW-①「import 副作用」同型），且
        # 无条件播种会把 API 注册的新版本顶回旧版 current——只补缺=根因修。
        self._seed_components: list[tuple] = list(seed_components or [])
        self._registry_loaded = False
        self._loading = False

    # ── RV-①b-⑭-1 三面持久化 ────────────────────────────────────
    def _ensure_loaded(self) -> None:
        """首触懒回载（RV-①b-⑬ _ensure_loaded 同款）：读/写双首触——写口不回载=
        回载旧行冲掉在录新态（⑦-3 纪律）。先载后录（回载先于一切变更/播种）+
        只补缺不覆盖（内存在册更真禁回载洗内存新态，⑫ 纪律）；回载失败不置位
        下次触碰重试（registry_persist 诚实边界②）。加载完成后补播 SEED 缺件
        （component_id 已在册=跳过，禁顶掉 API 注册版本状态）。_loading 反重入
        哨兵（播种走 register() 复用同一写穿口，禁第二套注册路径）。
        坏行（状态枚举/JSON 不可解）跳过+logger.error 可见（persist 层坏行+
        本层枚举坏行双防线）。"""
        if self._registry_loaded or self._loading or self._persist is None:
            return
        self._loading = True
        try:
            load_ok = True
            try:
                cv_rows = self._persist.load_component_versions()
                mig_rows = self._persist.load_migrations()
                cl_rows = self._persist.load_changelog()
                platform = self._persist.get_meta("platform_version")
            except Exception as e:
                logger.error(f"heredity 注册表回载失败（空注册表起步，下次触碰重试）: {e}")
                cv_rows, mig_rows, cl_rows, platform = [], [], [], None
                load_ok = False
            # 只补缺：cv_id 精确行身份 + (component_id, version) 自然键防
            # 「回载失败→SEED 已落内存→重试回载」双行（宁稳勿重，边界见
            # registry_persist）；批内同自然键多行（同版本重复注册）各行独立
            # cv_id 逐行进内存不折叠。
            pre_cv_ids = {cv.cv_id for cv in self._iter_cvs()}
            pre_natural = {(cv.component_id, cv.version) for cv in self._iter_cvs()}
            for row in cv_rows:
                try:
                    cv = ComponentVersion(
                        component_id=row["component_id"],
                        component_name=row["component_name"],
                        version=row["version"],
                        status=VersionStatus(row["status"]),
                        release_notes=row["release_notes"],
                        dependencies=row["dependencies"],
                        config_schema=row["config_schema"],
                        breaking_changes=row["breaking_changes"],
                        created_at=row["created_at"],
                        retired_at=row["retired_at"],
                        cv_id=row["cv_id"],
                    )
                except Exception as e:
                    logger.error(
                        f"heredity 组件版本坏行跳过（cv_id={row.get('cv_id')}，状态不可解）: {e}"
                    )
                    continue
                if cv.cv_id in pre_cv_ids or (cv.component_id, cv.version) in pre_natural:
                    continue
                pre_cv_ids.add(cv.cv_id)
                self._components.setdefault(cv.component_id, []).append(cv)
            seen_mig = {m.migration_id for m in self._migrations}
            for row in mig_rows:
                if row["migration_id"] in seen_mig:
                    continue
                try:
                    migration = MigrationPlan(
                        migration_id=row["migration_id"],
                        component_id=row["component_id"],
                        from_version=row["from_version"],
                        to_version=row["to_version"],
                        steps=row["steps"],
                        status=MigrationStatus(row["status"]),
                        dry_run=row["dry_run"],
                        started_at=row["started_at"],
                        completed_at=row["completed_at"],
                        error=row["error"],
                        rollback_plan=row["rollback_plan"],
                    )
                except Exception as e:
                    logger.error(
                        f"heredity 迁移计划坏行跳过（migration_id={row.get('migration_id')}，状态不可解）: {e}"
                    )
                    continue
                seen_mig.add(migration.migration_id)
                self._migrations.append(migration)
            seen_cl = {e.entry_id for e in self._changelog}
            for row in cl_rows:
                if row["entry_id"] in seen_cl:
                    continue
                seen_cl.add(row["entry_id"])
                self._changelog.append(
                    ChangelogEntry(
                        entry_id=row["entry_id"],
                        component_id=row["component_id"],
                        version=row["version"],
                        change_type=row["change_type"],
                        description=row["description"],
                        author=row["author"],
                        timestamp=row["timestamp"],
                    )
                )
            if platform:
                self._platform_version = platform
            # 回载失败不置位=下次触碰重试（只补缺防重试双行，见上）
            self._registry_loaded = load_ok
            # SEED 只补缺随回载播种（在册 component_id 跳过——API 注册面重启
            # 不被种子顶回旧版）；播种写穿复用 register() 同一写穿口。
            for cid, cname, ver, deps in self._seed_components:
                if cid not in self._components:
                    self.register(cid, cname, ver, dependencies=deps)
        finally:
            self._loading = False

    def _iter_cvs(self):
        for versions in self._components.values():
            yield from versions

    def _sync_cv(self, cv: ComponentVersion) -> None:
        """组件版本行写穿单一点（全变更点共用禁散落写点）：记账旁路——写失败
        logger.error 可见不阻断注册动作本体（registry_persist 诚实边界①）。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert_component(
                {
                    "cv_id": cv.cv_id,
                    "component_id": cv.component_id,
                    "component_name": cv.component_name,
                    "version": cv.version,
                    "status": cv.status.value,
                    "release_notes": cv.release_notes,
                    "dependencies": cv.dependencies,
                    "config_schema": cv.config_schema,
                    "breaking_changes": cv.breaking_changes,
                    "created_at": cv.created_at,
                    "retired_at": cv.retired_at,
                }
            )
        except Exception as e:
            logger.error(f"heredity 组件版本落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _sync_migration(self, migration: MigrationPlan) -> None:
        """迁移计划行写穿单一点（create/execute/rollback 三变更点共用）。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert_migration(
                {
                    "migration_id": migration.migration_id,
                    "component_id": migration.component_id,
                    "from_version": migration.from_version,
                    "to_version": migration.to_version,
                    "steps": migration.steps,
                    "status": migration.status.value,
                    "dry_run": migration.dry_run,
                    "started_at": migration.started_at,
                    "completed_at": migration.completed_at,
                    "error": migration.error,
                    "rollback_plan": migration.rollback_plan,
                }
            )
        except Exception as e:
            logger.error(f"heredity 迁移计划落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _sync_changelog(self, entry: ChangelogEntry) -> None:
        """changelog 追加写穿单一点（append 表：撞主键如实抛进旁路日志禁覆盖）。"""
        if self._persist is None:
            return
        try:
            self._persist.append_changelog(
                {
                    "entry_id": entry.entry_id,
                    "component_id": entry.component_id,
                    "version": entry.version,
                    "change_type": entry.change_type,
                    "description": entry.description,
                    "author": entry.author,
                    "timestamp": entry.timestamp,
                }
            )
        except Exception as e:
            logger.error(f"heredity changelog 落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _sync_platform(self) -> None:
        """platform_version 标量写穿单一点（meta kv）。"""
        if self._persist is None:
            return
        try:
            self._persist.set_meta("platform_version", str(self._platform_version))
        except Exception as e:
            logger.error(f"heredity platform_version 落盘失败（该值只活内存重启丢）: {e}")

    # ── Component Registration ────────────────────────────

    def register(
        self,
        component_id: str,
        component_name: str,
        version: str,
        dependencies: dict[str, str] = None,
        config_schema: dict = None,
        release_notes: str = "",
        breaking_changes: list[str] = None,
    ) -> ComponentVersion:
        """Register a new version for a component."""
        self._ensure_loaded()  # RV-①b-⑭-1 先载后录（写首触同款，⑦-3 纪律）
        cv = ComponentVersion(
            component_id=component_id,
            component_name=component_name,
            version=version,
            dependencies=dependencies or {},
            config_schema=config_schema or {},
            release_notes=release_notes,
            breaking_changes=breaking_changes or [],
        )
        if component_id not in self._components:
            self._components[component_id] = []
        # Mark previous versions as deprecated
        for existing in self._components[component_id]:
            if existing.status == VersionStatus.ACTIVE:
                existing.status = VersionStatus.DEPRECATED
                self._sync_cv(existing)  # RV-①b-⑭-1 状态迁移随影落行
        self._components[component_id].append(cv)
        self._sync_cv(cv)  # RV-①b-⑭-1 新版本行写穿

        # Auto-add changelog
        self._add_changelog(
            component_id,
            version,
            "feature",
            release_notes or f"Registered {component_name} v{version}",
        )
        return cv

    def get_current(self, component_id: str) -> ComponentVersion | None:
        """Get the current active version of a component."""
        self._ensure_loaded()
        versions = self._components.get(component_id, [])
        for v in reversed(versions):
            if v.status in (VersionStatus.ACTIVE, VersionStatus.DRAFT):
                return v
        return versions[-1] if versions else None

    def get_all_versions(self, component_id: str) -> list[ComponentVersion]:
        """Get all versions for a component."""
        self._ensure_loaded()
        return self._components.get(component_id, [])

    def list_components(self) -> list[dict]:
        """List all registered components with their current version."""
        self._ensure_loaded()
        result = []
        for cid, versions in self._components.items():
            current = self.get_current(cid)
            if current:
                result.append(
                    {
                        "component_id": cid,
                        "component_name": current.component_name,
                        "current_version": current.version,
                        "status": current.status.value,
                        "total_versions": len(versions),
                        "dependencies": current.dependencies,
                        "created_at": current.created_at,
                    }
                )
        return result

    # ── Dependency Checking ───────────────────────────────

    def check_compatibility(self, component_id: str) -> dict:
        """Check if a component's dependencies are satisfied."""
        self._ensure_loaded()
        current = self.get_current(component_id)
        if not current:
            return {"compatible": False, "error": "Component not registered"}

        issues = []
        for dep_id, version_range in current.dependencies.items():
            dep_current = self.get_current(dep_id)
            if not dep_current:
                issues.append(
                    {"dependency": dep_id, "issue": "not_registered", "required": version_range}
                )
            elif not self._version_matches(dep_current.version, version_range):
                issues.append(
                    {
                        "dependency": dep_id,
                        "issue": "version_mismatch",
                        "required": version_range,
                        "actual": dep_current.version,
                    }
                )

        return {
            "compatible": len(issues) == 0,
            "component_id": component_id,
            "version": current.version,
            "issues": issues,
        }

    def get_dependency_graph(self) -> dict:
        """Get the full dependency graph of all components."""
        self._ensure_loaded()
        graph = {}
        for cid, versions in self._components.items():
            current = self.get_current(cid)
            if current:
                graph[cid] = {
                    "version": current.version,
                    "dependencies": current.dependencies,
                }
        return graph

    # ── Migration Management ──────────────────────────────

    def create_migration(
        self,
        component_id: str,
        from_version: str,
        to_version: str,
        steps: list[dict] = None,
        dry_run: bool = False,
    ) -> MigrationPlan:
        """Create a migration plan for upgrading a component."""
        self._ensure_loaded()  # RV-①b-⑭-1 先载后录
        migration = MigrationPlan(
            migration_id=f"mig_{uuid.uuid4().hex[:12]}",
            component_id=component_id,
            from_version=from_version,
            to_version=to_version,
            steps=steps
            or [
                {"action": "backup", "description": "Create backup before migration"},
                {"action": "validate", "description": "Validate pre-conditions"},
                {
                    "action": "migrate",
                    "description": f"Apply migration {from_version} → {to_version}",
                },
                {"action": "verify", "description": "Verify post-migration integrity"},
            ],
            dry_run=dry_run,
            rollback_plan=[
                {"action": "restore_backup", "description": "Restore from backup"},
                {"action": "verify_rollback", "description": "Verify rollback integrity"},
            ],
        )
        self._migrations.append(migration)
        self._sync_migration(migration)  # RV-①b-⑭-1 计划行写穿
        return migration

    def execute_migration(self, migration_id: str) -> MigrationPlan:
        """Execute a migration plan."""
        self._ensure_loaded()
        migration = None
        for m in self._migrations:
            if m.migration_id == migration_id:
                migration = m
                break
        if not migration:
            raise ValueError(f"Migration {migration_id} not found")

        migration.status = MigrationStatus.RUNNING
        migration.started_at = time.time()

        # Simulate migration steps
        try:
            for step in migration.steps:
                step["executed_at"] = time.time()
                step["status"] = "completed"

            migration.status = MigrationStatus.COMPLETED
            migration.completed_at = time.time()

            # Update component version
            current = self.get_current(migration.component_id)
            if current:
                self.register(
                    migration.component_id,
                    current.component_name,
                    migration.to_version,
                    dependencies=current.dependencies,
                    release_notes=f"Migrated from {migration.from_version}",
                )

            self._add_changelog(
                migration.component_id,
                migration.to_version,
                "feature",
                f"Migration completed: {migration.from_version} → {migration.to_version}",
            )
        except Exception as e:
            migration.status = MigrationStatus.FAILED
            migration.error = str(e)

        self._sync_migration(migration)  # RV-①b-⑭-1 终值随影落行（成功/失败同点）
        return migration

    def rollback_migration(self, migration_id: str) -> MigrationPlan:
        """Rollback a migration."""
        self._ensure_loaded()
        migration = None
        for m in self._migrations:
            if m.migration_id == migration_id:
                migration = m
                break
        if not migration:
            raise ValueError(f"Migration {migration_id} not found")

        migration.status = MigrationStatus.ROLLED_BACK
        self._add_changelog(
            migration.component_id,
            migration.from_version,
            "fix",
            f"Migration rolled back: {migration.to_version} → {migration.from_version}",
        )
        self._sync_migration(migration)  # RV-①b-⑭-1 状态迁移随影落行
        return migration

    def list_migrations(self, component_id: str = None) -> list[MigrationPlan]:
        """List migrations, optionally filtered by component."""
        self._ensure_loaded()
        if component_id:
            return [m for m in self._migrations if m.component_id == component_id]
        return self._migrations

    # ── Changelog ─────────────────────────────────────────

    def _add_changelog(self, component_id: str, version: str, change_type: str, description: str):
        entry = ChangelogEntry(
            entry_id=f"cl_{uuid.uuid4().hex[:12]}",
            component_id=component_id,
            version=version,
            change_type=change_type,
            description=description,
        )
        self._changelog.append(entry)
        self._sync_changelog(entry)  # RV-①b-⑭-1 追加写穿（append 表语义）

    def get_changelog(self, component_id: str = None, limit: int = 50) -> list[dict]:
        """Get changelog entries."""
        self._ensure_loaded()
        entries = self._changelog
        if component_id:
            entries = [e for e in entries if e.component_id == component_id]
        entries = sorted(entries, key=lambda e: e.timestamp, reverse=True)[:limit]
        return [
            {
                "entry_id": e.entry_id,
                "component_id": e.component_id,
                "version": e.version,
                "change_type": e.change_type,
                "description": e.description,
                "author": e.author,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    # ── Platform Version ──────────────────────────────────

    def get_platform_version(self) -> dict:
        """Get overall platform version info."""
        self._ensure_loaded()
        stats = self.get_stats()
        return {
            "platform_version": self._platform_version,
            "total_components": stats["total_components"],
            "total_migrations": stats["total_migrations"],
            "total_changelog_entries": stats["total_changelog_entries"],
            "components": self.list_components(),
        }

    def bump_platform_version(self, bump_type: str = "patch") -> str:
        """Bump platform version (major/minor/patch)."""
        self._ensure_loaded()
        parts = self._platform_version.split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if bump_type == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump_type == "minor":
            minor += 1
            patch = 0
        else:
            patch += 1
        self._platform_version = f"{major}.{minor}.{patch}"
        self._add_changelog(
            "platform",
            self._platform_version,
            "feature",
            f"Platform bumped to {self._platform_version}",
        )
        self._sync_platform()  # RV-①b-⑭-1 标量写穿（meta kv）
        return self._platform_version

    # ── Stats ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        self._ensure_loaded()
        if self._persist is not None:
            # RV-①b-⑭-1 计数恒对账行聚合（MEM-⑥ 行真源：写失败窗口行滞后内存
            # 以行为准=重启后续算口径）；对账失败回落内存派生禁假 0。
            try:
                counters = self._persist.counters()
                return {
                    "total_components": counters["total_components"],
                    "total_versions": counters["total_versions"],
                    "version_statuses": counters["version_statuses"],
                    "total_migrations": counters["total_migrations"],
                    "migration_statuses": counters["migration_statuses"],
                    "total_changelog_entries": counters["total_changelog_entries"],
                    "platform_version": counters["platform_version"] or self._platform_version,
                }
            except Exception as e:
                logger.error(f"heredity 计数对账失败（回落内存派生，禁假 0）: {e}")
        statuses = {}
        for versions in self._components.values():
            for v in versions:
                statuses[v.status.value] = statuses.get(v.status.value, 0) + 1
        migration_statuses = {}
        for m in self._migrations:
            migration_statuses[m.status.value] = migration_statuses.get(m.status.value, 0) + 1
        return {
            "total_components": len(self._components),
            "total_versions": sum(len(v) for v in self._components.values()),
            "version_statuses": statuses,
            "total_migrations": len(self._migrations),
            "migration_statuses": migration_statuses,
            "total_changelog_entries": len(self._changelog),
            "platform_version": self._platform_version,
        }

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _version_matches(actual: str, constraint: str) -> bool:
        """Simple semver constraint matching. Supports: >=1.0.0, ~1.0, ^1.0.0, =1.0.0."""
        if constraint.startswith(">="):
            return VersionRegistry._parse_ver(actual) >= VersionRegistry._parse_ver(constraint[2:])
        if constraint.startswith("=") or constraint.startswith("=="):
            return actual == constraint.lstrip("=")
        if constraint.startswith("^"):
            # Compatible: same major
            return actual.split(".")[0] == constraint[1:].split(".")[0]
        if constraint.startswith("~"):
            # Same minor
            a_parts = actual.split(".")
            c_parts = constraint[1:].split(".")
            return a_parts[0] == c_parts[0] and a_parts[1] == c_parts[1]
        # Default: exact
        return actual == constraint

    @staticmethod
    def _parse_ver(v: str) -> tuple:
        parts = v.strip().split(".")
        return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))

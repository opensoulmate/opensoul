"""Schema Migration Engine — Handles knowledge base structure migrations."""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .migration_persist import MigrationPersistence

logger = logging.getLogger(__name__)


class MigrationType(StrEnum):
    KB_SCHEMA = "kb_schema"
    CONFIG = "config"
    PLUGIN = "plugin"
    DATA = "data"


@dataclass
class SchemaField:
    name: str
    field_type: str  # string, number, boolean, list, dict, vector
    required: bool = True
    default: object = None
    description: str = ""


@dataclass
class SchemaVersion:
    schema_id: str
    component_id: str
    version: str
    fields: list[SchemaField] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class SchemaDiff:
    added: list[SchemaField] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)  # {name, old_type, new_type}


@dataclass
class MigrationScript:
    script_id: str
    migration_type: MigrationType
    component_id: str
    from_version: str
    to_version: str
    up_sql: str = ""  # forward migration
    down_sql: str = ""  # rollback
    transform: str = ""  # Python transform function name
    created_at: float = field(default_factory=time.time)
    applied: bool = False
    applied_at: float | None = None


class MigrationEngine:
    """Engine for managing schema migrations and data transforms.

    RV-①b-⑭-2：注册 schema/迁移脚本 applied 状态两面持久化——persist 缺省
    None=纯内存零变化；变更点 _sync_schema/_sync_script 写穿单一点+首触懒回载
    （先载后录、只补缺不覆盖）。细节/诚实边界见 migration_persist.py。
    """

    def __init__(self, persist: MigrationPersistence | None = None):
        self._schemas: dict[str, list[SchemaVersion]] = {}  # component_id -> versions
        self._scripts: list[MigrationScript] = []
        # ── RV-①b-⑭-2 两面持久化（persist 缺省 None=纯内存零变化）──
        self._persist = persist
        self._migration_loaded = False
        self._loading = False

    def _ensure_loaded(self) -> None:
        """首触懒回载（RV-①b-⑭-1 _ensure_loaded 同款）：读/写双首触——写口不回载=
        回载旧行冲掉在录新态（⑦-3 纪律）。先载后录（回载先于一切变更）+只补缺不
        覆盖（schema_id/script_id 精确行身份，内存在册更真禁回载洗内存新态，⑫ 纪律）；
        回载失败不置位下次触碰重试（migration_persist 诚实边界②）。坏行（type 枚举/
        字段不可解）跳过+logger.error 可见（persist 层 JSON 坏行+本层枚举坏行双防线）。
        """
        if self._migration_loaded or self._loading or self._persist is None:
            return
        self._loading = True
        try:
            load_ok = True
            try:
                schema_rows = self._persist.load_schemas()
                script_rows = self._persist.load_scripts()
            except Exception as e:
                logger.error(f"heredity 迁移引擎回载失败（空引擎起步，下次触碰重试）: {e}")
                schema_rows, script_rows = [], []
                load_ok = False
            seen_schema = {sv.schema_id for versions in self._schemas.values() for sv in versions}
            for row in schema_rows:
                if row["schema_id"] in seen_schema:
                    continue
                try:
                    fields = [SchemaField(**f) for f in row["fields"]]
                    sv = SchemaVersion(
                        schema_id=row["schema_id"],
                        component_id=row["component_id"],
                        version=row["version"],
                        fields=fields,
                        created_at=row["created_at"],
                    )
                except Exception as e:
                    logger.error(
                        f"heredity schema 坏行跳过（schema_id={row.get('schema_id')}，字段不可解）: {e}"
                    )
                    continue
                seen_schema.add(sv.schema_id)
                self._schemas.setdefault(sv.component_id, []).append(sv)
            seen_script = {s.script_id for s in self._scripts}
            for row in script_rows:
                if row["script_id"] in seen_script:
                    continue
                try:
                    script = MigrationScript(
                        script_id=row["script_id"],
                        migration_type=MigrationType(row["migration_type"]),
                        component_id=row["component_id"],
                        from_version=row["from_version"],
                        to_version=row["to_version"],
                        up_sql=row["up_sql"],
                        down_sql=row["down_sql"],
                        transform=row["transform"],
                        created_at=row["created_at"],
                        applied=row["applied"],
                        applied_at=row["applied_at"],
                    )
                except Exception as e:
                    logger.error(
                        f"heredity 迁移脚本坏行跳过（script_id={row.get('script_id')}，类型不可解）: {e}"
                    )
                    continue
                seen_script.add(script.script_id)
                self._scripts.append(script)
            # 回载失败不置位=下次触碰重试（只补缺防重试双行，见上）
            self._migration_loaded = load_ok
        finally:
            self._loading = False

    def _sync_schema(self, sv: SchemaVersion) -> None:
        """schema 行写穿单一点（register_schema 唯一变更点共用禁散落写点）：记账旁路
        ——写失败 logger.error 可见不阻断注册动作本体（migration_persist 诚实边界①）。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert_schema(
                {
                    "schema_id": sv.schema_id,
                    "component_id": sv.component_id,
                    "version": sv.version,
                    "fields": [
                        {
                            "name": f.name,
                            "field_type": f.field_type,
                            "required": f.required,
                            "default": f.default,
                            "description": f.description,
                        }
                        for f in sv.fields
                    ],
                    "created_at": sv.created_at,
                }
            )
        except Exception as e:
            logger.error(f"heredity schema 落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _sync_script(self, script: MigrationScript) -> None:
        """迁移脚本行写穿单一点（create/apply 两变更点共用）：applied 状态随影更新。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert_script(
                {
                    "script_id": script.script_id,
                    "migration_type": script.migration_type.value,
                    "component_id": script.component_id,
                    "from_version": script.from_version,
                    "to_version": script.to_version,
                    "up_sql": script.up_sql,
                    "down_sql": script.down_sql,
                    "transform": script.transform,
                    "created_at": script.created_at,
                    "applied": script.applied,
                    "applied_at": script.applied_at,
                }
            )
        except Exception as e:
            logger.error(f"heredity 迁移脚本落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def register_schema(self, component_id: str, version: str, fields: list[dict]) -> SchemaVersion:
        """Register a new schema version for a component."""
        self._ensure_loaded()  # RV-①b-⑭-2 先载后录（写首触同款，⑦-3 纪律）
        schema_fields = [SchemaField(**f) for f in fields]
        sv = SchemaVersion(
            schema_id=f"sch_{uuid.uuid4().hex[:12]}",
            component_id=component_id,
            version=version,
            fields=schema_fields,
        )
        if component_id not in self._schemas:
            self._schemas[component_id] = []
        self._schemas[component_id].append(sv)
        self._sync_schema(sv)  # RV-①b-⑭-2 新 schema 行写穿
        return sv

    def get_schema(self, component_id: str, version: str = None) -> SchemaVersion | None:
        """Get a schema version (latest if version not specified)."""
        self._ensure_loaded()
        versions = self._schemas.get(component_id, [])
        if not versions:
            return None
        if version:
            for v in versions:
                if v.version == version:
                    return v
            return None
        return versions[-1]

    def diff_schemas(self, component_id: str, from_version: str, to_version: str) -> SchemaDiff:
        """Compute diff between two schema versions."""
        from_schema = self.get_schema(component_id, from_version)
        to_schema = self.get_schema(component_id, to_version)
        if not from_schema or not to_schema:
            return SchemaDiff()

        from_fields = {f.name: f for f in from_schema.fields}
        to_fields = {f.name: f for f in to_schema.fields}

        added = [f for name, f in to_fields.items() if name not in from_fields]
        removed = [name for name in from_fields if name not in to_fields]
        modified = []
        for name in from_fields:
            if name in to_fields and from_fields[name].field_type != to_fields[name].field_type:
                modified.append(
                    {
                        "name": name,
                        "old_type": from_fields[name].field_type,
                        "new_type": to_fields[name].field_type,
                    }
                )

        return SchemaDiff(added=added, removed=removed, modified=modified)

    def create_migration_script(
        self,
        migration_type: str,
        component_id: str,
        from_version: str,
        to_version: str,
        up_sql: str = "",
        down_sql: str = "",
        transform: str = "",
    ) -> MigrationScript:
        """Create a migration script."""
        self._ensure_loaded()  # RV-①b-⑭-2 先载后录
        script = MigrationScript(
            script_id=f"ms_{uuid.uuid4().hex[:12]}",
            migration_type=MigrationType(migration_type),
            component_id=component_id,
            from_version=from_version,
            to_version=to_version,
            up_sql=up_sql,
            down_sql=down_sql,
            transform=transform,
        )
        self._sync_script(script)  # RV-①b-⑭-2 新脚本行写穿
        self._scripts.append(script)
        return script

    def apply_script(self, script_id: str) -> MigrationScript:
        """Mark a migration script as applied."""
        self._ensure_loaded()
        for s in self._scripts:
            if s.script_id == script_id:
                s.applied = True
                s.applied_at = time.time()
                self._sync_script(s)  # RV-①b-⑭-2 applied 状态随影落行
                return s
        raise ValueError(f"Script {script_id} not found")

    def list_scripts(self, component_id: str = None, applied: bool = None) -> list[dict]:
        """List migration scripts."""
        self._ensure_loaded()
        scripts = self._scripts
        if component_id:
            scripts = [s for s in scripts if s.component_id == component_id]
        if applied is not None:
            scripts = [s for s in scripts if s.applied == applied]
        return [
            {
                "script_id": s.script_id,
                "migration_type": s.migration_type.value,
                "component_id": s.component_id,
                "from_version": s.from_version,
                "to_version": s.to_version,
                "applied": s.applied,
                "applied_at": s.applied_at,
                "created_at": s.created_at,
            }
            for s in scripts
        ]

    def get_stats(self) -> dict:
        self._ensure_loaded()
        if self._persist is not None:
            # RV-①b-⑭-2 计数恒对账行聚合（MEM-⑥ 行真源：写失败窗口行滞后内存
            # 以行为准=重启后续算口径）；对账失败回落内存派生禁假 0。
            try:
                return dict(self._persist.counters())
            except Exception as e:
                logger.error(f"heredity 迁移引擎计数对账失败（回落内存派生，禁假 0）: {e}")
        total_schemas = sum(len(v) for v in self._schemas.values())
        applied = sum(1 for s in self._scripts if s.applied)
        return {
            "total_components": len(self._schemas),
            "total_schema_versions": total_schemas,
            "total_scripts": len(self._scripts),
            "applied_scripts": applied,
            "pending_scripts": len(self._scripts) - applied,
        }

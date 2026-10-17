"""Schema Migration Engine — Handles knowledge base structure migrations."""

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


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
    """Engine for managing schema migrations and data transforms."""

    def __init__(self):
        self._schemas: dict[str, list[SchemaVersion]] = {}  # component_id -> versions
        self._scripts: list[MigrationScript] = []

    def register_schema(self, component_id: str, version: str, fields: list[dict]) -> SchemaVersion:
        """Register a new schema version for a component."""
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
        return sv

    def get_schema(self, component_id: str, version: str = None) -> SchemaVersion | None:
        """Get a schema version (latest if version not specified)."""
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
        self._scripts.append(script)
        return script

    def apply_script(self, script_id: str) -> MigrationScript:
        """Mark a migration script as applied."""
        for s in self._scripts:
            if s.script_id == script_id:
                s.applied = True
                s.applied_at = time.time()
                return s
        raise ValueError(f"Script {script_id} not found")

    def list_scripts(self, component_id: str = None, applied: bool = None) -> list[dict]:
        """List migration scripts."""
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
        total_schemas = sum(len(v) for v in self._schemas.values())
        applied = sum(1 for s in self._scripts if s.applied)
        return {
            "total_components": len(self._schemas),
            "total_schema_versions": total_schemas,
            "total_scripts": len(self._scripts),
            "applied_scripts": applied,
            "pending_scripts": len(self._scripts) - applied,
        }

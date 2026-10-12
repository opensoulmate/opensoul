"""Enterprise features: authentication, RBAC, and audit logging.

Uses a self-contained SQLite database so this module can be added
without touching existing project files.
"""

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from src.config import settings
from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace
from src.services.tenant_binding import ensure_user_tenant_columns, validate_tenant

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])


@router.get("/health")
async def enterprise_health():
    """Enterprise health check."""
    return {"status": "ok", "component": "Enterprise"}


security = HTTPBearer()

# ── SQLite database ─────────────────────────────────────────────────────

_DB_PATH = Path(
    os.environ.get("ENTERPRISE_DB")
    or Path(__file__).parent.parent.parent / "data" / "enterprise.db"
)
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    UNIQUE NOT NULL,
                password    TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                tenant_id   TEXT    NOT NULL DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS roles (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER NOT NULL REFERENCES users(id),
                role_id INTEGER NOT NULL REFERENCES roles(id),
                PRIMARY KEY (user_id, role_id)
            );

            CREATE TABLE IF NOT EXISTS permissions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id   INTEGER NOT NULL REFERENCES roles(id),
                resource  TEXT    NOT NULL,
                action    TEXT    NOT NULL,
                UNIQUE(role_id, resource, action)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                action     TEXT    NOT NULL,
                resource   TEXT    NOT NULL,
                detail     TEXT,
                ip         TEXT,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                tenant_id  TEXT    NOT NULL DEFAULT 'default'
            );
        """)
        # F3b-③c-2②c：老库缺列自愈（DDL IF NOT EXISTS 不给既有表补列；幂等）。
        healed_audit = False
        for table in ("users", "audit_log"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "tenant_id" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                )
                if table == "audit_log":
                    healed_audit = True
        if healed_audit:
            # 行归属回填=审计行随 users 行归属租户（user_id 关联不到→'default'）。
            # 仅迁移时跑一次：随写落盘的租户键有粘性（COALESCE 粘性同款），
            # 重跑会把改绑用户的旧审计行洗回，故不做每次启动的幂等 UPDATE。
            conn.execute(
                "UPDATE audit_log SET tenant_id = COALESCE("
                "(SELECT u.tenant_id FROM users u WHERE u.id = audit_log.user_id), 'default')"
            )
        # 租户索引必须在补列之后建（③c-2① 坑：老库形态先建索引=no such column）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id)")


_init_db()


# ── Helpers ─────────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    import bcrypt as _bcrypt

    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, stored: str) -> bool:
    import bcrypt as _bcrypt

    try:
        return _bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False


def _create_token(user_id: int, username: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "username": username,
        "scope": "enterprise",
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("scope") != "enterprise":
            return None
        return payload
    except (JWTError, KeyError, ValueError):
        return None


def _get_user_permissions(user_id: int) -> set[tuple[str, str]]:
    """Return {(resource, action), ...} for the given user."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT p.resource, p.action
            FROM permissions p
            JOIN user_roles ur ON ur.role_id = p.role_id
            WHERE ur.user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return {(r["resource"], r["action"]) for r in rows}


def _audit_tenant(user_id: int | None, explicit: str | None = None) -> str:
    """审计行租户键派生单一源=users.tenant_id 行（F3b-③c-2②c）。

    显式键优先（登录/注册等行租户已知处直传）；否则按 user_id 查 users 行归属，
    关联不到（匿名登录失败/未知用户）→'default' 兜底——键派生故障绝不吃掉落库
    （③b-2 旁路纪律）。
    """
    val = str(explicit or "").strip()
    if val:
        return val
    if user_id is not None:
        with _db() as conn:
            row = conn.execute("SELECT tenant_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and str(row["tenant_id"] or "").strip():
            return str(row["tenant_id"]).strip()
    return "default"


def _record_audit(
    user_id: int | None,
    action: str,
    resource: str,
    detail: str = "",
    ip: str = "",
    tenant_id: str | None = None,
):
    tenant_val = _audit_tenant(user_id, tenant_id)
    with _db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, resource, detail, ip, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, action, resource, detail, ip, tenant_val),
        )


# ── 租户隔离闸门（F3b-③c-2②c）────────────────────────────────────────

_isolation_cache: tuple[str, IsolationEngine] | None = None


class EnterpriseAccessDenied(Exception):
    """跨租户访问被 IsolationEngine 拒绝（API 层映射 403 带真因，禁静默 not_found/假成功）。"""

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


def _iso() -> IsolationEngine:
    """隔离引擎（check_access 真执行点；审计落 NestStore=NEST_DB 或 data/nest.db）。

    按 NEST_DB env 值缓存：测试 env 切换自动换引擎（ai_groups _iso 同款），
    默认实例即真闸门，无"未接线"模式。
    """
    global _isolation_cache
    key = os.environ.get("NEST_DB") or ""
    if _isolation_cache is None or _isolation_cache[0] != key:
        _isolation_cache = (key, IsolationEngine())
    return _isolation_cache[1]


def _gate(tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
    """by-ID 访问闸门：IsolationEngine.check_access 真执行。

    资源 ID=namespaced_id(行归属租户 ns, user_id)——资源 namespace 前缀是行归属
    租户，比较基准是访问者派生租户；namespace 不匹配=跨租户冒读/冒写，引擎真拒绝
    （allowed=False）→ EnterpriseAccessDenied 上抛（API 层映射 403）。允许/拒绝都进
    引擎审计日志（audit_access=True）。
    """
    iso = _iso()
    verdict = iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=ResourceType.ENTERPRISE_USER,
        resource_id=iso.namespaced_id(tenant_namespace(row_tenant), resource_id),
        action=action,
    )
    if not verdict.get("allowed"):
        raise EnterpriseAccessDenied(verdict.get("reason", "access denied"), verdict)


def _gate_bulk(tenant_id: str, resource_type: ResourceType, action: str) -> None:
    """bulk 操作（users/list、audit 查询）审计留痕：执行隔离由 WHERE 租户过滤承担，
    引擎侧记一次 namespace 自匹配访问（audit_access=True「Log all access attempts」），
    使数据面访问在 /api/nest 隔离审计里可见。
    """
    iso = _iso()
    iso.check_access(
        tenant_namespace=tenant_namespace(tenant_id),
        resource_type=resource_type,
        resource_id=iso.namespaced_id(tenant_namespace(tenant_id), "*"),
        action=action,
    )


# ── Pydantic models ────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(LoginRequest):
    """注册扩展（F3b-② 认证→租户绑定）：用户行携带租户绑定，
    token 身份派生的单一真源就是 users.tenant_id。"""

    tenant_id: str = "default"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


class RoleCreate(BaseModel):
    name: str


class PermissionAssign(BaseModel):
    role_name: str
    resource: str
    action: str


class UserListItem(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: str
    roles: list[str]
    tenant_id: str = "default"  # F3b-③c-2②c 加性契约：行归属可观测


class AuditEntry(BaseModel):
    id: int
    user_id: int | None
    action: str
    resource: str
    detail: str | None
    ip: str | None
    created_at: str
    tenant_id: str = "default"  # F3b-③c-2②c 加性契约：行归属可观测


# ── Auth dependency ─────────────────────────────────────────────────────


async def get_current_enterprise_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Decode JWT and return user info dict."""
    payload = _decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = int(payload["sub"])
    with _db() as conn:
        row = conn.execute(
            "SELECT id, username, is_active, tenant_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or not row["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    # tenant_id 从 users 行派生（F3b-② 单一真源；F3b-③c-2②c 起流入数据面闸门/过滤）
    return {
        "id": row["id"],
        "username": row["username"],
        "tenant_id": str(row["tenant_id"] or "").strip() or "default",
    }


def require_permission(resource: str, action: str):
    """Dependency factory — raises 403 if the user lacks the given permission."""

    async def _check(user: dict = Depends(get_current_enterprise_user)):
        perms = _get_user_permissions(user["id"])
        if (resource, action) not in perms and ("*", "*") not in perms:
            raise HTTPException(status_code=403, detail=f"Permission denied: {resource}:{action}")
        return user

    return _check


# ── Audit middleware helper ─────────────────────────────────────────────


def audit_write(action: str, resource: str):
    """Call this inside handlers to record an audit entry."""

    def _record(user_id: int | None, detail: str = "", ip: str = "", tenant_id: str | None = None):
        _record_audit(user_id, action, resource, detail, ip, tenant_id)

    return _record


# ── Routes: Authentication ─────────────────────────────────────────────


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    """Authenticate with username/password and return a JWT token.

    LDAP integration point: replace the local DB check with an LDAP bind
    when `settings.ldap_url` is configured.
    """
    # ── LDAP placeholder ──
    # if settings.ldap_url:
    #     user = ldap_authenticate(body.username, body.password)
    # else:
    #     user = local_authenticate(...)
    with _db() as conn:
        row = conn.execute(
            "SELECT id, username, password, is_active, tenant_id FROM users WHERE username = ?",
            (body.username,),
        ).fetchone()
    if not row or not row["is_active"]:
        _record_audit(
            None,
            "login_failed",
            "auth",
            f"username={body.username}",
            request.client.host,
            tenant_id=(row["tenant_id"] if row else None),
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not _verify_password(body.password, row["password"]):
        _record_audit(
            row["id"], "login_failed", "auth", "", request.client.host, tenant_id=row["tenant_id"]
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(row["id"], row["username"])
    _record_audit(row["id"], "login", "auth", "", request.client.host, tenant_id=row["tenant_id"])
    return LoginResponse(access_token=token, user_id=row["id"], username=row["username"])


@router.post("/auth/register")
async def register(body: RegisterRequest, request: Request):
    """Register a new enterprise user (for bootstrap; disable in production).

    tenant_id（F3b-②）：写入 users.tenant_id 绑定（认证身份派生真源），必须是
    已在 /api/nest/tenants 登记的租户（'default' 恒可用）——禁悬空绑定。
    """
    try:
        tenant_id = validate_tenant(body.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ensure_user_tenant_columns(str(_DB_PATH))
    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password, tenant_id) VALUES (?, ?, ?)",
                (body.username, _hash_password(body.password), tenant_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Username already exists")
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    _record_audit(
        user_id,
        "register",
        "auth",
        f"tenant={tenant_id}",
        request.client.host if request.client else "",
        tenant_id=tenant_id,
    )
    return {"ok": True, "user_id": user_id, "tenant_id": tenant_id}


# ── Routes: RBAC ────────────────────────────────────────────────────────


@router.post("/roles")
async def create_role(
    body: RoleCreate,
    request: Request,
    user: dict = Depends(require_permission("roles", "create")),
):
    """Create a new role."""
    with _db() as conn:
        try:
            conn.execute("INSERT INTO roles (name) VALUES (?)", (body.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Role already exists")
    _record_audit(
        user["id"],
        "create",
        "roles",
        f"name={body.name}",
        request.client.host,
        tenant_id=user["tenant_id"],
    )
    return {"ok": True, "role": body.name}


@router.post("/permissions")
async def assign_permission(
    body: PermissionAssign,
    request: Request,
    user: dict = Depends(require_permission("permissions", "assign")),
):
    """Assign a permission (resource + action) to a role."""
    with _db() as conn:
        role = conn.execute("SELECT id FROM roles WHERE name = ?", (body.role_name,)).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        try:
            conn.execute(
                "INSERT INTO permissions (role_id, resource, action) VALUES (?, ?, ?)",
                (role["id"], body.resource, body.action),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Permission already assigned")
    _record_audit(
        user["id"],
        "assign",
        "permissions",
        f"role={body.role_name} resource={body.resource} action={body.action}",
        request.client.host,
        tenant_id=user["tenant_id"],
    )
    return {"ok": True}


@router.post("/users/{user_id}/roles")
async def assign_role_to_user(
    user_id: int,
    body: RoleCreate,
    request: Request,
    user: dict = Depends(require_permission("users", "manage")),
):
    """Assign a role to a user.

    F3b-③c-2②c：目标用户行按租户闸门（by-ID gate）——跨租户禁绑，403 带真因。
    """
    with _db() as conn:
        target = conn.execute("SELECT id, tenant_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            _gate(
                user["tenant_id"],
                str(target["tenant_id"] or "").strip() or "default",
                str(user_id),
                "write",
            )
        except EnterpriseAccessDenied as e:
            raise HTTPException(status_code=403, detail=f"cross-tenant access denied: {e.reason}")
        role = conn.execute("SELECT id FROM roles WHERE name = ?", (body.name,)).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        try:
            conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role["id"]),
            )
        except sqlite3.IntegrityError:
            pass  # already assigned
    _record_audit(
        user["id"],
        "assign_role",
        "users",
        f"target_user={user_id} role={body.name}",
        request.client.host,
        tenant_id=user["tenant_id"],
    )
    return {"ok": True}


@router.get("/users/list", response_model=list[UserListItem])
async def list_users(user: dict = Depends(require_permission("users", "list"))):
    """List enterprise users with their roles.

    F3b-③c-2②c：恒按请求者租户过滤（租户只见自己的用户行，修前全量列表=跨租户
    身份泄露），bulk 访问走隔离引擎审计留痕。
    """
    _gate_bulk(user["tenant_id"], ResourceType.ENTERPRISE_USER, "list")
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, username, is_active, created_at, tenant_id FROM users "
            "WHERE tenant_id = ? ORDER BY id",
            (user["tenant_id"],),
        ).fetchall()
        result = []
        for r in rows:
            roles = conn.execute(
                """
                SELECT ro.name FROM roles ro
                JOIN user_roles ur ON ur.role_id = ro.id
                WHERE ur.user_id = ?
                """,
                (r["id"],),
            ).fetchall()
            result.append(
                UserListItem(
                    id=r["id"],
                    username=r["username"],
                    is_active=bool(r["is_active"]),
                    created_at=r["created_at"],
                    roles=[ro["name"] for ro in roles],
                    tenant_id=str(r["tenant_id"] or "").strip() or "default",
                )
            )
    return result


# ── Routes: Audit log ───────────────────────────────────────────────────


@router.get("/audit", response_model=list[AuditEntry])
async def get_audit_log(
    user: dict = Depends(require_permission("audit", "read")),
    action: str | None = None,
    resource: str | None = None,
    user_id: int | None = None,
    limit: int = Query(default=100, le=1000),
):
    """Query audit log with optional filters.

    F3b-③c-2②c：恒按请求者租户过滤（修前全租户审计可见=跨租户行为泄露），bulk
    访问走隔离引擎审计留痕。
    """
    _gate_bulk(user["tenant_id"], ResourceType.ENTERPRISE_AUDIT, "list")
    clauses = ["tenant_id = ?"]
    params: list = [user["tenant_id"]]
    if action:
        clauses.append("action = ?")
        params.append(action)
    if resource:
        clauses.append("resource = ?")
        params.append(resource)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)

    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)

    with _db() as conn:
        rows = conn.execute(
            f"SELECT id, user_id, action, resource, detail, ip, created_at, tenant_id "
            f"FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()

    return [AuditEntry(**dict(r)) for r in rows]

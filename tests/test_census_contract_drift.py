"""CD-① 契约漂移普查工具回归测试（tmp 夹具全隔离，零生产接触）。

核心能力证明=GLAND-⑤ trajectory 真 bug 夹具：修前消费形必须被工具抓成 drift_hard，
修后消费形必须全 produced——工具若退化（漏报）此两例必炸。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "scripts" / "census_contract_drift.py"
spec = importlib.util.spec_from_file_location("census_contract_drift", _TOOL)
assert spec is not None and spec.loader is not None
cdc = importlib.util.module_from_spec(spec)
sys.modules["census_contract_drift"] = (
    cdc  # dataclass 装饰需模块在 sys.modules（否则 __dict__ None）
)
spec.loader.exec_module(cdc)


# ── 夹具仓库构建 ──────────────────────────────────────────────

MAIN_PY = """
from fastapi import FastAPI
from src.api.traj import router as traj_router

app = FastAPI()
app.include_router(traj_router, prefix="/api/trajectory", tags=["trajectory"])
"""

TRAJ_API = """
from src.traj.store import TrajectorySession, trajectory_store

router = APIRouter()


@router.get("/sessions")
async def list_sessions():
    sessions = await trajectory_store.list_sessions()
    return {"sessions": [TrajectorySession(id=s) for s in sessions], "total": len(sessions)}
"""

STORE_PY = """
class TrajectorySession:
    id: str
    agent_id: str
    task_description: str
    tags: list
    status: str
    total_events: int
    created_at: str
    ended_at: str | None

    def to_dict(self):
        return {
            "id": "x",
            "agent_id": "a",
            "task_description": "t",
            "tags": [],
            "status": "running",
            "total_events": 0,
            "created_at": "now",
            "ended_at": None,
        }
"""

UI_PRE_FIX = """
'use client';
export default function P() {
  const load = async () => {
    const data = await apiFetch('/api/trajectory/sessions');
    setSessions(data.sessions);
    const t = data.total;
  };
  const rows = sessions.map((s) => s.session_id + s.event_count + s.updated_at);
  return <div>{rows}</div>;
}
"""

UI_POST_FIX = """
'use client';
export default function P() {
  const load = async () => {
    const data = await apiFetch('/api/trajectory/sessions');
    setSessions(data.sessions);
    const t = data.total;
  };
  const rows = sessions.map((s) => s.id + s.total_events + s.created_at);
  return <div>{rows}</div>;
}
"""


def build_repo(
    tmp_path: Path, api_src: str = TRAJ_API, store_src: str = STORE_PY
) -> tuple[Path, Path]:
    src_root = tmp_path / "src"
    (src_root / "api").mkdir(parents=True)
    (src_root / "traj").mkdir()
    (src_root / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (src_root / "api" / "traj.py").write_text(api_src, encoding="utf-8")
    (src_root / "traj" / "store.py").write_text(store_src, encoding="utf-8")
    ui_root = tmp_path / "ui"
    ui_root.mkdir()
    return src_root, ui_root


def run_compare(tmp_path: Path, ui_code: str, **kw) -> dict:
    src_root, ui_root = build_repo(tmp_path, **kw)
    (ui_root / "page.tsx").write_text(ui_code, encoding="utf-8")
    return cdc.compare(ui_root, src_root)


# ── GLAND-⑤ trajectory 回归（工具能力证明）────────────────────


def test_pre_fix_drift_is_caught(tmp_path):
    """修前消费形（session_id/event_count/updated_at）必须被抓成元素级 drift_hard。"""
    r = run_compare(tmp_path, UI_PRE_FIX)
    verdicts = {tuple(f["key"]): f["verdict"] for f in r["findings"]}
    for key in (
        ("sessions", "session_id"),
        ("sessions", "event_count"),
        ("sessions", "updated_at"),
    ):
        assert verdicts[key] == "drift_hard", f"{key} 应为 drift_hard，实得 {verdicts[key]}"


def test_post_fix_keys_all_produced(tmp_path):
    """修后消费形（id/total_events/created_at）必须全 produced——零误报。"""
    r = run_compare(tmp_path, UI_POST_FIX)
    for f in r["findings"]:
        assert f["verdict"] == "produced", f"{f['key']} 误报 {f['verdict']}"


def test_pre_fix_top_keys_produced(tmp_path):
    """顶层键 sessions/total 是真源键，禁与元素级漂移混判。"""
    r = run_compare(tmp_path, UI_PRE_FIX)
    verdicts = {tuple(f["key"]): f["verdict"] for f in r["findings"]}
    assert verdicts[("sessions",)] == "produced"
    assert verdicts[("total",)] == "produced"


# ── 消费面提取精度 ────────────────────────────────────────────


def test_builtin_members_not_keys(tmp_path):
    """JS 内建成员（length/map/find…）禁误收为响应键。"""
    ui = """
    const data = await apiFetch('/api/trajectory/sessions');
    const n = data.length;
    const m = data.sessions.map((s) => s.id);
    const f = data.sessions.find((s) => s.id);
    """
    r = run_compare(tmp_path, ui)
    keys = {tuple(f["key"]) for f in r["findings"]}
    assert not any(k[-1] in ("length", "map", "find") for k in keys), keys


def test_path_normalize_param_vs_query_tail(tmp_path):
    """/${id}=路径参数→{}；${qs} 尾拼接=query 尾参数→路径截断。"""
    assert cdc._normalize_path("/api/voice/profiles/${profileId}") == "/api/voice/profiles/{}"
    assert cdc._normalize_path("/api/limb/tasks${params}") == "/api/limb/tasks"
    assert cdc._normalize_path("/api/trajectory/search?${params}") == "/api/trajectory/search"
    assert cdc._normalize_path("/api/x/y/?q=1") == "/api/x/y"


def test_dynamic_path_unresolved_not_empty_match(tmp_path):
    """路径非字面量=unresolved_path，禁按空串匹配到任何端点。"""
    ui = """
    const data = await apiFetch(endpoint);
    const s = data.sources;
    const d2 = await apiFetch(`/api/thing/${x}/sub`);
    const k = d2.key1;
    """
    r = run_compare(tmp_path, ui)
    by_key = {tuple(f["key"]): f for f in r["findings"]}
    assert by_key[("sources",)]["verdict"] == "unresolved_path"
    assert by_key[("key1",)]["verdict"] == "unresolved_endpoint"  # /api/thing/{}/sub 不在夹具索引


def test_assignment_segment_scope(tmp_path):
    """同名变量再次赋值后旧段消费禁混入（data 赋值切段）。"""
    ui = """
    const data = await apiFetch('/api/trajectory/sessions');
    const a = data.sessions;
    const data = await apiFetch('/other');
    const b = data.notakey;
    """
    r = run_compare(tmp_path, ui)
    keys = {tuple(f["key"]) for f in r["findings"]}
    assert ("notakey",) in keys  # 第二段归属 /other（未映射）——不得算到 sessions 头上
    for f in r["findings"]:
        if tuple(f["key"]) == ("notakey",):
            assert f["path"] == "/other"


# ── 真源面提取精度 ────────────────────────────────────────────


def test_response_model_with_base_class(tmp_path):
    """response_model=模型含基类字段并入（EntityResponse(EntityBase) 回归形）。"""
    api = """
from src.traj.store import EntityResponse, entity_service

router = APIRouter()


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_one(entity_id: str):
    row = await entity_service.get_entity(entity_id)
    return row
"""
    store = """
class EntityBase:
    name: str
    description: str
    entity_type: str
    properties: dict


class EntityResponse(EntityBase):
    id: str
    user_id: str


entity_service = None
"""
    src_root, ui_root = build_repo(tmp_path, api_src=api, store_src=store)
    (ui_root / "page.tsx").write_text(
        """
        const d = await apiFetch(`/api/trajectory/${eid}`);
        const n = d.name;
        const b = d.entity_type;
        const x = d.id;
        const bad = d.nosuchkey;
        """,
        encoding="utf-8",
    )
    r = cdc.compare(ui_root, src_root)
    verdicts = {tuple(f["key"]): f["verdict"] for f in r["findings"]}
    assert verdicts[("name",)] == "produced"  # 基类字段
    assert verdicts[("entity_type",)] == "produced"
    assert verdicts[("id",)] == "produced"
    assert verdicts[("nosuchkey",)] == "drift_hard"


def test_cross_module_bare_return_is_not_hard(tmp_path):
    """跨模块 bare return=形状不可知，禁当全解析误定罪 drift_hard（诚实边界锁死）。"""
    api = """
router = APIRouter()


@router.post("/test")
async def test_connection(body: dict | None = None):
    result = await gateway.test_provider("p")
    return result
"""
    src_root, ui_root = build_repo(tmp_path, api_src=api)
    (ui_root / "page.tsx").write_text(
        """
        const d = await apiFetch('/api/trajectory/test', { method: 'POST' });
        const v = d.input;
        """,
        encoding="utf-8",
    )
    r = cdc.compare(ui_root, src_root)
    f = [x for x in r["findings"] if tuple(x["key"]) == ("input",)][0]
    assert f["verdict"] == "unresolved_shape"  # 待确认层，禁 drift_hard
    assert f["dynamic"] is True


def test_receiver_var_to_dict_is_honest_boundary(tmp_path):
    """s.to_dict() 接收者变量无法定型=形状不可知，禁硬判 drift_hard（诚实边界）。"""
    api = """
router = APIRouter()


@router.get("/sessions")
async def list_sessions():
    sessions = await store.list_sessions()
    return {"sessions": [s.to_dict() for s in sessions], "total": 0}
"""
    src_root, ui_root = build_repo(tmp_path, api_src=api)
    (ui_root / "page.tsx").write_text(
        """
        const d = await apiFetch('/api/trajectory/sessions');
        const bad = d.sessions.map((x) => x.session_id);
        """,
        encoding="utf-8",
    )
    r = cdc.compare(ui_root, src_root)
    for f in r["findings"]:
        assert f["verdict"] != "drift_hard", f"{f['key']} 不得硬判"


def test_sql_alias_and_helper_keys(tmp_path):
    """SQL 别名与 helper 显式键=真源键。"""
    api = """
router = APIRouter()


@router.get("/sessions")
async def list_sessions():
    rows = await db.fetch("SELECT session_id, COUNT(*) as event_count FROM t GROUP BY session_id")
    return {"sessions": [_row_to_dict(r) for r in rows], "ok": True}


def _row_to_dict(row):
    d = dict(row)
    d["tenant_id"] = "default"
    return d
"""
    src_root, ui_root = build_repo(tmp_path, api_src=api)
    (ui_root / "page.tsx").write_text(
        """
        const d = await apiFetch('/api/trajectory/sessions');
        const a = d.sessions;
        const b = d.ok;
        const c = d.tenant_id;
        const bad = d.nope;
        """,
        encoding="utf-8",
    )
    r = cdc.compare(ui_root, src_root)
    verdicts = {tuple(f["key"]): f["verdict"] for f in r["findings"]}
    assert verdicts[("ok",)] == "produced"
    assert verdicts[("tenant_id",)] == "produced"  # helper 显式键
    assert verdicts[("nope",)] in ("drift_hard", "drift_suspect")


# ── openapi 交叉核对 ──────────────────────────────────────────


def test_openapi_missing_path_flagged(tmp_path):
    """UI 打的端点不在挂载路由表=missing_in_openapi（路径级断点真源交叉）。"""
    src_root, ui_root = build_repo(tmp_path)
    (ui_root / "page.tsx").write_text(
        """
        const d = await apiFetch('/api/trajectory/sessions');
        const a = d.sessions;
        const g = await apiFetch('/api/permission/roles/all');
        const roles = g.roles;
        """,
        encoding="utf-8",
    )
    openapi = {
        "paths": {
            "/api/trajectory/sessions": {"get": {}},
            "/api/permission/roles/{username}": {"get": {}},
        }
    }
    r = cdc.compare(ui_root, src_root, openapi)
    by_key = {tuple(f["key"]): f for f in r["findings"]}
    assert by_key[("roles",)]["path_status"] == "missing_in_openapi"
    # 路径真源缺位时禁把键判成 produced/drift_hard（端点先断）
    assert by_key[("roles",)]["verdict"] == "unresolved_endpoint"
    assert by_key[("sessions",)]["path_status"] == "in_openapi"


def test_openapi_path_counts_present(tmp_path):
    """openapi 提供时 path_counts 必须有账（零漂移也如实记 0/全量）。"""
    ui = "const d = await apiFetch('/api/trajectory/sessions'); const a = d.sessions;"
    src_root, ui_root = build_repo(tmp_path)
    (ui_root / "page.tsx").write_text(ui, encoding="utf-8")
    openapi = {"paths": {"/api/trajectory/sessions": {"get": {}}}}
    r = cdc.compare(ui_root, src_root, openapi)
    assert r["path_counts"].get("in_openapi") == 1
    assert r["callsites"] == 1

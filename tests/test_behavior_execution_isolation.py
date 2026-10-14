"""PROACT-③ 行为执行面数据面隔离护栏——测试进程里默认行为回调的文件写入面
绝不写生产（PROACT-① 诚实边界「生产单例数据面另拍」执行件的防复发钉）。

修前实证（2026-10-14 探针，非推断）：TestClient(src.main) 单测试文件
（test_a2a_card_shape 12 例）跑完，生产 opensoul.db experiences +2 行
（source_ref=msg:2996/msg:2998）；live 服务 experience_collect 心跳节拍
（proactive_events：16:35:43 后至 17:35:43 无采集运行）证明写入方只能是
测试进程 lifespan tick 的默认行为回调——register_default_watchers 的
partial() 零注入缺省直指生产数据面。

本文件钉住四个文件写入面的隔离契约（live 并发写也干扰不了唯一标记判定）：
- experiences（experience_collect）：OPENSOUL_DB env 单一源 → 采集写入落
  env 库，生产 source_ref 标记行恒不存在
- learned_patterns（pattern_extract/advisory_review 默认池）：
  _default_learn_db_pool env 绑定 → 提取/推荐写入落 env 库，生产 marker key
  恒不存在
- ai_groups.db（advisory_review 上板/回群）：DB_PATH 归 tmp → 任务落 tmp，
  生产恒无 marker goal
- evolution.db（heredity_triggers 熔断/触发）：heredity 单例归 tmp → 熔断行
  落 tmp，生产恒无 marker error_type

memory_decay 纯内存无文件面（如实不设护栏）；experience_collect 的 job/eval
采集源仍只读生产（写入面已归 tmp，源面只读零写入）。
"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learn import experience_collector as ec_mod
from src.learn.long_term import LongTermLearning
from src.will import proactive_behaviors as pb_mod
from src.will.proactive import ProactiveEngine

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_OPENSOUL_DB = REPO_ROOT / "data" / "opensoul.db"
PROD_AI_GROUPS_DB = Path.home() / "opensoul" / "data" / "ai_groups.db"
PROD_EVOLUTION_DB = Path.home() / ".hermes" / "opensoul" / "evolution" / "evolution.db"


# 容缺取符号（控制组旧代码无此符号时按 AttributeError 炸在行为断言上，而非
# 收集期 ImportError——控制组语义内建，test_advisory_review.py 同款）。
def _missing(name: str):
    def _f(*args, **kwargs):
        raise AttributeError(f"old code has no {name}")

    return _f


_default_opensoul_db = getattr(ec_mod, "_default_opensoul_db", _missing("_default_opensoul_db"))
_default_learn_db_pool = getattr(
    pb_mod, "_default_learn_db_pool", _missing("_default_learn_db_pool")
)
experience_collect = getattr(pb_mod, "experience_collect", _missing("experience_collect"))
pattern_extract = getattr(pb_mod, "pattern_extract", _missing("pattern_extract"))
advisory_review = getattr(pb_mod, "advisory_review", _missing("advisory_review"))
heredity_triggers = getattr(pb_mod, "heredity_triggers", _missing("heredity_triggers"))
register_default_watchers = getattr(
    pb_mod, "register_default_watchers", _missing("register_default_watchers")
)

MARKER = "PROACT3_MARK"


def _env_db() -> Path:
    return Path(__import__("os").environ["OPENSOUL_DB"])


def _seed_message(db_path: Path, marker: str) -> str:
    """往 env 库播一条 assistant 源消息（体验采集源面），返回 source_ref 后缀 id。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
            """
        )
        cur = conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (f"{marker}-sess", "assistant", f"done {marker}", time.time()),
        )
        conn.commit()
        return str(cur.lastrowid)
    finally:
        conn.close()


def _seed_experiences(db_path: Path, marker: str, n: int = 3) -> None:
    """往 env 库播 n 条成功经验（file_combo 提取源面，生产路径 ensure_schema+_insert_exp）。"""
    collector = ec_mod.ExperienceCollector(db_path=str(db_path), job_db_path="", eval_db_path="")
    conn = sqlite3.connect(str(db_path))
    try:
        collector.ensure_schema(conn)
        for i in range(n):
            collector._insert_exp(
                conn,
                action=f"edit {marker}_a.py and {marker}_b.py iter{i}",
                intent_summary=f"{marker} intent",
                outcome="success",
                error="",
                created_at=time.time(),
                source_ref=f"{marker}-exp-{i}",
                metadata={"source": "test_seed"},
            )
        conn.commit()
    finally:
        conn.close()


def _count(db_path: Path, sql: str, args: tuple = ()) -> int:
    if not db_path.exists():
        return -1
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


# ── 1. 单一源解析：OPENSOUL_DB env 优先 ──────────────────────────────


def test_default_opensoul_db_env_first(monkeypatch, tmp_path):
    env_db = tmp_path / "envface.db"
    monkeypatch.setenv("OPENSOUL_DB", str(env_db))
    assert _default_opensoul_db() == str(env_db.resolve())


def test_default_opensoul_db_without_env_follows_settings(monkeypatch):
    monkeypatch.delenv("OPENSOUL_DB", raising=False)
    from src.config import settings

    url = settings.database_url
    expected = str(Path(url.replace("sqlite:///", "").replace("sqlite://", "")).resolve())
    assert _default_opensoul_db() == expected


# ── 2. experience_collect 缺省写入面归 env 库（修前实证泄漏面） ──────


def test_experience_collect_default_face_lands_in_env_db(monkeypatch):
    env_db = _env_db()
    msg_id = _seed_message(env_db, MARKER)
    res = asyncio.run(experience_collect())
    assert res.get("status") == "ok", res
    assert res.get("written", 0) >= 1, res
    assert (
        _count(env_db, "SELECT COUNT(*) FROM experiences WHERE source_ref=?", (f"msg:{msg_id}",))
        == 1
    ), "采集写入必须落 env 库"
    assert (
        _count(
            PROD_OPENSOUL_DB,
            "SELECT COUNT(*) FROM experiences WHERE source_ref=?",
            (f"msg:{msg_id}",),
        )
        == 0
    ), "生产 experiences 绝不许出现标记行（修前泄漏回归）"


# ── 3. 学习面默认池：env 绑定 / 无 env 走生产单例 ────────────────────


def test_learn_db_pool_env_binding_and_write_landing(monkeypatch):
    env_db = _env_db()
    pool = asyncio.run(_default_learn_db_pool())
    assert getattr(pool, "_db_path", None) == str(env_db.resolve()) or getattr(
        pool, "_db_path", None
    ) == str(env_db), f"默认池必须绑定 env 库，got {getattr(pool, '_db_path', None)}"

    async def _write():
        lt = LongTermLearning(pool, "default", "default")
        await lt._ensure_table()
        await lt._upsert_pattern("file_combo", f"{MARKER}-pool-key", {"files": []}, 0.5, 1)

    asyncio.run(_write())
    assert (
        _count(
            env_db,
            "SELECT COUNT(*) FROM learned_patterns WHERE pattern_key=?",
            (f"{MARKER}-pool-key",),
        )
        == 1
    )
    assert (
        _count(
            PROD_OPENSOUL_DB,
            "SELECT COUNT(*) FROM learned_patterns WHERE pattern_key=?",
            (f"{MARKER}-pool-key",),
        )
        == 0
    ), "生产 learned_patterns 绝不许出现标记行"


def test_learn_db_pool_without_env_returns_singleton(monkeypatch):
    monkeypatch.delenv("OPENSOUL_DB", raising=False)
    from src.database.postgres import db_pool

    pool = asyncio.run(_default_learn_db_pool())
    assert pool is db_pool, "无 env 时必须返回生产单例（行为字节不变）"


# ── 4. pattern_extract 缺省写入面归 env 库 ───────────────────────────


def test_pattern_extract_default_face_lands_in_env_db():
    env_db = _env_db()
    _seed_experiences(env_db, MARKER, n=3)
    asyncio.run(pattern_extract())
    combo_key = f"{MARKER}_a.py+{MARKER}_b.py"
    assert (
        _count(env_db, "SELECT COUNT(*) FROM learned_patterns WHERE pattern_key=?", (combo_key,))
        >= 1
    ), "提取产物必须落 env 库"
    assert (
        _count(
            PROD_OPENSOUL_DB,
            "SELECT COUNT(*) FROM learned_patterns WHERE pattern_key=?",
            (combo_key,),
        )
        == 0
    ), "生产 learned_patterns 绝不许出现标记 key（修前默认池=生产单例）"


# ── 5. advisory_review 缺省写入面归 tmp ai_groups.db ────────────────


def test_advisory_review_default_face_lands_in_tmp_ai_groups(monkeypatch):
    from src.api import ai_groups

    monkeypatch.setattr(ai_groups, "GROUP_MSG_PUSH", "http://127.0.0.1:9/disabled")
    ai_groups.init_db()
    conn = sqlite3.connect(str(ai_groups.DB_PATH))
    conn.execute(
        "INSERT INTO ai_groups (id,name,description,status,tenant_id,created_at) VALUES (?,?,?,?,?,?)",
        ("g-proact3", "隔离组", "", "active", "default", "2026-10-14T00:00:00"),
    )
    conn.commit()
    conn.close()

    _env_db()

    async def _seed_rec():
        pool = await _default_learn_db_pool()
        lt = LongTermLearning(pool, "default", "default")
        await lt._ensure_table()
        await lt._upsert_pattern(
            "failure", f"{MARKER}-rec-key", {"common_fixes": [f"{MARKER} fix"]}, 0.9, 10
        )

    asyncio.run(_seed_rec())
    res = asyncio.run(advisory_review())
    assert res.get("created", 0) >= 1, res
    goal_like = f"%{MARKER}-rec-key%"
    assert (
        _count(
            Path(ai_groups.DB_PATH),
            "SELECT COUNT(*) FROM ai_group_tasks WHERE goal LIKE ?",
            (goal_like,),
        )
        >= 1
    ), "上板任务必须落 tmp ai_groups.db"
    assert (
        _count(
            PROD_AI_GROUPS_DB, "SELECT COUNT(*) FROM ai_group_tasks WHERE goal LIKE ?", (goal_like,)
        )
        == 0
    ), "生产 ai_groups.db 绝不许出现标记任务（修前 DB_PATH 恒指生产）"


# ── 6. heredity_triggers 缺省写入面归 tmp evolution.db ──────────────


def test_heredity_triggers_default_face_lands_in_tmp_evolution(tmp_path):
    res = asyncio.run(heredity_triggers(recent_errors=[f"{MARKER}-err"] * 3))
    assert res.get("breaker_open") is True, res
    tmp_evo = tmp_path / "evolution.db"
    assert (
        _count(
            tmp_evo,
            "SELECT COUNT(*) FROM evolution_breaker WHERE error_type=?",
            (f"{MARKER}-err",),
        )
        >= 1
    ), "熔断行必须落 tmp evolution.db"
    assert (
        _count(
            PROD_EVOLUTION_DB,
            "SELECT COUNT(*) FROM evolution_breaker WHERE error_type=?",
            (f"{MARKER}-err",),
        )
        == 0
    ), "生产 evolution.db 绝不许出现标记熔断行"


# ── 7. 注册缝（修前泄漏真路径）：partial() 零注入缺省也归 tmp ──────


def test_registration_seam_defaults_land_in_env_db():
    env_db = _env_db()
    msg_id = _seed_message(env_db, f"{MARKER}-tick")
    engine = ProactiveEngine(db_path="")
    register_default_watchers(engine)
    r = asyncio.run(engine.run_watcher("experience_collect"))
    assert r.get("ok") is True, r
    assert (
        _count(env_db, "SELECT COUNT(*) FROM experiences WHERE source_ref=?", (f"msg:{msg_id}",))
        == 1
    )
    assert (
        _count(
            PROD_OPENSOUL_DB,
            "SELECT COUNT(*) FROM experiences WHERE source_ref=?",
            (f"msg:{msg_id}",),
        )
        == 0
    ), "注册缝缺省回调绝不许写生产（TestClient(src.main) 泄漏回归）"

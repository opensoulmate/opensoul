"""C3-⑪ — SQLite 适配层 $N 绑定语义（asyncpg 语义=按编号绑定）。

2026-10-11 live 实证：旧实现把 $N 一律按文本顺序替换为 ?，两种情形静默错绑：
① 乱序占位符（entity list `LIMIT $3 OFFSET $2`）→ LIMIT 绑到 offset、
   OFFSET 绑到 limit → 列表恒空（entity 页 0 实体的真因）；
② ANY($N) 展开成多个 ? 后，后续 $N 的参数位置错位。
本测试锁死新实现：按 $N 编号重排绑定、同一 $N 复用按出现次数展开、
"?" 风格（无 $N）调用点参数原样透传、顺序 $N 查询行为不变。
"""

from src.database.postgres import _convert_sql_for_sqlite


def test_out_of_order_placeholders_rebind_by_number():
    sql, args = _convert_sql_for_sqlite(
        "SELECT * FROM t WHERE a = $1 LIMIT $3 OFFSET $2", ("u", 20, 5)
    )
    assert sql == "SELECT * FROM t WHERE a = ? LIMIT ? OFFSET ?"
    assert args == ("u", 5, 20)  # LIMIT 绑 5、OFFSET 绑 20（按编号，非文本序）


def test_reused_placeholder_expands_per_occurrence():
    sql, args = _convert_sql_for_sqlite("SELECT $1, $2, $1", ("x", "y"))
    assert sql == "SELECT ?, ?, ?"
    assert args == ("x", "y", "x")


def test_any_then_later_placeholder_keeps_positions():
    sql, args = _convert_sql_for_sqlite(
        "SELECT * FROM t WHERE a = $1 AND b = ANY($2) AND c = $3",
        ("one", ["p", "q"], "three"),
    )
    # 2026-10-17 契约随更新：旧断言锁死 "b = IN (?, ?)"=非法 SQL（= 留在
    # 替换外必炸 near "IN"），真执行测试 test_any_executes_on_real_sqlite 补盲。
    assert sql == "SELECT * FROM t WHERE a = ? AND b IN (?, ?) AND c = ?"
    assert args == ("one", "p", "q", "three")


def test_any_scalar_fallback():
    sql, args = _convert_sql_for_sqlite("SELECT * FROM t WHERE a = ANY($1)", ("solo",))
    assert sql == "SELECT * FROM t WHERE a IN (?)"
    assert args == ("solo",)


def test_questionmark_style_passthrough_untouched():
    # services/feedback.py 等 "?" 风格调用点：无 $N 时参数原样透传
    sql, args = _convert_sql_for_sqlite(
        "INSERT INTO tags (id, name, user_id) VALUES (?, ?, ?)", ("i", "n", "u")
    )
    assert sql == "INSERT INTO tags (id, name, user_id) VALUES (?, ?, ?)"
    assert args == ("i", "n", "u")


def test_in_order_placeholders_unchanged():
    sql, args = _convert_sql_for_sqlite("SELECT * FROM t WHERE a = $1 AND b = $2", (1, 2))
    assert sql == "SELECT * FROM t WHERE a = ? AND b = ?"
    assert args == (1, 2)


def test_now_translated():
    sql, _ = _convert_sql_for_sqlite("UPDATE t SET x = NOW() WHERE id = $1", ("i",))
    assert "datetime('now')" in sql and "NOW()" not in sql


# ---------------------------------------------------------------------------
# 真执行补盲（2026-10-17）：上面的字符串断言从未真执行，把 "= IN" 非法 SQL
# 锁死过——凡 ANY 形态必须落真 SQLite 跑出真行数。
# ---------------------------------------------------------------------------


def _exec(sql: str, args: tuple) -> list:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (id TEXT, source_id TEXT, target_id TEXT)")
        conn.executemany(
            "INSERT INTO t (id, source_id, target_id) VALUES (?, ?, ?)",
            [("1", "a", "b"), ("2", "b", "c"), ("3", "c", "a")],
        )
        new_sql, new_args = _convert_sql_for_sqlite(sql, args)
        rows = conn.execute(new_sql, new_args).fetchall()
        return rows
    finally:
        conn.close()


def test_any_executes_on_real_sqlite():
    rows = _exec("SELECT id FROM t WHERE source_id = ANY($1)", (["a", "b"],))
    assert sorted(r[0] for r in rows) == ["1", "2"]


def test_reused_any_single_list_arg_two_occurrences():
    """get_graph 全图分支同形：同一 $1 复用两处+单 list 参——恰 2 元素时
    旧"单序列展开"约定会误展开成两标量错绑（真执行回归锁死）。"""
    rows = _exec(
        "SELECT id FROM t WHERE source_id = ANY($1) AND target_id = ANY($1)",
        (["a", "b"],),
    )
    assert sorted(r[0] for r in rows) == ["1"]


def test_any_empty_array_matches_nothing():
    rows = _exec("SELECT id FROM t WHERE source_id = ANY($1)", ([],))
    assert rows == []

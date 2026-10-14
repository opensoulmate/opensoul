"""knowledge update 写面收口（KN-①）：update tags 静默丢 + metadata 裸 dict 绑参。

修前（HEAD 旧代码同测实证非推断）：
① update_knowledge 对 tags 字段整个跳过（`if field != "tags"`）——客户端带
   tags 更新→200 回旧 tags=静默丢+谎报 ok；且 tags 是唯一字段时 set_clauses 空
   → `UPDATE knowledge SET , updated_at = NOW()` 空 SET 炸 SQL→500。
② metadata 传裸 dict 绑参（create 路径 json.dumps、update 不）→ SQLite 绑定
   失败→500（铁律「metadata json.dumps()」在 update 写面失守）。
③ 若 tags 写面只补链接不加归属闸：他人可用 update 写坏受害者链接面
   （TL-⑲ delete 归属闸同型面，本拍闸前置防跨用户零破坏）。
"""

import sqlite3


def _db(client):
    return sqlite3.connect(client.app.state.knowledge_client_db)


UID_A = "00000000-0000-4000-8000-00000000000a"
UID_B = "00000000-0000-4000-8000-00000000000b"


def _create(client, tags=("a", "b"), uid=UID_A, **kw):
    payload = {"title": "t1", "content": "c1", "tags": list(tags)}
    payload.update(kw)
    resp = client.post("/api/knowledge/", params={"user_id": uid}, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _link_names(client, kid):
    conn = _db(client)
    try:
        rows = conn.execute(
            "SELECT t.name FROM knowledge_tags kt JOIN tags t ON kt.tag_id = t.id "
            "WHERE kt.knowledge_id = ? ORDER BY t.name",
            (kid,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


class TestUpdateTagsApplied:
    def test_tags_update_applied(self, knowledge_client):
        kid = _create(knowledge_client, tags=("a", "b"))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"tags": ["b", "c"]},
        )
        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["tags"]) == ["b", "c"]  # 修前静默丢→["a","b"]
        assert _link_names(knowledge_client, kid) == ["b", "c"]  # 旧链接 a 真删

    def test_tags_only_update_200(self, knowledge_client):
        kid = _create(knowledge_client, tags=("a",))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"tags": ["x"]},
        )
        assert resp.status_code == 200, resp.text  # 修前空 SET SQL→500
        assert resp.json()["tags"] == ["x"]

    def test_tags_unset_untouched(self, knowledge_client):
        kid = _create(knowledge_client, tags=("a",))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"title": "t2"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "t2"
        assert data["tags"] == ["a"]  # 未声明字段零改动（exclude_unset 契约）
        assert _link_names(knowledge_client, kid) == ["a"]

    def test_tags_empty_clears(self, knowledge_client):
        kid = _create(knowledge_client, tags=("a", "b"))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"tags": []},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tags"] == []
        assert _link_names(knowledge_client, kid) == []

    def test_tags_and_title_together(self, knowledge_client):
        kid = _create(knowledge_client, tags=("a",))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"title": "t-new", "tags": ["y", "z"]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "t-new"
        assert sorted(data["tags"]) == ["y", "z"]
        assert _link_names(knowledge_client, kid) == ["y", "z"]

    def test_tags_row_vocabulary_kept(self, knowledge_client):
        """解链不删词表行（tags 是用户词表，与 create 语义同轴）：a 的 tags 行仍在。"""
        kid = _create(knowledge_client, tags=("a",))
        knowledge_client.put(
            f"/api/knowledge/{kid}", params={"user_id": UID_A}, json={"tags": ["x"]}
        )
        conn = _db(knowledge_client)
        try:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM tags WHERE user_id = ? ORDER BY name", (UID_A,)
                ).fetchall()
            ]
        finally:
            conn.close()
        assert names == ["a", "x"]


class TestUpdateMetadata:
    def test_metadata_update_serialized(self, knowledge_client):
        kid = _create(knowledge_client)
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"metadata": {"k": "v"}},
        )
        assert resp.status_code == 200, resp.text  # 修前裸 dict 绑参→500
        assert resp.json()["metadata"] == {"k": "v"}
        conn = _db(knowledge_client)
        try:
            raw = conn.execute("SELECT metadata FROM knowledge WHERE id = ?", (kid,)).fetchone()[0]
        finally:
            conn.close()
        assert raw == '{"k": "v"}'  # 落库 JSON 文本（铁律 metadata json.dumps()）

    def test_metadata_null_clears_to_empty_dict(self, knowledge_client):
        kid = _create(knowledge_client, metadata={"m": 1})
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_A},
            json={"metadata": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["metadata"] == {}


class TestUpdateGate:
    def test_update_wrong_user_zero_side_effect(self, knowledge_client):
        """他人 update 带 tags=404 零副作用（链接面/词表零改写，TL-⑲ 归属闸同型）。"""
        kid = _create(knowledge_client, tags=("a",))
        resp = knowledge_client.put(
            f"/api/knowledge/{kid}",
            params={"user_id": UID_B},
            json={"tags": ["evil"], "title": "hijack"},
        )
        assert resp.status_code == 404, resp.text
        assert _link_names(knowledge_client, kid) == ["a"]
        conn = _db(knowledge_client)
        try:
            assert conn.execute("SELECT COUNT(*) FROM tags WHERE name = 'evil'").fetchone()[0] == 0
            title = conn.execute("SELECT title FROM knowledge WHERE id = ?", (kid,)).fetchone()[0]
        finally:
            conn.close()
        assert title == "t1"

    def test_update_missing_404(self, knowledge_client):
        resp = knowledge_client.put(
            "/api/knowledge/00000000-0000-0000-0000-0000000000ff",
            params={"user_id": UID_A},
            json={"tags": ["x"]},
        )
        assert resp.status_code == 404, resp.text
        conn = _db(knowledge_client)
        try:
            assert (
                conn.execute("SELECT COUNT(*) FROM knowledge_tags").fetchone()[0] == 0
            )  # 缺行禁留孤儿链接
        finally:
            conn.close()

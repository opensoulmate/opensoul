"""knowledge 服务读写面断点回归（TL-⑲ 顺手收口件，Gate3 e2e 逼出的挡路真断点）。

修前（live :8090 真栈实证非推断）：
① create/get/list 响应 tags 契约归一缺失——存储行原样回吐（knowledge.tags 列
   JSON 文本 '[]' / GROUP_CONCAT 逗号串），KnowledgeResponse.tags=list[str] 校验
   'Input should be a valid list' → 500（create 逐次必炸且行已落库=静默部分写、
   get/list 有行即炸；空结果面把它掩成 200 故既有测试从未暴露）。
② get 空行 dict(None) TypeError → 500（API 404 契约不可达）。
③ delete 只删 knowledge 行——knowledge_chunks/knowledge_tags 留孤儿行；且
   qdrant/meili 清理在归属检查之前=他人向量/索引可被毁（SQL 行留着检索面已残）。
"""

import sqlite3


def _db(client):
    return sqlite3.connect(client.app.state.knowledge_client_db)


class TestTagsContract:
    def test_create_200_tags_list(self, knowledge_client):
        resp = knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": "00000000-0000-4000-8000-00000000000a"},
            json={"title": "t1", "content": "c1", "tags": ["a", "b"], "metadata": {"m": 1}},
        )
        assert resp.status_code == 200, resp.text  # 修前 500 ResponseValidationError
        data = resp.json()
        assert sorted(data["tags"]) == ["a", "b"]  # 序不承诺（GROUP_CONCAT 聚合序）
        assert data["metadata"] == {"m": 1}

    def test_get_tags_from_links(self, knowledge_client):
        uid = "00000000-0000-4000-8000-00000000000a"
        kid = knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": uid},
            json={"title": "t1", "content": "c1", "tags": ["a", "b"]},
        ).json()["id"]
        resp = knowledge_client.get(f"/api/knowledge/{kid}", params={"user_id": uid})
        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["tags"]) == ["a", "b"]

    def test_list_rows_200_tags_list(self, knowledge_client):
        uid = "00000000-0000-4000-8000-00000000000a"
        knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": uid},
            json={"title": "t1", "content": "c1", "tags": ["a"]},
        )
        knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": uid},
            json={"title": "t2", "content": "c2"},
        )
        resp = knowledge_client.get("/api/knowledge/", params={"user_id": uid})
        assert resp.status_code == 200, resp.text  # 修前有行即 500
        rows = resp.json()
        assert len(rows) == 2
        by_title = {r["title"]: r["tags"] for r in rows}
        assert by_title["t1"] == ["a"]
        assert by_title["t2"] == []

    def test_tags_normalizer_shapes(self):
        from src.services.knowledge import _row_out

        cases = [
            ("[]", []),
            ('["a", "b"]', ["a", "b"]),
            ("a,b", ["a", "b"]),
            ("", []),
            ("  ", []),
            (None, []),
            (["x"], ["x"]),
        ]
        for raw, want in cases:
            assert _row_out({"tags": raw})["tags"] == want, raw

    def test_metadata_parse_kept(self):
        from src.services.knowledge import _row_out

        assert _row_out({"metadata": '{"m": 1}', "tags": "[]"})["metadata"] == {"m": 1}
        assert _row_out({"metadata": "not-json", "tags": "[]"})["metadata"] == {}


class TestGetMissing:
    def test_get_missing_returns_404(self, knowledge_client):
        resp = knowledge_client.get(
            "/api/knowledge/00000000-0000-0000-0000-0000000000ff",
            params={"user_id": "00000000-0000-4000-8000-00000000000a"},
        )
        assert resp.status_code == 404, resp.text  # 修前 dict(None) TypeError→500


class TestDeleteCascade:
    def test_delete_cascades_children(self, knowledge_client):
        uid = "00000000-0000-4000-8000-00000000000a"
        kid = knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": uid},
            json={"title": "t1", "content": "hello chunk content", "tags": ["a"]},
        ).json()["id"]
        conn = _db(knowledge_client)
        try:
            before_chunks = conn.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE knowledge_id = ?", (kid,)
            ).fetchone()[0]
            before_links = conn.execute(
                "SELECT COUNT(*) FROM knowledge_tags WHERE knowledge_id = ?", (kid,)
            ).fetchone()[0]
            assert before_chunks >= 1
            assert before_links == 1
        finally:
            conn.close()
        resp = knowledge_client.delete(f"/api/knowledge/{kid}", params={"user_id": uid})
        assert resp.status_code == 200, resp.text
        conn = _db(knowledge_client)
        try:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE knowledge_id = ?", (kid,)
                ).fetchone()[0]
                == 0
            )  # 修前孤儿行残留
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_tags WHERE knowledge_id = ?", (kid,)
                ).fetchone()[0]
                == 0
            )
        finally:
            conn.close()

    def test_delete_wrong_user_zero_side_effect(self, knowledge_client):
        uid_a = "00000000-0000-4000-8000-00000000000a"
        uid_b = "00000000-0000-4000-8000-00000000000b"
        kid = knowledge_client.post(
            "/api/knowledge/",
            params={"user_id": uid_a},
            json={"title": "t1", "content": "hello chunk content", "tags": ["a"]},
        ).json()["id"]
        resp = knowledge_client.delete(f"/api/knowledge/{kid}", params={"user_id": uid_b})
        assert resp.status_code == 404, resp.text
        conn = _db(knowledge_client)
        try:
            assert (
                conn.execute("SELECT COUNT(*) FROM knowledge WHERE id = ?", (kid,)).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE knowledge_id = ?", (kid,)
                ).fetchone()[0]
                >= 1
            )  # 修前 qdrant/meili 先删=他人检索面可被毁，行级面必须零破坏
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_tags WHERE knowledge_id = ?", (kid,)
                ).fetchone()[0]
                == 1
            )
        finally:
            conn.close()

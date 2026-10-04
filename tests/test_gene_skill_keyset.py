"""gene技能库keyset分页：cursor游标翻页稳定性（"分页offset稳定性（可改keyset）"挂账销账）。

缺口背景：GET /api/gene/skill的limit/offset分页面对的是**活表**——gene学习链每回合
可落新技能、record_usage(last_used_at)改变排序键。翻页间隙的插入把后续页整体下推
（offset=N页拿到重复行），删除把后续页上提（漏行）；排序键并列（同秒created_at）时
行序本身不确定=页边界抖动。本文件钉住keyset解法的正确性：
1. 游标编解码（repr浮点往返=页边界行不因精度错位重/漏；畸形游标显式报错）；
2. 全量遍历不重不漏 + has_more/next_cursor真值；
3. 翻页间隙插入/删除不移位页边界（offset模做不到的稳定性）；
4. 排序键并列行的确定性tiebreak（skill_id终裁）；
5. 排序键漂移（record_usage激活未显示行）的已知极限——如实钉住语义不许静默漂移；
6. API面真实走router（conftest gene_client隔离库，绝不写生产skills.db）。
"""

import sqlite3

import pytest

from src.gene.skill_learner import SkillLearner, decode_cursor, encode_cursor


def _tool_calls(*names):
    return [
        {"function": {"name": n, "arguments": {"command": "echo hi", "content": "x" * 20}}}
        for n in names
    ]


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


def _seed_raw(learner, skill_id: str, created_at: float, last_used_at: float = 0.0):
    """裸插行（精确控制排序键——extract_from_execution用time.time()没法造并列键）。"""
    with sqlite3.connect(learner.db_path) as conn:
        conn.execute(
            "INSERT INTO learned_skills (skill_id, name, category, created_at, last_used_at)"
            " VALUES (?, ?, 'general', ?, ?)",
            (skill_id, f"技能{skill_id}", created_at, last_used_at),
        )


def _walk(learner, limit: int, category: str = "", mutate=None):
    """keyset全量遍历：返回(页列表, ids, has_more序列, next_cursor序列)。mutate在翻页间隙调用。"""
    pages, ids, has_mores, cursors = [], [], [], []
    cursor = None
    for _ in range(50):  # 防死循环保险
        page = learner.list_skills_page(category=category, limit=limit, after=cursor)
        pages.append(page)
        ids.extend(r["skill_id"] for r in page["skills"])
        has_mores.append(page["has_more"])
        cursors.append(page["next_cursor"])
        if not page["has_more"]:
            break
        if mutate:
            mutate()
            mutate = None  # 只在第一页后突变一次
        cursor = page["next_cursor"]
    return pages, ids, has_mores, cursors


class TestCursorCodec:
    def test_round_trip_float_exact(self, learner):
        """repr浮点往返必须是同一double（%.6f截断会错位页边界行）。"""
        row = {
            "skill_id": "skill_x",
            "created_at": 1728000000.1234567,
            "last_used_at": 1728000099.9876543,
        }
        a_ts, c_ts, sid = decode_cursor(encode_cursor(row))
        assert a_ts == row["last_used_at"] and c_ts == row["created_at"] and sid == "skill_x"

    def test_unused_row_activity_key_is_created_at(self):
        """未使用过（last_used_at=0）的行排序键=created_at（与_ACTIVITY_SQL同口径）。"""
        row = {"skill_id": "s", "created_at": 5.5, "last_used_at": 0.0}
        a_ts, _, _ = decode_cursor(encode_cursor(row))
        assert a_ts == 5.5

    def test_skill_id_with_colons_survives(self):
        row = {"skill_id": "a:b:c", "created_at": 1.0, "last_used_at": 2.0}
        assert decode_cursor(encode_cursor(row))[2] == "a:b:c"

    def test_tuple_passthrough(self):
        assert decode_cursor((1.0, 2.0, "x")) == (1.0, 2.0, "x")

    @pytest.mark.parametrize(
        "bad",
        ["", "junk", "1.0:2.0", "nan_part:2.0:id", "1.0:notafloat:id", "1.0:2.0:"],
    )
    def test_malformed_cursor_raises(self, bad):
        with pytest.raises(ValueError):
            decode_cursor(bad)


class TestKeysetPaging:
    def test_full_walk_no_dup_no_skip(self, learner):
        for i in range(25):
            _seed_raw(learner, f"s{i:02d}", created_at=float(i))
        _, ids, has_mores, cursors = _walk(learner, limit=10)
        assert sorted(ids) == sorted(f"s{i:02d}" for i in range(25))
        assert len(ids) == len(set(ids)), "keyset遍历不许重复"
        assert has_mores == [True, True, False]
        assert cursors[-1] == ""  # 无下一页=空游标

    def test_insert_mid_pagination_no_shift(self, learner):
        """翻页间隙插入新行（更新的活动=排最前）不移位页边界：后页不重复、老行不漏。"""
        for i in range(10):
            _seed_raw(learner, f"old{i}", created_at=float(i))

        def insert_new():
            _seed_raw(learner, "new", created_at=999.0)

        _, ids, _, _ = _walk(learner, limit=5, mutate=insert_new)
        # "new"排最前（游标之前）本批不可见（刷新可见）——老行10条必须不重不漏
        assert sorted(i for i in ids if i.startswith("old")) == sorted(f"old{i}" for i in range(10))
        assert len(ids) == len(set(ids))
        assert "new" not in ids  # 插在游标上方=不属于本批下半段

    def test_delete_mid_pagination_no_skip(self, learner):
        """翻页间隙删除未显示行不把后续行上提挤出视图（offset模会漏）。"""
        for i in range(10):
            _seed_raw(learner, f"s{i}", created_at=float(i))

        def delete_unseen():
            with sqlite3.connect(learner.db_path) as conn:
                conn.execute("DELETE FROM learned_skills WHERE skill_id='s2'")

        _, ids, _, _ = _walk(learner, limit=5, mutate=delete_unseen)
        assert "s2" not in ids
        assert sorted(ids) == sorted(f"s{i}" for i in range(10) if i != 2)

    def test_tied_sort_keys_deterministic(self, learner):
        """排序键并列（同created_at）也有确定序（skill_id DESC终裁）——遍历不重不漏。"""
        for i in range(9):
            _seed_raw(learner, f"t{i}", created_at=100.0)
        _, ids, _, _ = _walk(learner, limit=2)
        assert sorted(ids) == sorted(f"t{i}" for i in range(9))
        assert len(ids) == len(set(ids))
        # 确定性=两次遍历同序
        _, ids2, _, _ = _walk(learner, limit=4)
        assert ids == ids2

    def test_sort_key_drift_skip_is_documented_semantics(self, learner):
        """已知极限如实钉住：翻页间隙被record_usage激活的**未显示行**前移越过游标，
        本批不可见（语义="它升到顶部了"，刷新首屏可见）——不许静默漂移成别的语义。"""
        for i in range(6):
            _seed_raw(learner, f"s{i}", created_at=float(i))
        first = learner.list_skills_page(limit=3)
        # 激活一个未显示行（s0在下半段）→排序键变成最新
        learner.record_usage("s0", success=True)
        rest = learner.list_skills_page(limit=3, after=first["next_cursor"])
        assert "s0" not in [r["skill_id"] for r in rest["skills"]]
        # 刷新首屏：s0排最前
        assert learner.list_skills_page(limit=3)["skills"][0]["skill_id"] == "s0"

    def test_category_filter_keyset(self, learner):
        learner.extract_from_execution(
            "sess-1", "读取文件并写入文件", _tool_calls("read_file", "write_file"), success=True
        )
        learner.extract_from_execution(
            "sess-2", "搜索网页信息", _tool_calls("web_search", "read_file"), success=True
        )
        learner.extract_from_execution(
            "sess-3", "读写编辑文件", _tool_calls("read_file", "edit_file"), success=True
        )
        pages, ids, has_mores, _ = _walk(learner, limit=1, category="file_operations")
        assert len(ids) == 2 and len(set(ids)) == 2
        assert all(r["category"] == "file_operations" for p in pages for r in p["skills"])
        assert has_mores == [True, False]

    def test_has_more_is_page_truth_not_total_inference(self, learner):
        """has_more=limit+1探底（本页真值），不是offset+len<total推算——total会随
        翻页间隙增删漂移，推算式说谎（07:35轮同病）。"""
        for i in range(3):
            _seed_raw(learner, f"s{i}", created_at=float(i))
        p1 = learner.list_skills_page(limit=2)
        assert p1["has_more"] is True and p1["next_cursor"] != ""
        p2 = learner.list_skills_page(limit=2, after=p1["next_cursor"])
        assert p2["has_more"] is False and p2["next_cursor"] == ""

    def test_empty_page_no_cursor(self, learner):
        page = learner.list_skills_page(limit=5)
        assert page["skills"] == [] and page["has_more"] is False and page["next_cursor"] == ""


class TestKeysetApi:
    _EXTRACT_PAYLOADS = [
        {
            "task_description": f"keyset API 文件库存任务{i}",
            "execution_log": (
                '[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},'
                ' {"function": {"name": "write_file", "arguments": {"path": "b.txt"}}}]'
            ),
            "success": True,
        }
        for i in range(5)
    ]

    def _seed_api(self, gene_client):
        ids = []
        for payload in self._EXTRACT_PAYLOADS:
            r = gene_client.post("/api/gene/skill/extract", json=payload)
            assert r.status_code == 200, r.text
            ids.append(r.json()["skill_id"])
        return ids

    def test_cursor_walk_no_dup_no_skip(self, gene_client):
        seeded = set(self._seed_api(gene_client))
        seen, cursor, has_mores = set(), "", []
        for _ in range(10):
            r = gene_client.get(f"/api/gene/skill?limit=2&cursor={cursor}")
            assert r.status_code == 200, r.text
            body = r.json()
            page_ids = {s["skill_id"] for s in body["skills"]}
            assert not (page_ids & seen), f"翻页重复: {page_ids & seen}"
            seen |= page_ids
            has_mores.append(body["has_more"])
            if not body["has_more"]:
                assert body["next_cursor"] == ""
                break
            assert body["next_cursor"], "has_more=True必须带游标"
            cursor = body["next_cursor"]
        assert seen == seeded
        assert has_mores == [True, True, False]

    def test_malformed_cursor_400(self, gene_client):
        r = gene_client.get("/api/gene/skill?cursor=not_a_cursor")
        assert r.status_code == 400
        assert "cursor" in r.json()["detail"]

    def test_offset_mode_backward_compat(self, gene_client):
        """offset模字段照旧（前端已切游标但curl/老消费者还在用）+游标回填可中途切换。"""
        self._seed_api(gene_client)
        r = gene_client.get("/api/gene/skill?limit=2&offset=2")
        body = r.json()
        assert body["offset"] == 2 and body["limit"] == 2
        assert body["count"] == len(body["skills"]) == 2
        assert body["total"] == 5
        assert body["has_more"] is True
        assert body["next_cursor"], "offset模has_more=True时也回填游标"
        # 中途切游标模：offset=2末行的下一页=游标续页，不重不漏（5行剩最后1行）
        r2 = gene_client.get(f"/api/gene/skill?limit=10&cursor={body['next_cursor']}")
        assert r2.status_code == 200
        rest = {s["skill_id"] for s in r2.json()["skills"]}
        shown = {s["skill_id"] for s in body["skills"]}
        assert not (rest & shown) and len(rest) == 1

    def test_category_cursor_api(self, gene_client):
        self._seed_api(gene_client)
        r = gene_client.get("/api/gene/skill?limit=2&category=file_operations&cursor=")
        body = r.json()
        assert body["total"] == 5  # 全部是file_operations（read_file+write_file）
        seen = {s["skill_id"] for s in body["skills"]}
        while body["has_more"]:
            r = gene_client.get(
                f"/api/gene/skill?limit=2&category=file_operations&cursor={body['next_cursor']}"
            )
            body = r.json()
            page_ids = {s["skill_id"] for s in body["skills"]}
            assert not (page_ids & seen)
            seen |= page_ids
        assert len(seen) == 5

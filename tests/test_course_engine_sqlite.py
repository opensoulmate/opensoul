"""A2: CourseEngine存储迁SQLite — 持久化/遗留JSON迁移/不再写JSON文件的证据测试。"""

import json
import sqlite3

from src.learn.course_engine import CourseEngine


class TestCourseEngineSQLite:
    def test_persistence_across_instances(self, tmp_path):
        """写入后重建engine（新连接）仍可见 = 真持久化，不是内存态。"""
        eng = CourseEngine(storage_dir=tmp_path)
        course = eng.create_course(title="A2持久化验证", tags=["sqlite"], topics=["t1"])
        ch = eng.add_chapter(
            course.course_id,
            title="第一章",
            content="内容",
            quiz=[
                {"question": "Q1?", "options": ["a", "b"], "correct_index": 0, "explanation": "why"}
            ],
        )
        eng.mark_chapter(course.course_id, ch.chapter_id, True)

        eng2 = CourseEngine(storage_dir=tmp_path)
        got = eng2.get_course(course.course_id)
        assert got is not None
        assert got.title == "A2持久化验证"
        assert got.tags == ["sqlite"]
        assert got.status == "completed"
        assert len(got.chapters) == 1
        assert got.chapters[0].completed is True
        assert got.chapters[0].quiz[0].question == "Q1?"

    def test_no_json_written(self, tmp_path):
        """替换JSON文件存储：CRUD全走SQLite后目录内不得出现新*.json。"""
        eng = CourseEngine(storage_dir=tmp_path)
        course = eng.create_course(title="不写JSON")
        ch = eng.add_chapter(course.course_id, title="c1")
        eng.update_course(course.course_id, title="改名")
        eng.update_chapter(course.course_id, ch.chapter_id, content="改内容")
        eng.add_quiz(
            course.course_id,
            ch.chapter_id,
            [{"question": "q", "options": ["x"], "correct_index": 0}],
        )
        assert list(tmp_path.glob("*.json")) == []
        assert (tmp_path / "learn.db").exists()

    def test_legacy_json_migration_idempotent(self, tmp_path):
        """遗留course_*.json首载入库并改名，重建engine不重复导入。"""
        legacy = {
            "course_id": "course_legacy0001",
            "title": "旧JSON课程",
            "description": "迁移前",
            "tags": ["old"],
            "topics": [],
            "domain": "",
            "knowledge_ids": [],
            "chapters": [
                {
                    "chapter_id": "ch_legacy000001",
                    "title": "旧章节",
                    "content": "旧内容",
                    "order": 0,
                    "completed": True,
                    "completed_at": 1700000000.0,
                    "quiz": [
                        {
                            "question": "旧题",
                            "options": ["a"],
                            "correct_index": 0,
                            "explanation": "",
                        }
                    ],
                }
            ],
            "status": "in_progress",
            "created_at": 1700000000.0,
            "updated_at": 1700000001.0,
            "generated_by": "manual",
        }
        (tmp_path / "course_legacy0001.json").write_text(json.dumps(legacy, ensure_ascii=False))

        eng = CourseEngine(storage_dir=tmp_path)
        got = eng.get_course("course_legacy0001")
        assert got is not None
        assert got.title == "旧JSON课程"
        assert got.chapters[0].completed is True
        assert got.chapters[0].quiz[0].question == "旧题"
        # 迁移后原JSON改名，不再被当作数据源
        assert not (tmp_path / "course_legacy0001.json").exists()
        assert (tmp_path / "course_legacy0001.json.migrated").exists()

        # 重建engine：DB已入库，不重复迁移、不举示
        eng2 = CourseEngine(storage_dir=tmp_path)
        assert [c.course_id for c in eng2.list_courses()] == ["course_legacy0001"]
        conn = sqlite3.connect(str(tmp_path / "learn.db"))
        n = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        ch_n = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        conn.close()
        assert (n, ch_n) == (1, 1)

    def test_delete_course_purges_db(self, tmp_path):
        eng = CourseEngine(storage_dir=tmp_path)
        course = eng.create_course(title="待删")
        eng.add_chapter(course.course_id, title="ch")
        assert eng.delete_course(course.course_id) is True

        eng2 = CourseEngine(storage_dir=tmp_path)
        assert eng2.get_course(course.course_id) is None
        conn = sqlite3.connect(str(tmp_path / "learn.db"))
        rows = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        ch_rows = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        conn.close()
        assert (rows, ch_rows) == (0, 0)

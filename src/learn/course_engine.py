"""Learn engine — manage courses, chapters, quizzes and progress tracking."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class QuizQuestion:
    question: str
    options: list[str]
    correct_index: int
    explanation: str = ""


@dataclass
class Chapter:
    chapter_id: str
    title: str
    content: str  # Markdown content
    order: int = 0
    completed: bool = False
    completed_at: float | None = None
    quiz: list[QuizQuestion] = field(default_factory=list)


@dataclass
class Course:
    course_id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    domain: str = ""
    knowledge_ids: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    status: str = "not_started"  # not_started | in_progress | reviewing | completed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    generated_by: str = "manual"  # manual | ai


class CourseEngine:
    """Course management with SQLite persistence (A2: 替换JSON文件存储，与hippo统一).

    数据面：storage_dir/learn.db（courses + chapters两表）。历史 per-course JSON
    文件（course_*.json）在首载时一次性迁移入库（INSERT OR IGNORE幂等），
    成功后改名 *.json.migrated 不再重复读；新写入只走SQLite。
    """

    def __init__(self, storage_dir: str | Path | None = None, db_path: str | Path | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.opensoul/learn"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.storage_dir / "learn.db"
        # check_same_thread=False：模块级单例被FastAPI多线程/测试共用；
        # timeout=30防嵌套连接死锁时硬失败（与get_db惯例一致）。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._courses: dict[str, Course] = {}
        self._lock = threading.Lock()
        self._ensure_schema()
        self._load_courses()

    def _ensure_schema(self):
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    topics TEXT DEFAULT '[]',
                    domain TEXT DEFAULT '',
                    knowledge_ids TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'not_started',
                    created_at REAL,
                    updated_at REAL,
                    generated_by TEXT DEFAULT 'manual'
                );
                CREATE TABLE IF NOT EXISTS chapters (
                    chapter_id TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    ord INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    completed_at REAL,
                    quiz TEXT DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_chapters_course ON chapters(course_id);
                """
            )

    @staticmethod
    def _row_to_course(row: sqlite3.Row, chapters: list[Chapter]) -> Course:
        return Course(
            course_id=row["course_id"],
            title=row["title"],
            description=row["description"] or "",
            tags=json.loads(row["tags"] or "[]"),
            topics=json.loads(row["topics"] or "[]"),
            domain=row["domain"] or "",
            knowledge_ids=json.loads(row["knowledge_ids"] or "[]"),
            chapters=chapters,
            status=row["status"] or "not_started",
            created_at=row["created_at"] or time.time(),
            updated_at=row["updated_at"] or time.time(),
            generated_by=row["generated_by"] or "manual",
        )

    @staticmethod
    def _row_to_chapter(row: sqlite3.Row) -> Chapter:
        quiz = [QuizQuestion(**q) for q in json.loads(row["quiz"] or "[]")]
        return Chapter(
            chapter_id=row["chapter_id"],
            title=row["title"],
            content=row["content"] or "",
            order=row["ord"] or 0,
            completed=bool(row["completed"]),
            completed_at=row["completed_at"],
            quiz=quiz,
        )

    def _migrate_legacy_json(self):
        """一次性迁移旧 per-course JSON 文件入库，成功即改名 *.json.migrated。"""
        for f in sorted(self.storage_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                chapters = []
                for ch in data.get("chapters", []):
                    quiz = [QuizQuestion(**q) for q in ch.pop("quiz", [])]
                    chapters.append(Chapter(quiz=quiz, **ch))
                data["chapters"] = chapters
                course = Course(**data)
                with self._lock, self._conn:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO courses "
                        "(course_id, title, description, tags, topics, domain, "
                        " knowledge_ids, status, created_at, updated_at, generated_by) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(course.course_id),
                            course.title,
                            course.description,
                            json.dumps(course.tags, ensure_ascii=False),
                            json.dumps(course.topics, ensure_ascii=False),
                            course.domain,
                            json.dumps(course.knowledge_ids, ensure_ascii=False),
                            course.status,
                            course.created_at,
                            course.updated_at,
                            course.generated_by,
                        ),
                    )
                    if cur.rowcount:
                        for ch in course.chapters:
                            self._conn.execute(
                                "INSERT OR IGNORE INTO chapters "
                                "(chapter_id, course_id, title, content, ord, completed, completed_at, quiz) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    str(ch.chapter_id),
                                    str(course.course_id),
                                    ch.title,
                                    ch.content,
                                    ch.order,
                                    1 if ch.completed else 0,
                                    ch.completed_at,
                                    json.dumps([asdict(q) for q in ch.quiz], ensure_ascii=False),
                                ),
                            )
                f.rename(f.with_suffix(f.suffix + ".migrated"))
                logging.getLogger(__name__).info("legacy course JSON migrated: %s", f.name)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "legacy JSON migrate skipped %s: %s", f.name, exc
                )

    def _load_courses(self):
        self._migrate_legacy_json()
        self._courses.clear()
        for row in self._conn.execute("SELECT * FROM courses"):
            chapters = [
                self._row_to_chapter(ch_row)
                for ch_row in self._conn.execute(
                    "SELECT * FROM chapters WHERE course_id = ? ORDER BY ord",
                    (str(row["course_id"]),),
                )
            ]
            course = self._row_to_course(row, chapters)
            self._courses[course.course_id] = course

    def _save_course(self, course: Course):
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO courses "
                "(course_id, title, description, tags, topics, domain, "
                " knowledge_ids, status, created_at, updated_at, generated_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(course.course_id),
                    course.title,
                    course.description,
                    json.dumps(course.tags, ensure_ascii=False),
                    json.dumps(course.topics, ensure_ascii=False),
                    course.domain,
                    json.dumps(course.knowledge_ids, ensure_ascii=False),
                    course.status,
                    course.created_at,
                    course.updated_at,
                    course.generated_by,
                ),
            )
            self._conn.execute("DELETE FROM chapters WHERE course_id = ?", (str(course.course_id),))
            for ch in course.chapters:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chapters "
                    "(chapter_id, course_id, title, content, ord, completed, completed_at, quiz) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(ch.chapter_id),
                        str(course.course_id),
                        ch.title,
                        ch.content,
                        ch.order,
                        1 if ch.completed else 0,
                        ch.completed_at,
                        json.dumps([asdict(q) for q in ch.quiz], ensure_ascii=False),
                    ),
                )

    def _update_status(self, course: Course):
        if not course.chapters:
            course.status = "not_started"
            return
        completed = sum(1 for ch in course.chapters if ch.completed)
        if completed == 0:
            course.status = "not_started"
        elif completed == len(course.chapters):
            course.status = "completed"
        else:
            course.status = "in_progress"

    # ── Course CRUD ─────────────────────────────────────────

    def create_course(
        self,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        topics: list[str] | None = None,
        domain: str = "",
        knowledge_ids: list[str] | None = None,
        generated_by: str = "manual",
    ) -> Course:
        course = Course(
            course_id=f"course_{uuid.uuid4().hex[:12]}",
            title=title,
            description=description,
            tags=tags or [],
            topics=topics or [],
            domain=domain,
            knowledge_ids=knowledge_ids or [],
            generated_by=generated_by,
        )
        with self._lock:
            self._courses[course.course_id] = course
            self._save_course(course)
        return course

    def get_course(self, course_id: str) -> Course | None:
        return self._courses.get(course_id)

    def list_courses(self, status: str | None = None) -> list[Course]:
        courses = list(self._courses.values())
        if status:
            courses = [c for c in courses if c.status == status]
        courses.sort(key=lambda c: c.updated_at, reverse=True)
        return courses

    def update_course(self, course_id: str, **kwargs) -> Course | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(course, k) and k not in ("course_id", "created_at", "chapters"):
                    setattr(course, k, v)
            course.updated_at = time.time()
            self._save_course(course)
        return course

    def delete_course(self, course_id: str) -> bool:
        with self._lock:
            if course_id in self._courses:
                del self._courses[course_id]
                with self._conn:
                    self._conn.execute(
                        "DELETE FROM chapters WHERE course_id = ?", (str(course_id),)
                    )
                    self._conn.execute("DELETE FROM courses WHERE course_id = ?", (str(course_id),))
                # 遗留JSON文件兜底清理（迁移失败残片不再复活）
                (self.storage_dir / f"{course_id}.json").unlink(missing_ok=True)
                return True
        return False

    # ── Chapter CRUD ────────────────────────────────────────

    def add_chapter(
        self,
        course_id: str,
        title: str,
        content: str = "",
        quiz: list[dict] | None = None,
    ) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        chapter = Chapter(
            chapter_id=f"ch_{uuid.uuid4().hex[:12]}",
            title=title,
            content=content,
            order=len(course.chapters),
            quiz=[QuizQuestion(**q) for q in (quiz or [])],
        )
        with self._lock:
            course.chapters.append(chapter)
            course.updated_at = time.time()
            self._save_course(course)
        return chapter

    def update_chapter(self, course_id: str, chapter_id: str, **kwargs) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    for k, v in kwargs.items():
                        if hasattr(ch, k) and k not in ("chapter_id",):
                            setattr(ch, k, v)
                    course.updated_at = time.time()
                    self._update_status(course)
                    self._save_course(course)
                    return ch
        return None

    def delete_chapter(self, course_id: str, chapter_id: str) -> bool:
        course = self._courses.get(course_id)
        if not course:
            return False
        with self._lock:
            before = len(course.chapters)
            course.chapters = [ch for ch in course.chapters if ch.chapter_id != chapter_id]
            if len(course.chapters) < before:
                # Re-order
                for i, ch in enumerate(course.chapters):
                    ch.order = i
                course.updated_at = time.time()
                self._update_status(course)
                self._save_course(course)
                return True
        return False

    def mark_chapter(self, course_id: str, chapter_id: str, completed: bool) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    ch.completed = completed
                    ch.completed_at = time.time() if completed else None
                    course.updated_at = time.time()
                    self._update_status(course)
                    self._save_course(course)
                    return ch
        return None

    # ── Quiz ────────────────────────────────────────────────

    def add_quiz(
        self,
        course_id: str,
        chapter_id: str,
        questions: list[dict],
    ) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    ch.quiz = [QuizQuestion(**q) for q in questions]
                    course.updated_at = time.time()
                    self._save_course(course)
                    return ch.quiz
        return None

    def get_quiz(self, course_id: str, chapter_id: str) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        for ch in course.chapters:
            if ch.chapter_id == chapter_id:
                return ch.quiz
        return None

    # ── Stats ───────────────────────────────────────────────

    def stats(self) -> dict:
        courses = list(self._courses.values())
        total_chapters = sum(len(c.chapters) for c in courses)
        completed_chapters = sum(sum(1 for ch in c.chapters if ch.completed) for c in courses)
        reviewing = sum(1 for c in courses if c.status == "reviewing")
        return {
            "total_courses": len(courses),
            "total_chapters": total_chapters,
            "completed_chapters": completed_chapters,
            "pending_chapters": total_chapters - completed_chapters,
            "reviewing_courses": reviewing,
            "by_status": {
                s: sum(1 for c in courses if c.status == s)
                for s in ("not_started", "in_progress", "reviewing", "completed")
            },
        }

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

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

# F3b-③c-2②b（2026-10-12）数据面租户缺省：匿名/内部路径（search.py 跨模块消费、
# 旧 JSON 迁移）归 'default' 租户——与生产数据分布（learn.db 存量行）一致。
DEFAULT_TENANT = "default"


class CourseAccessDenied(Exception):
    """跨租户课程访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db），此处只携带原因供 API 如实报。
    """

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


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
    tenant_id: str = "default"  # F3b-③c-2②b：章节行=课程行继承（③b-3 成员行同纪律）


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
    tenant_id: str = "default"  # F3b-③c-2②b：课程行归属租户（create=身份派生）


class CourseEngine:
    """Course management with SQLite persistence (A2: 替换JSON文件存储，与hippo统一).

    数据面：storage_dir/learn.db（courses + chapters两表）。历史 per-course JSON
    文件（course_*.json）在首载时一次性迁移入库（INSERT OR IGNORE幂等），
    成功后改名 *.json.migrated 不再重复读；新写入只走SQLite。
    """

    def __init__(self, storage_dir: str | Path | None = None, db_path: str | Path | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.opensoul/learn"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # LEARN_DB env 覆盖（NEST_DB/AGENT_REGISTRY_DB 先例）：测试隔离用。
        self.db_path = (
            Path(db_path)
            if db_path
            else Path(os.environ.get("LEARN_DB") or self.storage_dir / "learn.db")
        )
        # check_same_thread=False：模块级单例被FastAPI多线程/测试共用；
        # timeout=30防嵌套连接死锁时硬失败（与get_db惯例一致）。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._courses: dict[str, Course] = {}
        self._lock = threading.Lock()
        # F3b-③c-2②b：数据面咨询 IsolationEngine（懒构造——首个 gate 调用才建
        # NestStore，收集期零生产库接触）。默认实例即真闸门，无"未接线"模式。
        self._isolation: IsolationEngine | None = None
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
                    generated_by TEXT DEFAULT 'manual',
                    tenant_id TEXT DEFAULT 'default'
                );
                CREATE TABLE IF NOT EXISTS chapters (
                    chapter_id TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    ord INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    completed_at REAL,
                    quiz TEXT DEFAULT '[]',
                    tenant_id TEXT DEFAULT 'default'
                );
                CREATE INDEX IF NOT EXISTS idx_chapters_course ON chapters(course_id);
                """
            )
        # F3b-③c-2②b：DDL+ALTER 双轨幂等——老库（迁移前形状无 tenant_id）补列+存量行
        # 回填 'default'（与生产数据分布一致），缺列才 ALTER（幂等）。租户索引必须在补列
        # 之后建（老库形态下先建索引会 no such column，③c-2① 坑记档延续）。
        with self._conn:
            for table in ("courses", "chapters"):
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "tenant_id" not in cols:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT DEFAULT 'default'"
                    )
                    self._conn.execute(
                        f"UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL"
                    )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_courses_tenant ON courses(tenant_id, updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chapters_tenant ON chapters(tenant_id, course_id)"
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
            tenant_id=row["tenant_id"] or DEFAULT_TENANT,
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
            tenant_id=row["tenant_id"] or DEFAULT_TENANT,
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
                        " knowledge_ids, status, created_at, updated_at, generated_by, tenant_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                            DEFAULT_TENANT,  # 旧 JSON=历史单租户数据，回填 'default'
                        ),
                    )
                    if cur.rowcount:
                        for ch in course.chapters:
                            self._conn.execute(
                                "INSERT OR IGNORE INTO chapters "
                                "(chapter_id, course_id, title, content, ord, completed, completed_at, quiz, tenant_id) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    str(ch.chapter_id),
                                    str(course.course_id),
                                    ch.title,
                                    ch.content,
                                    ch.order,
                                    1 if ch.completed else 0,
                                    ch.completed_at,
                                    json.dumps([asdict(q) for q in ch.quiz], ensure_ascii=False),
                                    DEFAULT_TENANT,
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
        # F3b-③c-2②b：课程行随对象落 tenant_id（往返粘性——update 路径不触 tenant
        # 字段故不洗键）；章节行=课程行继承（③b-3 成员行同纪律）。
        row_tenant = str(course.tenant_id or "").strip() or DEFAULT_TENANT
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO courses "
                "(course_id, title, description, tags, topics, domain, "
                " knowledge_ids, status, created_at, updated_at, generated_by, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    row_tenant,
                ),
            )
            self._conn.execute("DELETE FROM chapters WHERE course_id = ?", (str(course.course_id),))
            for ch in course.chapters:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chapters "
                    "(chapter_id, course_id, title, content, ord, completed, completed_at, quiz, tenant_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(ch.chapter_id),
                        str(course.course_id),
                        ch.title,
                        ch.content,
                        ch.order,
                        1 if ch.completed else 0,
                        ch.completed_at,
                        json.dumps([asdict(q) for q in ch.quiz], ensure_ascii=False),
                        row_tenant,
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

    # ── 租户隔离闸门（F3b-③c-2②b）────────────────────────────

    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def gate(self, tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-③c-2②b）。

        资源 ID 用 namespaced_id(row_tenant ns, course_id)——资源的 namespace 前缀是
        行归属租户，比较基准是访问者派生租户；namespace 不匹配=跨租户冒读，引擎拒绝
        （allowed=False）→ CourseAccessDenied 上抛（API 层映射 403），禁静默当
        not_found。允许/拒绝都进引擎审计日志（audit_access=True）。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.COURSE,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), resource_id),
            action=action,
        )
        if not verdict.get("allowed"):
            raise CourseAccessDenied(verdict.get("reason", "access denied"), verdict)

    def gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作（list/stats）审计留痕：执行隔离由内存租户过滤承担，引擎侧记一次
        namespace 自匹配访问（audit_access=True「Log all access attempts」语义），使
        数据面访问在 /api/nest 隔离审计里可见。"""
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.COURSE,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

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
        tenant_id: str | None = None,
    ) -> Course:
        # F3b-③c-2②b：行归属租户=调用方派生租户（bind_identity），缺失/空白→
        # 'default' 兜底（键派生故障绝不吃掉落库，③b-2/③c-2②a 同款旁路纪律）。
        row_tenant = str(tenant_id or "").strip() or DEFAULT_TENANT
        course = Course(
            course_id=f"course_{uuid.uuid4().hex[:12]}",
            title=title,
            description=description,
            tags=tags or [],
            topics=topics or [],
            domain=domain,
            knowledge_ids=knowledge_ids or [],
            generated_by=generated_by,
            tenant_id=row_tenant,
        )
        with self._lock:
            self._courses[course.course_id] = course
            self._save_course(course)
        return course

    def get_course(self, course_id: str, tenant_id: str | None = None) -> Course | None:
        """取课程（章节随行）。tenant_id 显式=by-ID 闸门真执行（跨租户
        CourseAccessDenied，禁静默 None）；None=系统内部路径（search.py 等）。"""
        course = self._courses.get(course_id)
        if course is None:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "read")
        return course

    def list_courses(self, status: str | None = None, tenant_id: str | None = None) -> list[Course]:
        """列课程。tenant_id 显式=bulk 审计+恒按租户过滤（B 恒读不到 A 的行）；
        None=系统视图（search.py 跨模块消费等无身份轴路径，诚实边界见 roadmap）。"""
        courses = list(self._courses.values())
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "list")
            courses = [c for c in courses if c.tenant_id == tenant_id]
        if status:
            courses = [c for c in courses if c.status == status]
        courses.sort(key=lambda c: c.updated_at, reverse=True)
        return courses

    def update_course(
        self, course_id: str, tenant_id: str | None = None, **kwargs
    ) -> Course | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "update")
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(course, k) and k not in ("course_id", "created_at", "chapters"):
                    setattr(course, k, v)
            course.updated_at = time.time()
            self._save_course(course)
        return course

    def delete_course(self, course_id: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            if course_id in self._courses:
                course = self._courses[course_id]
                if tenant_id is not None:
                    self.gate(tenant_id, course.tenant_id, course_id, "delete")
                del self._courses[course_id]
                with self._conn:
                    # 级联按行租户键清（③b-3 同纪律：他租户行一律不动，禁跨租户破坏
                    # 优先于不留孤儿）
                    self._conn.execute(
                        "DELETE FROM chapters WHERE course_id = ? AND tenant_id = ?",
                        (str(course_id), course.tenant_id),
                    )
                    self._conn.execute(
                        "DELETE FROM courses WHERE course_id = ? AND tenant_id = ?",
                        (str(course_id), course.tenant_id),
                    )
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
        tenant_id: str | None = None,
    ) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "write")
        chapter = Chapter(
            chapter_id=f"ch_{uuid.uuid4().hex[:12]}",
            title=title,
            content=content,
            order=len(course.chapters),
            quiz=[QuizQuestion(**q) for q in (quiz or [])],
            tenant_id=course.tenant_id,  # 章节行=课程行继承（③b-3 成员行同纪律）
        )
        with self._lock:
            course.chapters.append(chapter)
            course.updated_at = time.time()
            self._save_course(course)
        return chapter

    def update_chapter(
        self, course_id: str, chapter_id: str, tenant_id: str | None = None, **kwargs
    ) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "update")
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

    def delete_chapter(self, course_id: str, chapter_id: str, tenant_id: str | None = None) -> bool:
        course = self._courses.get(course_id)
        if not course:
            return False
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "delete")
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

    def mark_chapter(
        self, course_id: str, chapter_id: str, completed: bool, tenant_id: str | None = None
    ) -> Chapter | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "update")
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
        tenant_id: str | None = None,
    ) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "update")
        with self._lock:
            for ch in course.chapters:
                if ch.chapter_id == chapter_id:
                    ch.quiz = [QuizQuestion(**q) for q in questions]
                    course.updated_at = time.time()
                    self._save_course(course)
                    return ch.quiz
        return None

    def get_quiz(
        self, course_id: str, chapter_id: str, tenant_id: str | None = None
    ) -> list[QuizQuestion] | None:
        course = self._courses.get(course_id)
        if not course:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, course.tenant_id, course_id, "read")
        for ch in course.chapters:
            if ch.chapter_id == chapter_id:
                return ch.quiz
        return None

    # ── Stats ───────────────────────────────────────────────

    def stats(self, tenant_id: str | None = None) -> dict:
        """学习统计。tenant_id 显式=bulk 审计+恒按租户过滤（禁泄露全局分布）；
        None=系统视图（/health 引擎健康面，诚实边界见 roadmap）。"""
        courses = list(self._courses.values())
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "stats")
            courses = [c for c in courses if c.tenant_id == tenant_id]
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

"""Project Memory — 代码库文件索引、依赖图谱、核心组件识别，支持增量刷新。"""

import ast
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.cognitive import FileInfo, ImpactAnalysis

logger = logging.getLogger(__name__)

LANG_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".css": "css",
    ".html": "html",
    ".sh": "shell",
}

IGNORE_DIRS = {
    "node_modules",
    ".next",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".hermes",
    ".cache",
    "data",
}

CORE_FILE_PERCENTILE = 0.9


class ProjectMemory:
    """项目记忆：文件索引、依赖图谱、核心组件识别"""

    def __init__(self, repo_root: str, cache_path: str = "", autoload: bool = False):
        self.repo_root = Path(repo_root)
        self.cache_path = (
            Path(cache_path)
            if cache_path
            else Path(os.path.expanduser("~/.opensoul/project_memory.json"))
        )
        self.file_index: dict[str, FileInfo] = {}
        self.dependency_graph: dict[str, list[str]] = {}
        self.import_graph: dict[str, list[str]] = {}
        self.core_files: list[str] = []
        self._last_scan: datetime | None = None
        # H1-②b-⑩：autoload=True 优先缓存回载（缺失/损坏如实回落全量重扫），
        # 缺省 False=构造即全量重扫（既有契约逐字不变）。
        if not (autoload and self.load_cache()):
            self._build_index()

    def _build_index(self):
        logger.info("扫描项目: %s", self.repo_root)
        start = datetime.now()
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext in LANG_MAP:
                    rel_path = str(fpath.relative_to(self.repo_root))
                    try:
                        stat = fpath.stat()
                        self.file_index[rel_path] = FileInfo(
                            path=rel_path,
                            language=LANG_MAP[ext],
                            lines=sum(1 for _ in open(fpath, errors="replace")),
                            size_bytes=stat.st_size,
                            last_modified=datetime.fromtimestamp(stat.st_mtime),
                        )
                    except Exception:
                        pass
        self._build_dependency_graph()
        self._identify_core_files()
        self._last_scan = start
        logger.info(
            "扫描完成: %d文件, %d核心, %.1fs",
            len(self.file_index),
            len(self.core_files),
            (datetime.now() - start).total_seconds(),
        )

    def _build_dependency_graph(self):
        for rel_path, info in self.file_index.items():
            full_path = self.repo_root / rel_path
            self.import_graph[rel_path] = self._extract_imports(full_path, info.language)
        self._rebuild_reverse_edges()

    def _rebuild_reverse_edges(self):
        """依赖图（被依赖方→依赖方）从 import 图单点派生（全量/增量共用禁手抄）。"""
        dep: dict[str, list[str]] = {}
        for rel_path, imports in self.import_graph.items():
            for imp in imports:
                dep.setdefault(imp, [])
                if rel_path not in dep[imp]:
                    dep[imp].append(rel_path)
        self.dependency_graph = dep

    def _extract_imports(self, path: Path, language: str) -> list[str]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        if language == "python":
            return self._extract_python_imports(path, content)
        elif language in ("typescript", "javascript"):
            return self._extract_js_imports(path, content)
        return []

    def _extract_python_imports(self, path: Path, content: str) -> list[str]:
        imports = []
        try:
            tree = ast.parse(content, filename=str(path))
        except SyntaxError:
            return imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = self._resolve_python_import(alias.name, path)
                    if resolved:
                        imports.append(resolved)
            elif isinstance(node, ast.ImportFrom) and node.module:
                resolved = self._resolve_python_import(node.module, path)
                if resolved:
                    imports.append(resolved)
        return imports

    def _resolve_python_import(self, module_name: str, from_path: Path) -> str | None:
        parts = module_name.split(".")
        candidate = from_path.parent / "/".join(parts)
        for suffix in [".py", "/__init__.py"]:
            try:
                resolved = (
                    candidate.with_suffix(suffix)
                    if suffix.startswith(".")
                    else candidate / suffix.lstrip("/")
                )
                rel = resolved.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except (ValueError, OSError):
                pass
        candidate = self.repo_root / "/".join(parts)
        for suffix in [".py", "/__init__.py"]:
            try:
                resolved = (
                    candidate.with_suffix(suffix)
                    if suffix.startswith(".")
                    else candidate / suffix.lstrip("/")
                )
                rel = resolved.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except (ValueError, OSError):
                pass
        return None

    def _extract_js_imports(self, path: Path, content: str) -> list[str]:
        imports = []
        for m in re.finditer(r"""(?:import|from)\s+.*?['"]([^'"]+)['"]""", content):
            resolved = self._resolve_js_import(m.group(1), path)
            if resolved:
                imports.append(resolved)
        for m in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", content):
            resolved = self._resolve_js_import(m.group(1), path)
            if resolved:
                imports.append(resolved)
        return imports

    def _resolve_js_import(self, import_path: str, from_path: Path) -> str | None:
        if not import_path.startswith("."):
            return None
        candidate = (from_path.parent / import_path).resolve()
        for suffix in [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"]:
            test = (
                candidate.with_suffix(suffix)
                if "." in suffix and "/" not in suffix
                else candidate.parent / (candidate.name + suffix)
            )
            try:
                rel = test.relative_to(self.repo_root)
                if str(rel) in self.file_index:
                    return str(rel)
            except ValueError:
                continue
        return None

    def _identify_core_files(self):
        dep_counts = [(f, len(deps)) for f, deps in self.dependency_graph.items()]
        dep_counts.sort(key=lambda x: x[1], reverse=True)
        cutoff = max(1, int(len(dep_counts) * (1 - CORE_FILE_PERCENTILE)))
        self.core_files = [f for f, cnt in dep_counts[:cutoff] if cnt > 0]
        # 标志随重算复位（增量刷新复算时禁残留旧核心标志）。
        core_set = set(self.core_files)
        for p, info in self.file_index.items():
            info.is_core = p in core_set

    def rebuild(self):
        """全量重建（清旧态后重扫——重复调用幂等，禁残留已删文件幽灵行）。"""
        self.file_index.clear()
        self.dependency_graph.clear()
        self.import_graph.clear()
        self.core_files.clear()
        self._build_index()

    async def refresh_incremental(self):
        """增量刷新（H1-②b-⑩ 接线前定形，修前实现从未被消费故语义先定形）。

        语义：stat 对账为准——无变更零内容读取（think 高频路径的性能本体）；
        内容变更只重读变更文件并重提 import 边；拓扑变更（新增/删除文件）如实
        回落 rebuild 全量重建（import 解析依赖文件全集——新增文件可能补上既有
        文件早前解析不出的 import 边，增量补边会漏，解析正确性优先禁漏边）。
        修前 4 处根缺陷（接线即炸/接线即错，逐条收口）：①迭代中 del 必抛
        RuntimeError（dict changed size during iteration）②新增文件永不发现
        （只遍历既有 file_index）③变更文件 import 边永不同步（图陈旧）④删除
        文件幽灵边残留（import_graph/dependency_graph 不清）。
        """
        if not self._last_scan:
            self.rebuild()
            return
        seen: set[str] = set()
        changed: list[str] = []
        topology_changed = False
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext not in LANG_MAP:
                    continue
                rel_path = str(fpath.relative_to(self.repo_root))
                try:
                    stat = fpath.stat()
                except OSError:
                    continue
                seen.add(rel_path)
                info = self.file_index.get(rel_path)
                if info is None:
                    topology_changed = True
                    continue
                mod_time = datetime.fromtimestamp(stat.st_mtime)
                if mod_time > info.last_modified or stat.st_size != info.size_bytes:
                    info.lines = sum(1 for _ in open(fpath, errors="replace"))
                    info.size_bytes = stat.st_size
                    info.last_modified = mod_time
                    changed.append(rel_path)
        if any(p not in seen for p in self.file_index):
            topology_changed = True
        if topology_changed:
            self.rebuild()
            return
        for rel_path in changed:
            self.import_graph[rel_path] = self._extract_imports(
                self.repo_root / rel_path, self.file_index[rel_path].language
            )
        if changed:
            self._rebuild_reverse_edges()
            self._identify_core_files()
        self._last_scan = datetime.now()

    def stale_for(self, ttl_s: float) -> bool:
        """距上次扫描是否超 TTL（调度判定单一口）。"""
        if self._last_scan is None:
            return True
        return (datetime.now() - self._last_scan).total_seconds() > ttl_s

    def is_core_file(self, path: str) -> bool:
        return path in self.core_files

    def get_dependents(self, path: str) -> list[str]:
        return self.dependency_graph.get(path, [])

    def get_impact(self, path: str) -> ImpactAnalysis:
        direct = self.get_dependents(path)
        indirect = self._transitive_deps(path)
        level = (
            "critical"
            if len(direct) > 10
            else "high"
            if len(direct) > 5
            else "medium"
            if len(direct) > 2
            else "low"
        )
        return ImpactAnalysis(direct_impact=direct, indirect_impact=indirect, risk_level=level)

    def _transitive_deps(self, path: str) -> list[str]:
        visited, queue = set(), self.get_dependents(path).copy()
        while queue:
            f = queue.pop(0)
            if f not in visited:
                visited.add(f)
                queue.extend(self.get_dependents(f))
        return list(visited)

    def get_stats(self) -> dict:
        return {
            "total_files": len(self.file_index),
            "core_files": len(self.core_files),
            "total_dependencies": sum(len(d) for d in self.dependency_graph.values()),
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
        }

    def save_cache(self):
        data = {
            "files": {
                k: {
                    "language": v.language,
                    "lines": v.lines,
                    "is_core": v.is_core,
                    # H1-②b-⑩ 加性键：回载对账必需（refresh_incremental 的
                    # 变更判定轴=last_modified/size_bytes，缺则回载不可信）。
                    "size_bytes": v.size_bytes,
                    "last_modified": v.last_modified.isoformat(),
                }
                for k, v in self.file_index.items()
            },
            "dependency_graph": self.dependency_graph,
            "import_graph": self.import_graph,
            "core_files": self.core_files,
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def load_cache(self) -> bool:
        """缓存回载（H1-②b-⑩）：成功 True；缺失/损坏/缺对账键=False 如实回落实建
        （禁 fabricate 空索引——回载失败真因 logger.warning 留痕可见）。"""
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            files = data["files"]
            ig = data["import_graph"]
            dg = data["dependency_graph"]
            cf = data["core_files"]
            if not isinstance(files, dict) or not isinstance(ig, dict):
                raise ValueError("缓存结构非对象")
            if not isinstance(dg, dict) or not isinstance(cf, list):
                raise ValueError("缓存结构非对象/列表")
            index: dict[str, FileInfo] = {}
            for rel_path, meta in files.items():
                index[rel_path] = FileInfo(
                    path=rel_path,
                    language=meta["language"],
                    lines=int(meta["lines"]),
                    size_bytes=int(meta["size_bytes"]),
                    last_modified=datetime.fromisoformat(meta["last_modified"]),
                    is_core=bool(meta.get("is_core", False)),
                )
            self.file_index = index
            self.import_graph = {k: list(v) for k, v in ig.items()}
            self.dependency_graph = {k: list(v) for k, v in dg.items()}
            self.core_files = list(cf)
            self._last_scan = (
                datetime.fromisoformat(data["last_scan"]) if data.get("last_scan") else None
            )
            return True
        except Exception as e:
            logger.warning("project_memory 缓存回载失败（回落实建）: %s", e)
            return False


# ── 共享调度（H1-②b-⑩）────────────────────────────────────────────
# 修前：brain think 带 repo_root 每请求构造 ProjectMemory=每请求全仓重扫
# （H1-② 定性「缓存增量无调度」），refresh_incremental/save_cache 全仓零消费。
# 现：同 repo_root 复用单实例 + TTL 节流增量刷新 + 缓存落盘/回载。

DEFAULT_REFRESH_TTL_S = 60.0

_SHARED: dict[tuple[str, str], ProjectMemory] = {}
_SHARED_LOCK = threading.Lock()


def project_memory_cache_file(repo_root: str) -> Path:
    """每 repo_root 独立缓存文件（env PROJECT_MEMORY_CACHE_DIR 测试隔离）。"""
    cache_dir = os.environ.get("PROJECT_MEMORY_CACHE_DIR") or os.path.expanduser("~/.opensoul")
    digest = hashlib.sha1(str(Path(repo_root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"project_memory_{digest}.json"


async def acquire_project_memory(
    repo_root: str,
    ttl_s: float = DEFAULT_REFRESH_TTL_S,
    force_refresh: bool = False,
) -> ProjectMemory:
    """共享 ProjectMemory 调度口（H1-②b-⑩）。

    调度语义（诚实边界）：刷新按访问时点 TTL 节流（非后台线程周期调度）——
    同 repo_root 在 TTL 内零扫描直接复用（think 高频路径收益本体），过期/强制
    才增量刷新；首取走缓存回载（load_cache 失败如实回落构造全量重扫）。
    构造/落盘故障不阻断调用方（旁路纪律：logger.warning 留真因，沿用既有索引）。
    """
    root_key = str(Path(repo_root).resolve())
    cache_file = project_memory_cache_file(root_key)
    key = (root_key, str(cache_file))
    pm = _SHARED.get(key)
    created = False
    if pm is None:
        with _SHARED_LOCK:
            pm = _SHARED.get(key)
            if pm is None:
                pm = ProjectMemory(repo_root, cache_path=str(cache_file), autoload=True)
                _SHARED[key] = pm
                created = True
    if created or force_refresh or pm.stale_for(ttl_s):
        try:
            if force_refresh or pm.stale_for(ttl_s):
                await pm.refresh_incremental()
            pm.save_cache()
        except Exception as e:
            logger.warning("project_memory 刷新/落盘失败（沿用既有索引，不阻断）: %s", e)
    return pm

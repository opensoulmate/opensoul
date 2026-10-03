"""gene测试隔离护栏（2026-10-03测试隔离轮）——测试面绝不写生产skills.db。

背景：gene API测试此前走live :8090，每次全量跑往 ~/.hermes/opensoul/gene/skills.db
（生产库）累积测试技能行（单轮~14行，存量一度28行"wire解码测试-*/遥测接线测试-*"），
测试行挤占find_relevant候选窗口曾引爆假失败（dev-reports 2026-10-03 14:55轮遗留#3）。
conftest.gene_client已把API测试接进tmp隔离库（真实router+tmp单例）；本文件钉住
隔离不许回退：

1. API写入只落tmp库，生产库行数纹丝不动（数据面护栏）；
2. gene测试模块禁止再请求live client fixture（面源码扫描，防有人改回去）；
3. 隔离app挂的是真实gene/gene_templates路由（非stub app）。
"""

import re
import sqlite3
from pathlib import Path

PROD_DB = Path.home() / ".hermes" / "opensoul" / "gene" / "skills.db"


def _prod_count() -> int:
    """生产skills.db当前行数（只读；库不存在=0）。"""
    if not PROD_DB.exists():
        return 0
    with sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True) as conn:
        return conn.execute("select count(*) from learned_skills").fetchone()[0]


_EXTRACT_PAYLOAD = {
    "task_description": "隔离护栏测试-write-only-tmp",
    "execution_log": (
        '[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},'
        ' {"function": {"name": "write_file", "arguments": {"path": "b.txt"}}}]'
    ),
    "success": True,
}


class TestGeneApiDataIsolation:
    def test_extract_lands_only_in_tmp_db(self, gene_client):
        """extract真实走router→行落tmp库；生产skills.db行数不变。"""
        before = _prod_count()
        r = gene_client.post("/api/gene/skill/extract", json=_EXTRACT_PAYLOAD)
        assert r.status_code == 200, r.text
        skill_id = r.json()["skill_id"]

        tmp_db = gene_client.app.state.gene_skills_db
        assert Path(tmp_db) != PROD_DB  # 隔离面根本没指向生产库
        with sqlite3.connect(tmp_db) as conn:
            n = conn.execute(
                "select count(*) from learned_skills where skill_id=?", (skill_id,)
            ).fetchone()[0]
        assert n == 1, f"技能行没落tmp库 {tmp_db}"
        assert _prod_count() == before, "生产skills.db被测试写入污染"

    def test_usage_and_delete_also_stay_in_tmp_db(self, gene_client):
        """usage回执+删除同样只作用于tmp库（回执链是真实record_usage路径）。"""
        before = _prod_count()
        r = gene_client.post("/api/gene/skill/extract", json=_EXTRACT_PAYLOAD)
        assert r.status_code == 200, r.text
        skill_id = r.json()["skill_id"]
        ru = gene_client.post(f"/api/gene/skill/{skill_id}/usage", json={"success": False})
        assert ru.status_code == 200, ru.text
        rd = gene_client.delete(f"/api/gene/skill/{skill_id}?reason=isolation-guard")
        assert rd.status_code == 200, rd.text
        assert _prod_count() == before, "生产skills.db被测试写入污染"


class TestNoLiveClientRegression:
    def test_gene_test_modules_never_request_live_client(self):
        """gene测试模块里不允许再出现live client fixture（词边界扫描，gene_client不算）。"""
        tests_dir = Path(__file__).parent
        offenders = []
        for f in sorted(tests_dir.glob("test_gene*.py")):
            if f.name == Path(__file__).name:
                continue  # 本护栏文件自身含扫描模式文本，跳过
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if re.search(r"(?<!\w)client(?!\w)", line):
                    offenders.append(f"{f.name}:{i}: {line.strip()}")
        assert not offenders, "gene测试回到live client（生产skills.db将被污染）：\n" + "\n".join(
            offenders
        )


class TestIsolatedAppWiring:
    def test_isolated_app_mounts_real_gene_routes(self, gene_client):
        """隔离app挂的是真实router（镜像src/main.py:554-555的/api/gene挂载）。

        用openapi()看全量路径：新版FastAPI include_router是惰性_IncludedRouter，
        app.routes不再展平子路由（本护栏首版踩过此坑）。
        """
        paths = set(gene_client.app.openapi()["paths"].keys())
        for p in (
            "/api/gene/health",
            "/api/gene/skill/extract",
            "/api/gene/skill/recommend",
            "/api/gene/skill/audit",
            "/api/gene/adapter-templates",
            "/api/gene/instances",
        ):
            assert p in paths, f"隔离app缺路由 {p}（router挂载≠main.py，测试面失真）"

    def test_template_write_goes_to_tmp_engine_storage(self, gene_client, tmp_path):
        """模板POST写入TemplateEngine存储也走tmp（不落生产~/.opensoul/templates）。"""
        prod_dir = Path.home() / ".opensoul" / "templates"
        r = gene_client.post(
            "/api/gene/templates",
            json={
                "name": "isolation_guard_template",
                "category": "agent",
                "description": "isolation guard",
                "config": {"model": "x", "prompt": "y"},
                "variables": [],
            },
        )
        assert r.status_code == 200, r.text
        tid = r.json()["template_id"]
        tmp_file = tmp_path / "templates" / f"{tid}.json"
        assert tmp_file.exists(), f"模板没落tmp存储 {tmp_file}"
        assert "isolation_guard_template" in tmp_file.read_text()
        assert not (prod_dir / f"{tid}.json").exists(), "生产模板目录被测试写入污染"
        rd = gene_client.delete(f"/api/gene/templates/{tid}")
        assert rd.status_code == 200, rd.text

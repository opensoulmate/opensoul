"""TemplateEngine生产存储写入护栏（2026-10-03轮，销17:01遗留#3）。

护栏三件套参照kilocode discovery.ts"origin钉死+原子swap+校验"：
- 校验：template_id白名单字符集——堵死 `_save_template` 的
  `storage_dir / f"{template_id}.json"` 路径穿越写/删（id经POST /api/gene/templates、
  /import、clone完全用户可控）
- 钉死：落盘文件名==template_id才加载（防伪造来源假模板）
- 原子swap：tmp+os.replace（崩溃不留半截JSON被静默跳过）
"""

import json

import pytest

from src.gene.templates import TemplateEngine, TemplateValidationError


def _mk_engine(tmp_path):
    return TemplateEngine(storage_dir=tmp_path / "templates")


def _payload(tid, **over):
    data = {
        "template_id": tid,
        "name": "护栏测试模板",
        "category": "dev_norm",
        "description": "d",
    }
    data.update(over)
    return data


EVIL_IDS = [
    "../evil",  # 目录穿越
    "a/b",  # 路径分隔符
    "..",
    ".",  # 前导点
    ".hidden",
    "a\\b",  # 反斜杠（Windows风格穿越）
    "x" * 200,  # 超长
    "中",  # 非ASCII
    "a b",  # 空格
    "-lead",  # 首字符非字母数字
    "a" * 129,  # 超出128上限
]


class TestCreateWriteGuard:
    @pytest.mark.parametrize("evil", EVIL_IDS)
    def test_create_rejects_bad_ids(self, tmp_path, evil):
        eng = _mk_engine(tmp_path)
        with pytest.raises(TemplateValidationError):
            eng.create_template(_payload(evil))
        # 拒绝=不触盘：storage_dir为空，storage_dir之外零新文件
        assert list((tmp_path / "templates").iterdir()) == []
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    def test_create_empty_or_missing_id_autogenerates(self, tmp_path):
        eng = _mk_engine(tmp_path)
        for payload in (_payload(""), {"name": "n", "category": "dev_norm"}):
            t = eng.create_template(payload)
            assert t.template_id.startswith("user-")
            assert (tmp_path / "templates" / f"{t.template_id}.json").exists()

    def test_create_missing_or_empty_required_fields(self, tmp_path):
        eng = _mk_engine(tmp_path)
        for payload in (
            {"name": "n"},  # 缺category
            {"category": "dev_norm"},  # 缺name
            {"name": "", "category": "dev_norm"},  # 空name
            {"name": "n", "category": "  "},  # 空白category
        ):
            with pytest.raises(TemplateValidationError):
                eng.create_template(payload)
        assert list((tmp_path / "templates").iterdir()) == []


class TestAtomicSwap:
    def test_save_atomic_no_tmp_leftover(self, tmp_path, monkeypatch):
        eng = _mk_engine(tmp_path)
        calls = []
        real_replace = __import__("os").replace
        monkeypatch.setattr(
            "src.gene.templates.os.replace",
            lambda src, dst: (calls.append((str(src), str(dst))), real_replace(src, dst))[1],
        )
        eng.create_template(_payload("t-atomic"))
        # 原子swap被真实调用：tmp→final
        assert calls and calls[0][0].endswith(".t-atomic.json.tmp")
        assert calls[0][1].endswith("t-atomic.json")
        # 终态：正式文件存在+零tmp残留+内容可解析
        final = tmp_path / "templates" / "t-atomic.json"
        assert final.exists()
        assert [p.name for p in (tmp_path / "templates").iterdir()] == ["t-atomic.json"]
        assert json.loads(final.read_text())["template_id"] == "t-atomic"

    def test_roundtrip_reload(self, tmp_path):
        eng = _mk_engine(tmp_path)
        eng.create_template(_payload("t-round"))
        eng2 = TemplateEngine(storage_dir=tmp_path / "templates")
        t = eng2.get_template("t-round")
        assert t is not None and t.name == "护栏测试模板"


class TestOriginPinning:
    def test_load_rejects_filename_id_mismatch(self, tmp_path, caplog):
        store = tmp_path / "templates"
        store.mkdir(parents=True)
        (store / "evil.json").write_text(json.dumps(_payload("other-id")))
        with caplog.at_level("WARNING"):
            eng = TemplateEngine(storage_dir=store)
        assert eng.get_template("other-id") is None
        assert eng.get_template("evil") is None
        assert any("template load rejected" in r.message for r in caplog.records)

    def test_load_rejects_bad_id_in_file(self, tmp_path):
        store = tmp_path / "templates"
        store.mkdir(parents=True)
        (store / "x.json").write_text(json.dumps(_payload("../y")))
        eng = TemplateEngine(storage_dir=store)
        assert eng.get_template("../y") is None
        assert eng.get_template("x") is None


class TestImportCloneDeleteGuard:
    def test_import_rejects_bad_id(self, tmp_path):
        eng = _mk_engine(tmp_path)
        for evil in EVIL_IDS:
            t, msg = eng.import_template(_payload(evil))
            assert t is None and "invalid template_id" in msg
        assert list((tmp_path / "templates").iterdir()) == []

    def test_clone_rejects_bad_new_id(self, tmp_path):
        eng = _mk_engine(tmp_path)
        eng.create_template(_payload("src-ok"))
        t, msg = eng.clone_template("src-ok", new_id="../c")
        assert t is None and "invalid template_id" in msg
        assert not (tmp_path / "c.json").exists()

    def test_delete_rejects_bad_id(self, tmp_path):
        eng = _mk_engine(tmp_path)
        victim = tmp_path / "victim.json"
        victim.write_text("{}")
        assert eng.delete_template("../victim") is False
        assert victim.exists()


class TestApiWiring:
    """API层=真实router（gene_client隔离fixture），护栏在HTTP边界生效。"""

    def test_api_post_bad_id_400(self, gene_client):
        resp = gene_client.post(
            "/api/gene/templates",
            json={"template_id": "../pwn", "name": "n", "category": "dev_norm"},
        )
        assert resp.status_code == 400
        assert "invalid template_id" in resp.json()["detail"]

    def test_api_post_empty_required_field_400(self, gene_client):
        resp = gene_client.post(
            "/api/gene/templates",
            json={"template_id": "t-ok", "name": "", "category": "dev_norm"},
        )
        assert resp.status_code == 400
        assert "required field" in resp.json()["detail"]

    def test_api_post_valid_200_and_listed(self, gene_client):
        resp = gene_client.post(
            "/api/gene/templates",
            json={"template_id": "t-guard-ok", "name": "n", "category": "dev_norm"},
        )
        assert resp.status_code == 200
        resp = gene_client.get("/api/gene/templates")
        body = resp.json()
        templates = body if isinstance(body, list) else body.get("templates", [])
        ids = [t["template_id"] for t in templates]
        assert "t-guard-ok" in ids

    def test_api_import_bad_id_reports_per_item(self, gene_client):
        resp = gene_client.post(
            "/api/gene/import",
            json={"templates": [{"template_id": "../pwn2", "name": "n", "category": "dev_norm"}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 0
        assert "invalid template_id" in body["results"][0]["message"]

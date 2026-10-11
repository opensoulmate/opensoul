"""Admin 净URL回退测试：/admin/<page>（无 .html）直入/刷新不再 404。

Next 静态导出产出 <page>.html 形状，直入净 URL 此前 404（目录转跳后寻
index.html 失败 / 404.html 兜底）。AdminStaticFiles 对 404 回退同名
<page>.html，真缺失页仍如实 404。

① 单元层（tmp 目录，零构建产物依赖）：回退命中/尾斜杠/真缺失 404/带扩展名不回退/静态正常
② 集成层（src.main 真挂载 + 真构建产物）：净 URL 回包与 .html 直链逐字节一致
"""

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.main import AdminStaticFiles

ADMIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "static", "admin"
)


def _client(tmp_path):
    (tmp_path / "index.html").write_text("<html>index</html>")
    (tmp_path / "page.html").write_text("<html>page-content</html>")
    (tmp_path / "404.html").write_text("<html>not-found</html>")
    (tmp_path / "asset.js").write_text("console.log(1)")
    app = FastAPI()
    app.mount("/admin", AdminStaticFiles(directory=str(tmp_path), html=True), name="admin")
    return TestClient(app)


# ── ① 单元层：回退语义 ──────────────────────────────────────


def test_clean_url_falls_back_to_html(tmp_path):
    """净 URL 回退到同名 .html，回包与 .html 直链逐字节一致。"""
    c = _client(tmp_path)
    clean = c.get("/admin/page")
    direct = c.get("/admin/page.html")
    assert clean.status_code == 200
    assert direct.status_code == 200
    assert clean.content == direct.content == b"<html>page-content</html>"


def test_clean_url_trailing_slash_falls_back(tmp_path):
    """尾斜杠净 URL（/admin/page/）同样回退命中。"""
    c = _client(tmp_path)
    r = c.get("/admin/page/")
    assert r.status_code == 200
    assert r.content == b"<html>page-content</html>"


def test_missing_page_still_404(tmp_path):
    """真缺失页如实 404（回退只认真存在的 <name>.html），404.html 兜底照旧。"""
    c = _client(tmp_path)
    r = c.get("/admin/no-such-page")
    assert r.status_code == 404
    assert r.content == b"<html>not-found</html>"


def test_extension_paths_not_fallback(tmp_path):
    """带扩展名的资源路径不做页面回退（缺 .js 不会被硬塞页面 HTML）。"""
    c = _client(tmp_path)
    assert c.get("/admin/no-such-file.js").status_code == 404
    assert c.get("/admin/asset.js").status_code == 200  # 正常静态照常


def test_index_and_html_direct_untouched(tmp_path):
    """/admin/（index）与 .html 直链语义零变化。"""
    c = _client(tmp_path)
    assert c.get("/admin/").content == b"<html>index</html>"
    assert c.get("/admin/page.html").status_code == 200
    assert c.get("/admin/404.html").status_code == 200


# ── ② 集成层：src.main 真挂载 + 真构建产物 ──────────────────


def _real_page_stem():
    """取一个真实构建页面（非 404/index 兜底页）。"""
    stems = sorted(
        f[:-5]
        for f in os.listdir(ADMIN_DIR)
        if f.endswith(".html") and f not in ("404.html", "index.html")
    )
    assert stems, f"admin 构建产物无页面: {ADMIN_DIR}"
    return stems[0]


def test_real_mount_clean_url_matches_direct():
    """真挂载面（src.main /admin）净 URL 200 且与 .html 直链逐字节一致。"""
    from src.main import app

    stem = _real_page_stem()
    with TestClient(app) as c:
        clean = c.get(f"/admin/{stem}")
        direct = c.get(f"/admin/{stem}.html")
    assert clean.status_code == 200, f"/admin/{stem} -> {clean.status_code}"
    assert direct.status_code == 200
    assert clean.content == direct.content


def test_real_mount_missing_page_404():
    """真挂载面缺失页仍 404，回退不谎报。"""
    from src.main import app

    with TestClient(app) as c:
        r = c.get("/admin/definitely-missing-page-xyz")
    assert r.status_code == 404

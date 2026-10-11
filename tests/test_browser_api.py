"""Browser 能力测试（docs/roadmap.md P0"浏览器"最小切片）。

① 抽取层纯函数（离线）：标题/描述/正文/链接、相对链接解析、脚本剥离、截断如实标注
② 抓取层真网络（本地 HTTP 服务，真 httpx 栈）：HTML/text/无 Content-Type/二进制/404/连接失败/非法 scheme
③ API 层真 router 挂载：/health、/fetch 成功形状、失败 status=error 禁谎报 ok、缺 url 422
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.sense.web import (
    MAX_LINKS,
    MAX_TEXT,
    WebFetchError,
    extract_html,
    extract_text,
    fetch_page,
)

# ── 本地 HTTP 服务（真网络栈，零外网依赖） ─────────────────────


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音测试输出
        pass

    def do_GET(self):
        if self.path == "/page":
            body = (
                b"<html><head><title>Fallback Title</title>"
                b'<meta name="description" content="page description">'
                b"</head><body><script>var x=1;</script><style>.a{}</style>"
                b"<h1>Hello</h1><p>Some   text\n here</p>"
                b'<a href="/rel">rel</a><a href="https://example.com/abs">abs</a>'
                b'<a href="#top">skip</a><a href="mailto:x@y.z">mail</a>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/og":
            body = (
                b'<html><head><meta property="og:title" content="OG Title">'
                b'<meta property="og:description" content="OG Desc">'
                b"<title>Ignored</title></head><body>t</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/text":
            body = b"plain  text\ncontent"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/no-ctype":
            body = b"<html><head><title>NoCtype</title></head><body>x</body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/binary":
            body = b"\x00\x01\x02\x03"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"missing"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class _Server:
    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _run(coro):
    return asyncio.run(coro)


# ── ① 抽取层（纯函数） ──────────────────────────────────────


def test_extract_html_title_description_text_links():
    html = (
        "<html><head><title>  Titled Page  </title>"
        '<meta name="description" content="a description"></head>'
        "<body><script>bad()</script><style>.x{}</style>"
        "<p>Hello   world\nagain</p>"
        '<a href="/rel">rel link</a><a href="https://x.example/abs">abs</a>'
        '<a href="javascript:void(0)">js</a><a href="#anchor">hash</a>'
        "</body></html>"
    )
    out = extract_html(html, "https://base.example/dir/index.html")
    assert out["title"] == "Titled Page"
    assert out["description"] == "a description"
    assert "Hello world again" in out["text"]
    assert "bad()" not in out["text"] and ".x{}" not in out["text"]
    urls = [l["url"] for l in out["links"]]
    assert urls == ["https://base.example/rel", "https://x.example/abs"]
    assert out["links"][0]["text"] == "rel link"
    assert out["text_truncated"] is False and out["links_truncated"] is False


def test_extract_html_og_overrides_and_dedup():
    html = (
        '<html><head><meta property="og:title" content="OG">'
        '<meta property="og:description" content="OGD"><title>plain</title></head>'
        '<body><a href="/a">one</a><a href="/a">dup</a><a href="/a">dup2</a></body></html>'
    )
    out = extract_html(html, "https://b.example/")
    assert out["title"] == "OG" and out["description"] == "OGD"
    assert [l["url"] for l in out["links"]] == ["https://b.example/a"]


def test_extract_html_truncation_flags():
    html = "<html><body>" + "word " * (MAX_TEXT // 4) + "</body></html>"
    out = extract_html(html, "https://b.example/")
    assert out["text_truncated"] is True and len(out["text"]) == MAX_TEXT

    links = "".join(f'<a href="/p{i}">l</a>' for i in range(MAX_LINKS + 5))
    out = extract_html(f"<html><body>{links}</body></html>", "https://b.example/")
    assert len(out["links"]) == MAX_LINKS and out["links_truncated"] is True


def test_extract_text_passthrough():
    out = extract_text("raw  text")
    assert out["text"] == "raw  text" and out["links"] == []
    long_raw = "x" * (MAX_TEXT + 10)
    assert extract_text(long_raw)["text_truncated"] is True


# ── ② 抓取层（真 httpx 栈，本地真 HTTP） ──────────────────────


def test_fetch_page_html_real_http():
    with _Server() as srv:
        out = _run(fetch_page(f"{srv.base}/page"))
    assert out["status"] == "ok"
    assert out["status_code"] == 200
    assert out["content_type"] == "text/html"
    assert out["title"] == "Fallback Title"
    assert out["description"] == "page description"
    assert "Hello Some text here" in out["text"]
    assert out["links"][0]["url"] == f"{srv.base}/rel"
    assert out["body_truncated"] is False


def test_fetch_page_text_and_no_content_type():
    with _Server() as srv:
        text_out = _run(fetch_page(f"{srv.base}/text"))
        no_ctype_out = _run(fetch_page(f"{srv.base}/no-ctype"))
    assert text_out["status"] == "ok" and text_out["text"] == "plain  text\ncontent"
    assert no_ctype_out["status"] == "ok" and no_ctype_out["title"] == "NoCtype"


def test_fetch_page_reports_real_status_code():
    """404 页面：抓取成功≠页面成功，status_code 如实报 404，正文照常抽取。"""
    with _Server() as srv:
        out = _run(fetch_page(f"{srv.base}/missing"))
    assert out["status"] == "ok" and out["status_code"] == 404
    assert "missing" in out["text"]


def test_fetch_page_binary_content_type_rejected():
    with _Server() as srv:
        try:
            _run(fetch_page(f"{srv.base}/binary"))
        except WebFetchError as exc:
            assert "unsupported content-type" in str(exc)
        else:
            raise AssertionError("二进制内容类型必须如实报错，禁静默回空包")


def test_fetch_page_bad_scheme_and_unreachable():
    for bad in ("ftp://example.com/x", "file:///etc/passwd", "not-a-url"):
        try:
            _run(fetch_page(bad))
        except WebFetchError as exc:
            assert "unsupported url" in str(exc)
        else:
            raise AssertionError(f"{bad} 必须拒绝")
    try:
        _run(fetch_page("http://127.0.0.1:1/never"))
    except WebFetchError as exc:
        msg = str(exc)
        assert msg and "unsupported" not in msg  # 网络失败如实报网络错，不冒充 URL 非法
    else:
        raise AssertionError("连接失败必须如实报错")


# ── ③ API 层（真 router 挂载） ───────────────────────────────


def _api_client():
    app = FastAPI()
    from src.api.browser_api import router

    app.include_router(router, prefix="/api/browser")
    return TestClient(app)


def test_api_health_and_fetch_ok():
    c = _api_client()
    assert c.get("/api/browser/health").json() == {"status": "ok", "component": "Browser"}
    with _Server() as srv:
        resp = c.post("/api/browser/fetch", json={"url": f"{srv.base}/og"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok" and body["component"] == "Browser"
    assert body["title"] == "OG Title" and body["description"] == "OG Desc"
    assert body["url"] == f"{srv.base}/og"


def test_api_fetch_error_not_fake_ok():
    c = _api_client()
    for url in ("ftp://example.com/x", "http://127.0.0.1:1/never"):
        resp = c.post("/api/browser/fetch", json={"url": url})
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "error", f"{url} 失败禁谎报 ok"
        assert body["component"] == "Browser" and body["error"]
        assert body["url"] == url


def test_api_fetch_missing_url_422():
    c = _api_client()
    assert c.post("/api/browser/fetch", json={}).status_code == 422

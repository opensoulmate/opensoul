"""sense 扩展：网页访问 + 信息抓取（docs/roadmap.md P0 缺失能力"浏览器"最小切片）。

能力边界（如实，留后续）：
- 静态 HTML 抓取 + 标题/描述/正文/链接抽取；text/* 原样回文本；
- 不做 JS 渲染（动态页只拿得到初始 HTML）、不做表单填写/RPA、不做搜索。
- 本机/局域网 URL 不封禁是特性（Agent 操作的就是这台电脑），非漏判。

设计纪律：
- 抽取层（extract_html/extract_text）是纯函数，离线可测；
- 抓取层（fetch_page）只在网络 I/O 处抛 WebFetchError，message 面向 API 回包，
  由调用方按 C2 纪律落 {"status": "error", ...}，禁谎报 ok。
"""

from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 15.0
MAX_BYTES = 2_000_000  # 抓取体积上限（2MB），超出截断并如实标注
MAX_TEXT = 50_000  # 正文回包上限（字符）
MAX_LINKS = 200
USER_AGENT = "OpenSoul-Browser/1.0"


class WebFetchError(Exception):
    """抓取失败（URL 非法/网络失败/内容类型不支持），消息直接面向回包。"""


def _meta_content(tag) -> str:
    """meta content 值统一转 str（bs4 类型标注是 AttributeValueList，运行时是 str）。"""
    value = tag.get("content") if tag else None
    return value.strip() if isinstance(value, str) else ""


def extract_html(html: str, base_url: str) -> dict:
    """从 HTML 抽取标题/描述/正文/链接（纯函数，离线可测）。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    title = ""
    og_title = soup.find("meta", property="og:title")
    title = _meta_content(og_title)
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    description = _meta_content(soup.find("meta", property="og:description"))
    if not description:
        description = _meta_content(soup.find("meta", attrs={"name": "description"}))

    text = " ".join(soup.get_text(" ").split())
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]

    links = []
    seen = set()
    links_truncated = False
    for a in soup.find_all("a", href=True):
        raw_href = a.get("href")
        href = raw_href.strip() if isinstance(raw_href, str) else ""
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href)
        if urlparse(url).scheme not in ("http", "https"):
            continue
        if url in seen:
            continue
        seen.add(url)
        if len(links) >= MAX_LINKS:
            links_truncated = True
            break
        links.append({"text": " ".join(a.get_text(" ").split())[:120], "url": url})

    return {
        "title": title,
        "description": description,
        "text": text,
        "text_truncated": text_truncated,
        "links": links,
        "links_truncated": links_truncated,
    }


def extract_text(raw: str) -> dict:
    """text/* 内容：原样文本 + 末尾截断如实标注（形状与 extract_html 对齐）。"""
    text_truncated = len(raw) > MAX_TEXT
    return {
        "title": "",
        "description": "",
        "text": raw[:MAX_TEXT],
        "text_truncated": text_truncated,
        "links": [],
        "links_truncated": False,
    }


async def fetch_page(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """抓取 URL 并抽取内容。成功回包 status="ok"；失败抛 WebFetchError（禁谎报 ok）。

    非 2xx 响应仍如实抓取并回报 status_code（抓取成功≠页面成功，字段说真话）。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WebFetchError(f"unsupported url: {url!r} (only http/https)")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            async with client.stream("GET", url) as resp:
                content_type = (
                    (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                )
                charset = resp.charset_encoding or "utf-8"
                chunks: list[bytes] = []
                size = 0
                body_truncated = False
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES:
                        body_truncated = True
                        break
                body = b"".join(chunks)[:MAX_BYTES]
                final_url = str(resp.url)
                status_code = resp.status_code
    except httpx.HTTPError as exc:
        raise WebFetchError(f"{type(exc).__name__}: {exc}") from exc

    if content_type.startswith("text/html") or content_type == "application/xhtml+xml":
        extracted = extract_html(body.decode(charset, errors="replace"), final_url)
    elif content_type.startswith("text/"):
        extracted = extract_text(body.decode(charset, errors="replace"))
    elif not content_type and body.lstrip()[:1] == b"<":
        # 无 Content-Type 但像 HTML：按 HTML 抽，别把网页误报成不支持
        extracted = extract_html(body.decode(charset, errors="replace"), final_url)
    else:
        raise WebFetchError(f"unsupported content-type: {content_type or '(none)'}")

    return {
        "status": "ok",
        "component": "Browser",
        "url": url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "body_truncated": body_truncated,
        **extracted,
    }

"""Browser API：网页访问、信息抓取（docs/roadmap.md P0 缺失能力"浏览器"）。

- POST /api/browser/fetch {"url": ...} → 标题/描述/正文/链接 + 真实 status_code；
- 回包 status 字段说成败（C2 纪律：失败回 {"status": "error", "error": ...}，
  HTTP 保持 200 沿用统一降级惯例，禁谎报 ok）。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from src.sense.web import DEFAULT_TIMEOUT, WebFetchError, fetch_page

router = APIRouter()


class FetchRequest(BaseModel):
    url: str
    timeout: float = DEFAULT_TIMEOUT


@router.get("/health")
async def browser_health():
    """Browser health check."""
    return {"status": "ok", "component": "Browser"}


@router.post("/fetch")
async def browser_fetch(req: FetchRequest):
    """抓取网页并抽取标题/正文/链接（静态 HTML；不做 JS 渲染，见 sense/web.py 边界）。"""
    timeout = min(max(req.timeout, 1.0), 60.0)
    try:
        return await fetch_page(req.url, timeout=timeout)
    except WebFetchError as exc:
        return {
            "status": "error",
            "component": "Browser",
            "url": req.url,
            "error": str(exc),
        }

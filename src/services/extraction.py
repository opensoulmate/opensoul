import json
import logging
import re
from io import BytesIO

import httpx
from bs4 import BeautifulSoup

from src.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract entities and relations from the following text.
Return a JSON object with two keys:
- "entities": list of {{"name": str, "type": str, "description": str}}
- "relations": list of {{"source": str, "target": str, "relation_type": str}}

Entity types: person, place, concept, event, organization, technology, other

Text:
{text}

Return only valid JSON, no markdown fences."""


# ---------------------------------------------------------------------------
# Document text extraction
# ---------------------------------------------------------------------------


def extract_pdf(data: bytes) -> str:
    """Extract text from a PDF file."""
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        logger.error("PyPDF2 is not installed")
        raise

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def extract_docx(data: bytes) -> str:
    """Extract text from a Word (.docx) file."""
    try:
        from docx import Document
    except ImportError:
        logger.error("python-docx is not installed")
        raise

    doc = Document(BytesIO(data))
    parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def extract_markdown(text: str) -> str:
    """Extract plain text from Markdown, preserving structure."""
    try:
        import markdown

        html = markdown.markdown(text)
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n")
    except ImportError:
        # Fallback: strip markdown syntax with regex
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`[^`]+`", "", text)
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"~~([^~]+)~~", r"\1", text)
        text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_html(html_content: str) -> str:
    """Extract readable text from HTML, removing scripts and styles."""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_text_from_file(data: bytes, content_type: str) -> str:
    """Route to the correct extractor based on content type."""
    extractors = {
        "application/pdf": extract_pdf,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": extract_docx,
        "application/msword": extract_docx,
    }

    extractor = extractors.get(content_type)
    if extractor:
        return extractor(data)

    # Try text-based formats
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    if content_type in ("text/markdown", "text/x-markdown"):
        return extract_markdown(text)
    if content_type in ("text/html", "application/xhtml+xml"):
        return extract_html(text)

    return text


# ---------------------------------------------------------------------------
# LLM-based entity & relation extraction
# ---------------------------------------------------------------------------


async def extract_entities_and_relations(text: str) -> dict:
    """Use LLM to extract entities and relations from text."""
    api_key = settings.llm_api_key
    if not api_key:
        return {"entities": [], "relations": []}

    prompt = EXTRACTION_PROMPT.format(text=text[:4000])
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from bs4 import BeautifulSoup
from pypdf import PdfReader


class DocumentExtractionError(ValueError):
    pass


def _date_from_soup(soup: BeautifulSoup) -> str | None:
    selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "datePublished"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "DC.date"}),
        ("meta", {"name": "dcterms.date"}),
        ("meta", {"name": "publish-date"}),
    ]
    for tag, attrs in selectors:
        node = soup.find(tag, attrs=attrs)
        value = (node.get("content") if node else "") or ""
        if value.strip():
            return value.strip()[:100]
    time_node = soup.find("time", attrs={"datetime": True})
    return str(time_node.get("datetime"))[:100] if time_node else None


def extract_html(html: str | bytes, url: str, source_name: str, retrieved_at: str, max_chars: int = 150000) -> tuple[dict[str, Any], list[dict[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    published = _date_from_soup(soup)
    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        links.append({"href": str(anchor.get("href", "")), "text": anchor.get_text(" ", strip=True)[:300]})
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    for tag in soup.find_all(["form", "button"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    if not text:
        raise DocumentExtractionError("No readable text was found in this HTML page.")
    truncated = len(text) > max_chars
    document = {
        "title": title[:500], "url": url, "source_name": source_name,
        "document_type": "html", "page": None, "publication_date": published,
        "retrieved_at": retrieved_at, "text": text[:max_chars], "document_truncated": truncated,
    }
    return document, links


def extract_pdf(data: bytes, url: str, source_name: str, retrieved_at: str, max_pages: int = 60) -> list[dict[str, Any]]:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise DocumentExtractionError("The PDF is encrypted and cannot be read without credentials.")
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError("The downloaded file could not be read as a PDF.") from exc

    pdf_metadata = reader.metadata
    title = str((pdf_metadata.title if pdf_metadata else None) or url.rsplit("/", 1)[-1] or "Official PDF")
    file_creation_date = str(pdf_metadata.creation_date) if pdf_metadata and pdf_metadata.creation_date else None
    docs = []
    total_pages = len(reader.pages)
    page_limit = min(total_pages, max_pages)
    for page_index in range(page_limit):
        page_number = page_index + 1
        page = reader.pages[page_index]
        try:
            text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            docs.append({
                "title": title[:500], "url": url, "source_name": source_name,
                "document_type": "pdf", "page": page_number,
                "publication_date": None, "file_creation_date": file_creation_date, "retrieved_at": retrieved_at,
                "text": text, "document_truncated": total_pages > page_limit,
                "pages_total": total_pages,
            })
    if not docs:
        raise DocumentExtractionError("No extractable text was found in this PDF.")
    return docs


def chunk_text(text: str, chunk_size: int = 1100, overlap: int = 160) -> list[str]:
    """Split text on word boundaries and try to retain paragraph/sentence boundaries."""
    if chunk_size < 100 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be >= 100 and overlap must be smaller than chunk_size")
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        if end < len(clean):
            sentence_break = max(clean.rfind(". ", start, end), clean.rfind("? ", start, end), clean.rfind("! ", start, end))
            word_break = clean.rfind(" ", start, end)
            boundary = sentence_break + 1 if sentence_break > start + int(chunk_size * 0.65) else word_break
            if boundary > start:
                end = boundary
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean):
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and clean[next_start] != " ":
            next_start += 1
        start = min(next_start + 1, end)
    return chunks

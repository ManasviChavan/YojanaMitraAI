from __future__ import annotations

import ipaddress
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from config.settings import MAX_CRAWL_PAGES, MAX_DOWNLOAD_BYTES, MAX_HTML_TEXT_CHARS, MAX_PAGES_PER_SOURCE, MAX_PDF_PAGES, MIN_REQUEST_INTERVAL_SECONDS, REQUEST_TIMEOUT_SECONDS, SOURCES_PATH
from services.documents import DocumentExtractionError, extract_html, extract_pdf

USER_AGENT = "YojanaMitraAI/0.2"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/xml,text/xml,*/*;q=0.5"}
STOP_WORDS = {
    "what", "which", "where", "when", "how", "for", "the", "and", "are", "any", "available", "scheme", "schemes",
    "government", "benefit", "benefits", "about", "tell", "me", "from", "with", "that", "this", "can", "get",
    "मुझे", "कौन", "क्या", "के", "की", "लिए", "में", "से", "है", "और", "योजना", "योजनाएं", "सरकारी",
    "मला", "काय", "कोणत्या", "आहे", "साठी", "मध्ये", "योजना", "शासकीय",
}


class SourceAccessError(RuntimeError):
    pass


def list_sources() -> list[dict]:
    try:
        items = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceAccessError("The trusted government source registry could not be loaded.") from exc
    if not isinstance(items, list):
        raise SourceAccessError("The trusted government source registry must be a JSON array.")
    checked = []
    for item in items:
        if not isinstance(item, dict):
            raise SourceAccessError("Every trusted source must be a JSON object.")
        name = item.get("name")
        base_url = item.get("base_url")
        domains = item.get("allowed_domains")
        if not isinstance(name, str) or not name.strip() or not isinstance(domains, list) or not domains or not all(isinstance(domain, str) and domain for domain in domains):
            raise SourceAccessError("Each trusted source needs a name and one or more allowed domains.")
        if not isinstance(base_url, str) or not validate_trusted_url(base_url, [item]):
            raise SourceAccessError(f"The base URL for {name!r} is not HTTPS within its allowed domains.")
        if item.get("enabled", True):
            checked.append(item)
    return checked


def validate_trusted_url(url: str, sources: list[dict] | None = None) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
        host = parsed.hostname.lower().rstrip(".")
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        allowed_domains = [domain.lower().rstrip(".") for source in (sources or list_sources()) for domain in source.get("allowed_domains", [])]
        return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)
    except (ValueError, TypeError):
        return False


class PublicSourceFetcher:
    """Fetch only public HTTPS pages within the configured registry and robots policy."""

    def __init__(self, timeout: float = REQUEST_TIMEOUT_SECONDS, max_bytes: int = MAX_DOWNLOAD_BYTES):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.sources = list_sources()
        self.robots: dict[str, tuple[RobotFileParser, list[str]]] = {}
        self.last_request_at: dict[str, float] = {}

    def _rate_limit(self, url: str) -> None:
        origin = urlparse(url).netloc.lower()
        now = time.monotonic()
        previous = self.last_request_at.get(origin)
        if previous is not None:
            delay = MIN_REQUEST_INTERVAL_SECONDS - (now - previous)
            if delay > 0:
                time.sleep(delay)
        self.last_request_at[origin] = time.monotonic()

    def _source_for(self, url: str) -> dict:
        if not validate_trusted_url(url, self.sources):
            raise SourceAccessError("URL is outside the trusted HTTPS government domain registry.")
        host = urlparse(url).hostname.lower()
        for source in self.sources:
            if any(host == d.lower() or host.endswith("." + d.lower()) for d in source.get("allowed_domains", [])):
                return source
        raise SourceAccessError("No trusted source matches this URL.")

    def _robots_for(self, url: str) -> tuple[RobotFileParser, list[str]]:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        parser = RobotFileParser()
        parser.set_url(origin + "/robots.txt")
        try:
            self._rate_limit(origin + "/robots.txt")
            response = self.session.get(origin + "/robots.txt", timeout=self.timeout, allow_redirects=False)
            if response.status_code == 404:
                lines = []
            elif response.status_code == 200:
                if len(response.content) > 512000:
                    raise SourceAccessError("robots.txt is unexpectedly large; retrieval was skipped.")
                lines = response.text.splitlines()
            else:
                raise SourceAccessError(f"robots.txt returned HTTP {response.status_code}; retrieval was skipped.")
        except requests.RequestException as exc:
            raise SourceAccessError("Could not verify robots.txt; retrieval was skipped.") from exc
        parser.parse(lines)
        sitemaps = []
        for line in lines:
            if line.lower().startswith("sitemap:"):
                sitemap = line.split(":", 1)[1].strip()
                if validate_trusted_url(sitemap, self.sources):
                    sitemaps.append(sitemap)
        result = (parser, sitemaps)
        self.robots[origin] = result
        return result

    def _get_bytes(self, url: str) -> tuple[str, bytes, str]:
        current = urldefrag(url)[0]
        for _ in range(4):
            self._source_for(current)
            parser, _ = self._robots_for(current)
            if not parser.can_fetch(USER_AGENT, current):
                raise SourceAccessError("Retrieval is disallowed by the source site's robots.txt.")
            try:
                self._rate_limit(current)
                response = self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                raise SourceAccessError(f"Could not retrieve the approved public page ({type(exc).__name__}).") from exc
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise SourceAccessError("The source returned a redirect without a location.")
                current = urljoin(current, location)
                continue
            if not response.ok:
                status = response.status_code
                response.close()
                raise SourceAccessError(f"The approved source returned HTTP {status}.")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            declared_size = response.headers.get("Content-Length")
            if declared_size and declared_size.isdigit() and int(declared_size) > self.max_bytes:
                response.close()
                raise SourceAccessError("The source page exceeds the configured download limit.")
            body = bytearray()
            try:
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise SourceAccessError("The source page exceeds the configured download limit.")
            except requests.RequestException as exc:
                raise SourceAccessError(f"The approved source response was interrupted ({type(exc).__name__}).") from exc
            finally:
                response.close()
            return current, bytes(body), content_type
        raise SourceAccessError("Too many redirects while retrieving this source.")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def fetch_document(self, url: str) -> tuple[list[dict], list[dict[str, str]]]:
        source = self._source_for(url)
        final_url, body, content_type = self._get_bytes(url)
        source = self._source_for(final_url)
        retrieved_at = self._timestamp()
        if content_type in {"application/pdf", "application/x-pdf"} or final_url.lower().split("?", 1)[0].endswith(".pdf"):
            return extract_pdf(body, final_url, source["name"], retrieved_at, max_pages=MAX_PDF_PAGES), []
        if content_type not in ("text/html", "application/xhtml+xml", ""):
            raise SourceAccessError(f"Unsupported content type: {content_type or 'unknown'}.")
        try:
            doc, links = extract_html(body, final_url, source["name"], retrieved_at, max_chars=MAX_HTML_TEXT_CHARS)
        except DocumentExtractionError as exc:
            raise SourceAccessError(str(exc)) from exc
        blocked_signals = ("verify you are human", "checking your browser", "access denied", "captcha", "security check")
        page_sample = (doc["title"] + " " + doc["text"][:5000]).lower()
        if any(signal in page_sample for signal in blocked_signals):
            raise SourceAccessError("The website returned a browser verification or access restriction page; it was not indexed.")
        return [doc], links

    def _sitemap_urls(self, sitemap: str) -> list[str]:
        try:
            final_url, content, content_type = self._get_bytes(sitemap)
            if content_type not in ("application/xml", "text/xml", "application/octet-stream", "") and not final_url.endswith(".xml"):
                return []
            root = ET.fromstring(content)
            return [node.text.strip() for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "loc" and node.text and validate_trusted_url(node.text.strip(), self.sources)][:2000]
        except (SourceAccessError, ET.ParseError):
            return []

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        words = re.findall(r"[^\W_]+", query.lower(), flags=re.UNICODE)
        return {word for word in words if len(word) > 1 and word not in STOP_WORDS}

    @staticmethod
    def _url_score(url: str, anchor_text: str, terms: set[str]) -> int:
        haystack = (url + " " + anchor_text).lower()
        return sum(2 if term in url.lower() else 1 for term in terms if term in haystack)

    def retrieve_for_query(self, query: str, max_pages: int | None = None) -> tuple[list[dict], list[str]]:
        """Fetch real source landing pages and query-relevant discovered links/sitemap URLs."""
        limit = max_pages or MAX_CRAWL_PAGES
        terms = self._query_terms(query)
        documents: list[dict] = []
        failures: list[str] = []
        candidates: dict[str, tuple[int, str]] = {}
        sitemaps_by_source: list[tuple[dict, list[str]]] = []
        fetched_by_source: dict[str, int] = {}

        for source in self.sources:
            root = source["base_url"]
            try:
                root_docs, links = self.fetch_document(root)
                documents.extend(root_docs)
                fetched_by_source[source["name"]] = len(root_docs)
                for link in links:
                    url = urldefrag(urljoin(root_docs[0]["url"], link["href"]))[0]
                    if validate_trusted_url(url, self.sources) and url not in {d["url"] for d in root_docs}:
                        score = self._url_score(url, link["text"], terms)
                        if score:
                            candidates[url] = (score, source["name"])
                _, sitemaps = self._robots_for(root)
                if not sitemaps:
                    conventional = urljoin(root, "/sitemap.xml")
                    if validate_trusted_url(conventional, self.sources):
                        sitemaps = [conventional]
                sitemaps_by_source.append((source, sitemaps[:1]))
            except (SourceAccessError, DocumentExtractionError) as exc:
                failures.append(f"{source['name']}: {exc}")
            if len(documents) >= limit:
                break

        for source, sitemaps in sitemaps_by_source:
            for sitemap in sitemaps:
                for url in self._sitemap_urls(sitemap):
                    if url in {d["url"] for d in documents}:
                        continue
                    score = self._url_score(url, "", terms)
                    if score:
                        candidates[url] = (score, source["name"])

        ranked = sorted(candidates.items(), key=lambda pair: pair[1][0], reverse=True)
        attempted = 0
        visited = {d["url"] for d in documents}
        for url, (_score, source_name) in ranked:
            if attempted >= max(0, limit - len(documents)):
                break
            if fetched_by_source.get(source_name, 0) >= MAX_PAGES_PER_SOURCE:
                continue
            if url in visited:
                continue
            attempted += 1
            visited.add(url)
            try:
                page_docs, _ = self.fetch_document(url)
                documents.extend(page_docs)
                fetched_by_source[source_name] = fetched_by_source.get(source_name, 0) + len(page_docs)
            except (SourceAccessError, DocumentExtractionError) as exc:
                failures.append(f"{source_name} ({url}): {exc}")
        return documents, failures


def retrieve_official_sources(query: str) -> tuple[list[dict], list[str]]:
    return PublicSourceFetcher().retrieve_for_query(query)

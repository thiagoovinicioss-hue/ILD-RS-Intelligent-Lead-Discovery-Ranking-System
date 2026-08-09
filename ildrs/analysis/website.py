"""Website enrichment: fetch a business site and extract lightweight signals.

The analyzer is intentionally conservative: it uses one request, a short
timeout, and treats every failure as non-fatal. Output is a structured
``WebsiteAnalysis`` object that feature extraction can read; nothing here
raises on a broken site.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

from ildrs.config import get_settings
from ildrs.normalization.normalizers import normalize_website

logger = logging.getLogger("ildrs.analysis.website")

SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
    "pinterest.com",
    "behance.net",
    "dribbble.com",
)

BLOG_PATH_RE = re.compile(r"/blog|/news|/insights|/articles|/posts|/journal|/updates")
GENERATOR_RE = re.compile(
    r"wordpress|wix|shopify|squarespace|webflow|duda|goDaddy|weebly|blogger|ghost"
)

SPA_MARKERS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__GATSBY",
    "window.__VUE_",
    "create-react-app",
    "vite",
)

DATE_RE = re.compile(r"(20\d{2})[-/_.](\d{1,2})[-/_.](\d{1,2})")


@dataclass
class WebsiteAnalysis:
    url: str
    fetched: bool = False
    error: str | None = None
    title: str = ""
    meta_description: str = ""
    lang: str = ""
    generator: str = ""
    is_spa: bool = False
    has_blog: bool = False
    post_count: int = 0
    latest_post_at: str = ""
    social_links: list[str] = field(default_factory=list)
    word_count: int = 0
    response_time_ms: int | None = None
    has_ssl: bool = False
    status_code: int | None = None

    @property
    def has_content(self) -> bool:
        return self.fetched and not self.error

    @property
    def is_generated(self) -> bool:
        return bool(self.generator)

    def social_links_compact(self) -> list[str]:
        return sorted(set(self.social_links))


def _domain_ok(href: str) -> bool:
    value = (href or "").strip().lower()
    for d in SOCIAL_DOMAINS:
        if value.startswith(f"https://{d}") or value.startswith(f"http://{d}"):
            return True
        if value.startswith(f"https://www.{d}") or value.startswith(f"http://www.{d}"):
            return True
    return False


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.description: str = ""
        self.generator: str = ""
        self.lang: str = ""
        self.links: list[str] = []
        self.time_values: list[str] = []
        self.scripts: list[str] = []
        self.text_parts: list[str] = []
        self.in_title = False
        self.in_script = False
        self.in_body = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (attrs.get("name") or "").lower()
            content = attrs.get("content", "")
            if name == "description":
                self.description = content
            elif name == "generator":
                self.generator = content
        elif tag == "html":
            self.lang = attrs.get("lang", "")
        elif tag == "a":
            href = attrs.get("href", "")
            if href:
                self.links.append(href)
        elif tag == "time":
            value = attrs.get("datetime") or ""
            if value:
                self.time_values.append(value)
        elif tag == "script":
            src = attrs.get("src", "")
            if src:
                self.scripts.append(src)
            self.in_script = True
        elif tag == "body":
            self.in_body = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if getattr(self, "in_title", False):
            self.title = (self.title + " " + data).strip()
        elif getattr(self, "in_script", False):
            self.scripts.append(data)
        elif self.in_body:
            self.text_parts.append(data)


def _has_blog_link(links: Iterable[str]) -> bool:
    return any(BLOG_PATH_RE.search(link.lower()) for link in links)


def _is_spa(parser: _PageParser) -> bool:
    blob = " ".join([*parser.scripts, parser.generator.lower()])
    return any(marker in blob for marker in SPA_MARKERS)


def _post_count_and_latest(links: Iterable[str], time_values: Iterable[str]) -> tuple[int, str]:
    post_links = [link for link in links if BLOG_PATH_RE.search(link.lower())]
    if not post_links and not time_values:
        return 0, ""
    dates: list[str] = []
    for link in post_links:
        match = DATE_RE.search(link)
        if match:
            dates.append(f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
    for value in time_values:
        match = DATE_RE.search(value)
        if match:
            dates.append(f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
    latest = max(dates) if dates else ""
    count = len(post_links) if post_links else len(time_values)
    return count, latest


def _word_count(text_parts: Iterable[str]) -> int:
    return len(re.findall(r"\S+", " ".join(text_parts)))


def parse_page(html: str, url: str) -> WebsiteAnalysis:
    """Parse downloaded HTML into structured signals."""
    parser = _PageParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed HTML must not break analysis
        logger.debug("failed to parse page %s", url)
    links = list(parser.links)
    post_count, latest = _post_count_and_latest(links, parser.time_values)
    generator = parser.generator
    if not generator:
        generated = any(GENERATOR_RE.search(link.lower()) for link in parser.scripts)
        if generated:
            generator = "detected-cms"
    return WebsiteAnalysis(
        url=normalize_website(url),
        fetched=True,
        title=parser.title,
        meta_description=parser.description,
        lang=parser.lang,
        generator=generator,
        is_spa=_is_spa(parser),
        has_blog=_has_blog_link(links) or bool(latest),
        post_count=post_count,
        latest_post_at=latest,
        social_links=[link for link in links if _domain_ok(link)],
        word_count=_word_count(parser.text_parts),
        has_ssl=str(url).lower().startswith("https://"),
    )


async def analyze_website(
    url: str,
    *,
    timeout: float | None = None,
    max_redirects: int = 3,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebsiteAnalysis:
    """Fetch and analyze a single site. Never raises for a broken site."""
    target = normalize_website(url)
    if not target:
        return WebsiteAnalysis(url=url, error="no usable website url")
    settings = get_settings()
    timeout = timeout if timeout is not None else settings.website_analysis_timeout_seconds

    started = asyncio.get_running_loop().time()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, transport=transport, follow_redirects=True, max_redirects=max_redirects
        ) as client:
            response = await client.get(
                target, headers={"User-Agent": "ILD-RS/0.1 (+data enrichment)"}
            )
        elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        analysis = parse_page(response.text, target)
        analysis.status_code = response.status_code
        analysis.response_time_ms = elapsed_ms
        if response.status_code >= 400:
            analysis.error = f"http {response.status_code}"
            analysis.fetched = False
        return analysis
    except Exception as exc:  # noqa: BLE001 - network failures are non-fatal
        logger.info("website analysis failed for %s: %s", target, exc)
        return WebsiteAnalysis(url=target, error=f"{type(exc).__name__}: {exc}")

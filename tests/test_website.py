"""Tests for website enrichment (analysis.website)."""

from __future__ import annotations

import httpx
import pytest

from ildrs.analysis.website import analyze_website, parse_page

SAMPLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta name="generator" content="WordPress 6.4" />
  <meta name="description" content="Trusted plumbing services in Austin" />
  <title>Apex Plumbing Co — Leak repair & installs</title>
</head>
<body>
  <h1>Welcome</h1>
  <p>We fix leaks, install water heaters, and handle emergency callouts.</p>
  <p>Call now for fast service across Austin, TX.</p>
  <a href="/blog">Blog</a>
  <a href="https://www.facebook.com/apexplumbing">Facebook</a>
  <a href="https://www.instagram.com/apexplumbing">Instagram</a>
  <a href="https://twitter.com/apexplumbing">Twitter</a>
  <time datetime="2025-11-02T10:00:00">Nov 2</time>
  <script src="/wp-content/theme/app.js"></script>
</body>
</html>
"""


class HtmlTransport(httpx.AsyncBaseTransport):
    def __init__(self, html: str = SAMPLE_HTML, status: int = 200) -> None:
        self.html = html
        self.status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.status, text=self.html, request=request)


def test_parse_page_extracts_signals():
    analysis = parse_page(SAMPLE_HTML, "https://apex.example.com")
    assert analysis.fetched
    assert analysis.has_content
    assert analysis.title == "Apex Plumbing Co — Leak repair & installs"
    assert analysis.generator == "WordPress 6.4"
    assert analysis.lang == "en"
    assert analysis.has_blog
    assert analysis.post_count >= 1
    assert analysis.latest_post_at == "2025-11-02"
    assert "https://www.facebook.com/apexplumbing" in analysis.social_links_compact()
    assert analysis.word_count > 10
    assert analysis.has_ssl


def test_parse_page_detects_spa_markers():
    html = '<html><script src="/app.js">window.__NEXT_DATA__ = {}</script></html>'
    analysis = parse_page(html, "http://spa.example.com")
    assert analysis.is_spa
    assert not analysis.has_ssl


@pytest.mark.asyncio
async def test_analyze_website_success():
    analysis = await analyze_website(
        "https://apex.example.com", timeout=5, transport=HtmlTransport()
    )
    assert analysis.fetched
    assert analysis.status_code == 200
    assert analysis.response_time_ms is not None
    assert analysis.social_links


@pytest.mark.asyncio
async def test_analyze_website_http_error_is_non_fatal():
    analysis = await analyze_website(
        "https://broken.example.com", timeout=5, transport=HtmlTransport(status=500)
    )
    assert not analysis.has_content
    assert analysis.error is not None


@pytest.mark.asyncio
async def test_analyze_website_bad_url_is_non_fatal():
    analysis = await analyze_website(None, timeout=5)
    assert not analysis.has_content
    assert analysis.error

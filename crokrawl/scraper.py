"""Content scraper — httpx + readability-lxml + markdownify.

No browser required. Handles ~80% of the web (static HTML).
For JS-rendered pages (SPAs), results will be incomplete — we note this in metadata.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Any
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from readability import Document

from crokrawl.url_validation import is_safe_url


logger = logging.getLogger(__name__)

# HTML pages that are likely JS-rendered (SPA detection)
SPA_INDICATORS = [
    'id="__next"',           # Next.js
    '__NEXT_DATA__',        # Next.js data
    '__NUXT__',             # Nuxt.js
    '__REDUX__',            # Redux
    '__APOLLO_STATE__',     # Apollo/GraphQL
    'data-reactroot',       # React
    'angular-version',      # Angular
    'vue-app',              # Vue.js
]


@dataclass
class ScrapeResult:
    """Result from scraping a single URL."""
    success: bool = True
    url: str = ""
    markdown: str = ""
    html: str = ""
    title: str = ""
    description: str = ""
    source_url: str = ""
    status_code: int = 0
    error: str = ""
    metadata: dict = field(default_factory=dict)
    is_js_rendered: bool = False  # True if page likely needs JS rendering


class Scraper:
    """Content scraper using httpx + readability-lxml + markdownify.

    Works without a browser. For JS-rendered pages, results may be incomplete.
    """

    def __init__(self, config):
        self.config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "crokrawl/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",  # Don't compress — readability needs raw HTML
            },
        )

    async def start(self):
        """Initialize scraper (no browser needed)."""
        pass

    async def stop(self):
        """Close HTTP client."""
        await self._client.aclose()

    async def scrape(
        self,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        render_js: bool | None = None,
        wait_for: int | None = None,
        **kwargs: Any,
    ) -> ScrapeResult:
        """Scrape a single URL.

        Args:
            url: URL to scrape
            formats: Output formats (markdown, html, links, plainText, json)
            only_main_content: Extract only main content via Readability
            render_js: Ignored (no browser). Pages needing JS will be noted.
            wait_for: Ignored (no browser).
        """
        result = ScrapeResult(url=url)

        # SSRF protection — block private/internal URLs
        if not is_safe_url(url):
            result.success = False
            result.error = f"Access denied: URL targets a private/internal address"
            logger.warning("Blocked scrape request to: %s", url)
            return result

        try:
            # Fetch page
            response = await self._client.get(url)

            # Redirect SSRF check — validate the final URL after redirects
            final_url = str(response.url)
            if final_url != url and not is_safe_url(final_url):
                result.success = False
                result.error = "Redirect blocked (SSRF prevention)"
                logger.warning("Blocked redirect to internal address: %s -> %s", url, final_url)
                return result

            result.status_code = response.status_code
            html = response.text
            result.html = html
            result.source_url = url

            # Detect JS-rendered pages
            if self._is_js_rendered(html, response):
                result.is_js_rendered = True
                logger.info("Page likely JS-rendered (SPA): %s", url)

            # Parse HTML
            soup = BeautifulSoup(html, "lxml")

            # Extract title
            result.title = self._extract_title(soup)

            # Extract description
            result.description = self._extract_description(soup)

            if only_main_content:
                # Use Readability for main content extraction
                doc = Document(html, min_text_length=50)
                article_html = doc.summary()
                article_title = doc.title()

                if article_title:
                    result.title = article_title

                # Convert to markdown
                result.markdown = _html_to_markdown(article_html)

                # Fallback: if Readability extracted nothing, try body directly
                if not result.markdown.strip() and len(html) > 2000:
                    body = soup.find("body")
                    if body:
                        result.markdown = _html_to_markdown(str(body))
                        result.metadata["extraction_method"] = "fallback-body"
                    else:
                        result.markdown = _html_to_markdown(html)
                        result.metadata["extraction_method"] = "fallback-full"
                else:
                    result.metadata["extraction_method"] = "readability"

                # Post-extraction check: if Readability got very little from a large page,
                # it's likely JS-rendered (UI text is there but article content is empty)
                if (result.metadata.get("extraction_method") == "readability"
                    and len(html) > 50000
                    and len(result.markdown) < 1000):
                    result.is_js_rendered = True
                    if not result.metadata.get("warning"):
                        result.metadata["warning"] = (
                            "This page appears to be JS-rendered (SPA). "
                            "Content may be incomplete. Use a browser-based scraper for full rendering."
                        )
            else:
                # Use full page
                body = soup.find("body")
                if body:
                    result.markdown = _html_to_markdown(str(body))
                else:
                    result.markdown = _html_to_markdown(html)

            # Extract links
            if formats and "links" in formats:
                result.metadata["links"] = self._extract_links(soup, url)

            # Extract structured data
            if formats and "json" in formats:
                result.metadata["structured_data"] = self._extract_structured_data(soup)

            # Note JS-rendered pages
            if result.is_js_rendered:
                result.metadata["warning"] = (
                    "This page appears to be JS-rendered (SPA). "
                    "Content may be incomplete. Use a browser-based scraper for full rendering."
                )

        except httpx.HTTPError as e:
            result.success = False
            result.error = "Failed to fetch page"
            logger.error("Scrape HTTP error for %s: %s", url, e)
        except Exception as e:
            result.success = False
            result.error = "Scrape failed"
            logger.error("Scrape error for %s: %s", url, e)

        return result

    async def map_urls(self, url: str, max_depth: int = 2) -> list[str]:
        """Discover URLs on a domain without scraping content.

        Fetches pages, extracts links, follows same-origin URLs.
        """
        result_urls: set[str] = set()
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(url, 0)]

        domain = urlparse(url).netloc

        # SSRF protection — validate initial URL
        if not is_safe_url(url):
            logger.warning("Blocked map request (SSRF): %s", url)
            return []

        while queue and len(visited) < 1000:
            current_url, depth = queue.pop(0)
            if current_url in visited or depth > max_depth:
                continue
            visited.add(current_url)

            try:
                response = await self._client.get(current_url, timeout=10)
                if response.status_code == 200:
                    result_urls.add(current_url)

                    # Extract same-domain links
                    soup = BeautifulSoup(response.text, "lxml")
                    for a_tag in soup.find_all("a", href=True):
                        href = a_tag["href"]
                        # Skip anchors, mailto, tel, javascript
                        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                            continue
                        full_url = urljoin(current_url, href)
                        try:
                            parsed = urlparse(full_url)
                            if parsed.netloc == domain and full_url not in visited:
                                queue.append((full_url, depth + 1))
                        except Exception:
                            continue

            except Exception:
                pass

        return sorted(result_urls)

    def _is_js_rendered(self, html: str, response) -> bool:
        """Detect if page is likely JS-rendered (SPA)."""
        lower = html.lower()

        # Check for known SPA indicators in HTML
        if any(indicator in lower for indicator in SPA_INDICATORS):
            return True

        # Check for webpack/runtime bootstrapping patterns in scripts
        webpack_patterns = [
            '__webpack_require__',
            'runtime:',
            'module.exports=',
            '__react_refresh_',
            'hotUpdate',
            'registerModule',
        ]
        if any(p in lower for p in webpack_patterns):
            # Only flag if combined with other signs (many sites use webpack but render server-side)
            soup = BeautifulSoup(html, "lxml")
            # Check if main content area is empty
            main = soup.find(['main', 'article', '#content', '.content', '.main'])
            if main:
                main_text = main.get_text(strip=True)
                if len(main_text) < 100:
                    return True

        # Check for empty body (server returns empty shell, JS fills it)
        soup = BeautifulSoup(html, "lxml")
        body = soup.find("body")
        if body:
            body_text = body.get_text(strip=True)
            # If body has very little text but page is large, it's likely JS-rendered
            if len(html) > 5000 and len(body_text) < 200:
                return True

            # Check for common empty-body patterns
            if body_text.strip() in ("", "Loading...", "Please wait"):
                return True

        # Heuristic: many scripts + very little visible text = JS rendering
        scripts = soup.find_all("script")
        if len(scripts) > 30 and len(body_text) < 500 and len(html) > 100000:
            return True

        # Heuristic: if HTML is large but visible text (excluding scripts) is tiny, likely JS-rendered
        # Remove script content from body text for accurate count
        for script in scripts:
            script.decompose()
        visible_text = body.get_text(strip=True) if body else ""
        if len(html) > 80000 and len(visible_text) < 1500:
            return True

        return False

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title."""
        # Try <title>
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            return title_tag.string.strip()

        # Try og:title
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()

        return ""

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract page description."""
        # Try meta description
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            return meta["content"].strip()

        # Try og:description
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            return og["content"].strip()

        return ""

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract links from page."""
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = (a.get_text(strip=True) or "")[:100]
            # Normalize
            try:
                full = urljoin(base_url, href)
                # Skip non-HTTP
                if not full.startswith(("http://", "https://")):
                    continue
                # Skip anchors
                if "#" in full and full.index("#") < full.index("://") + 5:
                    continue
                if full in seen:
                    continue
                seen.add(full)
                links.append({"text": text, "href": full})
            except Exception:
                continue
        return links[:200]

    def _extract_structured_data(self, soup: BeautifulSoup) -> Optional[dict]:
        """Extract JSON-LD structured data."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                return json.loads(script.string or "{}")
            except (json.JSONDecodeError, Exception):
                continue
        return None


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown."""
    if not html:
        return ""

    md = markdownify(
        html,
        heading_style="ATX",
        code_language="default",
        strip=["img", "script", "style"],
        bullets="-",
        max_title_length=0,
    )

    # Clean up excessive whitespace
    lines = [line.rstrip() for line in md.split("\n")]
    cleaned = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned).strip()

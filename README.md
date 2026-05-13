# crokcrawl — Open-Source Firecrawl Replacement

A self-hosted, Firecrawl-compatible API with optional Playwright JS rendering. Drop it in, set `FIRECRAWL_API_URL=http://localhost:8000`, and it works.

**Latest: v0.2.1** — Bug fixes and robustness improvements. See [Release Notes](#release-notes) below.

## Features

- `/v1/scrape` — scrape a URL into clean Markdown (static + JS-rendered via Playwright)
- `/v1/search` — web search via SearXNG, with DuckDuckGo fallback and optional result scraping
- `/v1/crawl` — async multi-page website crawler
- `/v1/map` — discover URLs on a domain
- Drop-in Firecrawl API compatibility (same request/response shapes)
- SSRF protection (blocks private IPs, cloud metadata endpoints)
- Built-in auth (Bearer token / x-api-key) and rate limiting
- CLI with `crokcrawl` command

## Quick Start

**Standard (static pages, ~80% coverage):**
```bash
uv sync
uv run crokcrawl --port 8000
```

**With Playwright (full JS rendering):**
```bash
uv sync --all-extras
uv run playwright install chromium
uv run crokcrawl --port 8000
```

## CLI

```bash
uv run crokcrawl --help
uv run crokcrawl --port 9000
uv run crokcrawl --install-playwright   # Installs Chromium and exits
```

## Firecrawl Compatibility

Same API, same response shapes:

| Firecrawl | crokrawl |
|-----------|----------|
| `POST /v1/scrape` | `POST /v1/scrape` ✅ |
| `POST /v1/search` | `POST /v1/search` ✅ |
| `POST /v1/crawl` | `POST /v1/crawl` ✅ |
| `POST /v1/map` | `POST /v1/map` ✅ |

Response format matches:
```json
{"success": true, "data": {"markdown": "...", "metadata": {...}}}
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CROKRAWL_PORT` | `8000` | Server port |
| `CROKRAWL_SEARXNG_URL` | `http://localhost:8080` | SearXNG instance URL |
| `CROKRAWL_SEARXNG_API_KEY` | *(none)* | If your SearXNG requires auth |
| `CROKRAWL_MAX_CONCURRENCY` | `4` | Max concurrent scrapes |
| `CROKRAWL_STEALTH` | `true` | Use stealth mode (anti-bot) |
| `CROKRAWL_TIMEOUT` | `30` | Per-request timeout in seconds |
| `CROKRAWL_WAIT_FOR` | `500` | Extra ms to wait after page load |

## SearXNG Setup (optional but recommended)

```bash
docker run -d --name searxng -p 8080:8080 \
  -e SEARXNG_BASE_URL=http://localhost:8080/ \
  searxng/searxng:latest
```

Or use a public instance:
```bash
export CROKRAWL_SEARXNG_URL=https://search.sapti.me
```

## For Hermes Integration

Add to `~/.hermes/hermes-agent/tools/web_tools.py` as a new backend `"crokrawl"`:

```python
# In _get_backend() fallback:
("crokrawl", _has_env("CROKRAWL_URL")),

# In _is_backend_available():
if backend == "crokrawl":
    return True  # Always available once installed

# Use httpx to call http://localhost:8000/v1/scrape, etc.
```

Or set `FIRECRAWL_API_URL=http://localhost:8000` — the existing Firecrawl client code will work with crokrawl's compatible API.

## Release Notes

### v0.2.1 — 2026-05-13
**Bug fixes and robustness improvements**

- **Config deferred DNS validation:** `_validate_url()` now catches DNS resolution failures at startup and logs a warning instead of crashing the app. URL is validated at runtime before use. This prevents crashes in containerized setups where SearXNG isn't available yet.
- **SPA detection visibility:** When Playwright is unavailable but an SPA is detected, the response now includes `metadata.js_render_skipped: true` and `metadata.js_render_reason`, so clients know JS rendering was skipped. The `ScrapeResult.is_js_rendered` flag is always set when SPA indicators are detected.
- **CLI background process fix:** Changed from string-based `uvicorn.run("crokcrawl.server:app")` to explicit import `from crokcrawl.server import app`. This resolves `ModuleNotFoundError` issues in background/detached process contexts where Python `.pth` file editable install mechanisms may not activate.
- **Httpx timeout uses config:** The scraper now reads `CROKRAWL_TIMEOUT` for the read timeout instead of hardcoding 30s.
- **Version auto-sync:** API version now reads dynamically from `__init__.py` instead of being hardcoded, keeping it in sync with `pyproject.toml`.

### v0.2.0 — 2026-05-13
- Added CLI (`crokcrawl` command) with `--host`, `--port`, `--reload`, `--install-playwright`
- Added Playwright JS rendering for SPA detection and rendering
- Fixed package name display corruption in terminals (hex-level verified)
- Removed insecure fields (`proxy`, `llm_api_key`) from API models

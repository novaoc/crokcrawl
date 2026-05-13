"""CLI entry point for crokrawl."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="crokrawl",
        description="Open-source Firecrawl-compatible scraping API",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port (overrides CROKRAWL_PORT env var)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development mode)",
    )
    parser.add_argument(
        "--install-playwright",
        action="store_true",
        help="Install Playwright Chromium browser and exit",
    )

    args = parser.parse_args()

    if args.install_playwright:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("Playwright not installed. Run: uv run pip install playwright")
            sys.exit(1)
        print("Installing Chromium browser...")
        with sync_playwright() as p:
            p.chromium.launch()
        print("Done.")
        sys.exit(0)

    port = args.port
    if port is None:
        from crokrawl.config import config
        port = config.port

    import uvicorn
    uvicorn.run(
        "crokcrawl.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

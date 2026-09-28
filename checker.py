#!/usr/bin/env python3
"""Crawl configured sites and email a report of broken outbound links."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import smtplib
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "BrokenLinkMonitor/1.0"
SKIP_SCHEMES = {"mailto", "tel", "javascript", "data"}
REPORT_PATH = "report.html"
REPORT_JSON_PATH = "report.json"
MAX_SOURCES_SHOWN = 10
REVIEW_APP_URL = "https://ha-link-scanner.vercel.app/"
BOT_SAMPLE_BYTES = 16384
RENDER_TIMEOUT_FLOOR_SECONDS = 30
RENDER_WAIT_SECONDS = 12
LINK_SETTLE_SECONDS = 2.0


@dataclass
class Source:
    page_url: str
    anchor: str


@dataclass
class LinkResult:
    url: str
    status: int | None
    error: str | None
    sources: list[Source] = field(default_factory=list)


@dataclass
class SiteResult:
    start_url: str
    pages_crawled: int
    outbound_checked: int
    broken: list[LinkResult]
    unchecked: list[LinkResult]
    error: str | None = None


@dataclass
class FetchedPage:
    url: str
    status_code: int
    text: str
    content_type: str


def normalize_url(url: str) -> str:
    """Return an absolute http(s) URL without a fragment, or an empty string."""
    without_fragment, _fragment = urldefrag(url.strip())
    parts = urlparse(without_fragment)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if parts.port and not (
        (parts.scheme.lower() == "http" and parts.port == 80)
        or (parts.scheme.lower() == "https" and parts.port == 443)
    ):
        netloc = f"{host}:{parts.port}"
    else:
        netloc = host
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{netloc}{path}{query}"


def hostname_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def same_host(url: str, host: str) -> bool:
    return hostname_of(url) == host.lower()


def classify_status(status: int) -> str:
    if status == 429:
        return "unchecked"
    if status >= 400:
        return "broken"
    return "ok"


def is_bot_protection(headers: httpx.Headers, sample: str) -> bool:
    """True when an error page is a bot challenge rather than a dead link.

    A browser can complete these challenges and show the real page. This client
    cannot, so the 4xx status is not evidence that the URL is broken.
    """
    if headers.get("cf-mitigated", "").strip():
        return True
    text = sample.lower()
    server = headers.get("server", "").lower()
    if "just a moment" in text or "challenge-platform" in text:
        return True
    if "datadome" in server or "datadome" in text or "captcha-delivery.com" in text:
        return True
    if "akamai" in server and "access denied" in text:
        return True
    return False


def short_error(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if not detail:
        return exc.__class__.__name__
    if len(detail) > 200:
        detail = detail[:197] + "..."
    return f"{exc.__class__.__name__}: {detail}"


def is_html_page(page: FetchedPage) -> bool:
    if not page.content_type:
        return True
    lowered = page.content_type.lower()
    return "html" in lowered or lowered.startswith("text/")


def extract_links(page_url: str, body: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(body, "html.parser")
    base_tag = soup.find("base", href=True)
    base_url = urljoin(page_url, base_tag["href"].strip()) if base_tag else page_url
    found: list[tuple[str, str]] = []
    for tag in soup.find_all("a", href=True):
        raw = tag["href"].strip()
        if not raw or raw.startswith("#"):
            continue
        scheme = urlparse(raw).scheme.lower()
        if scheme in SKIP_SCHEMES:
            continue
        normalized = normalize_url(urljoin(base_url, raw))
        if not normalized:
            continue
        anchor = " ".join(tag.get_text(" ", strip=True).split())[:200]
        found.append((normalized, anchor))
    return found


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        sys.exit(f"Config file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"Config file is not valid JSON: {exc}")

    if not isinstance(data, dict):
        sys.exit("Config must be a JSON object")

    sites = data.get("sites")
    if not isinstance(sites, list) or not sites:
        sys.exit("Config sites must be a non-empty list")

    cleaned_sites = []
    for index, site in enumerate(sites, start=1):
        if not isinstance(site, dict) or not isinstance(site.get("url"), str):
            sys.exit(f"Site {index} needs a url string")
        url = normalize_url(site["url"])
        if not url:
            sys.exit(f"Site {index} url must be an absolute http(s) URL")
        max_pages = site.get("maxPages", 50)
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            sys.exit(f"Site {index} maxPages must be a positive integer")
        javascript = site.get("javascript", False)
        if not isinstance(javascript, bool):
            sys.exit(f"Site {index} javascript must be true or false")
        cleaned_sites.append({"url": url, "maxPages": max_pages, "javascript": javascript})

    concurrency = data.get("concurrency", 8)
    timeout_seconds = data.get("timeoutSeconds", 15)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        sys.exit("concurrency must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        sys.exit("timeoutSeconds must be a positive integer")

    return {
        "sites": cleaned_sites,
        "concurrency": concurrency,
        "timeoutSeconds": timeout_seconds,
    }


def load_smtp() -> dict:
    required = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "EMAIL_FROM",
        "EMAIL_TO",
    ]
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))

    try:
        port = int(os.environ["SMTP_PORT"].strip())
    except ValueError:
        sys.exit("SMTP_PORT must be an integer")

    recipients = [part.strip() for part in os.environ["EMAIL_TO"].split(",") if part.strip()]
    if not recipients:
        sys.exit("EMAIL_TO must contain at least one address")

    return {
        "host": os.environ["SMTP_HOST"].strip(),
        "port": port,
        "user": os.environ["SMTP_USER"].strip(),
        "password": os.environ["SMTP_PASSWORD"],
        "sender": os.environ["EMAIL_FROM"].strip(),
        "recipients": recipients,
    }


def remember_source(bucket: dict[str, list[Source]], url: str, page_url: str, anchor: str) -> None:
    sources = bucket[url]
    if any(source.page_url == page_url for source in sources):
        return
    sources.append(Source(page_url, anchor))


async def fetch_page(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
) -> tuple[FetchedPage | None, str | None]:
    async with semaphore:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            return None, short_error(exc)
    return (
        FetchedPage(
            url=str(response.url),
            status_code=response.status_code,
            text=response.text,
            content_type=response.headers.get("content-type", ""),
        ),
        None,
    )


async def collect_rendered_links(page, timeout_seconds: float) -> dict[str, str]:
    """Gather anchors while the page finishes rendering.

    A nav shell, a splash screen, or a carousel can change the anchors over
    several seconds. The first sample is only a baseline. Later new anchors
    reset a short quiet period. The wait also ends at timeout_seconds so a
    page that never adds links cannot stall the crawl.
    """
    deadline = time.monotonic() + timeout_seconds
    seen: dict[str, str] = {}
    baseline_done = False
    grew = False
    last_growth = time.monotonic()
    while True:
        pairs = await page.evaluate(
            """() => [...document.querySelectorAll('a[href]')].map(a => [
                a.href,
                (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200)
            ])"""
        )
        now = time.monotonic()
        fresh = False
        for href, text in pairs:
            if href not in seen:
                seen[href] = text
                fresh = True
        if not baseline_done:
            baseline_done = True
            last_growth = now
        elif fresh:
            grew = True
            last_growth = now
        elif grew and now - last_growth >= LINK_SETTLE_SECONDS:
            break
        if now >= deadline:
            break
        await asyncio.sleep(0.5)
    return seen


def sitemap_locations(body: str) -> list[str]:
    return [html.unescape(match.strip()) for match in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body, flags=re.I)]


async def sitemap_page_urls(client: httpx.AsyncClient, start_url: str, host: str) -> list[str]:
    """Return same-host page URLs listed in the site's sitemap, if it has one."""
    seeds: list[str] = []
    try:
        robots = await client.get(urljoin(start_url, "/robots.txt"))
    except httpx.HTTPError:
        robots = None
    if robots is not None and robots.status_code < 400:
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                seeds.append(line.split(":", 1)[1].strip())
    if not seeds:
        seeds.append(urljoin(start_url, "/sitemap.xml"))

    pages: list[str] = []
    seen_maps: set[str] = set()
    pending = list(seeds)
    while pending and len(seen_maps) < 10:
        map_url = pending.pop(0)
        if map_url in seen_maps:
            continue
        seen_maps.add(map_url)
        try:
            response = await client.get(map_url)
        except httpx.HTTPError:
            continue
        if response.status_code >= 400 or "<loc>" not in response.text.lower():
            continue
        locations = sitemap_locations(response.text)
        if "<sitemapindex" in response.text.lower():
            pending.extend(locations)
            continue
        for raw in locations:
            url = normalize_url(raw)
            if url and same_host(url, host) and url not in pages:
                pages.append(url)
    return pages


def html_with_collected_links(page_html: str, links: dict[str, str]) -> str:
    if not links:
        return page_html
    items = "".join(
        f'<a href="{html.escape(href, quote=True)}">{html.escape(text)}</a>'
        for href, text in links.items()
    )
    snippet = f"<nav hidden>{items}</nav>"
    if "</body>" in page_html:
        return page_html.replace("</body>", snippet + "</body>", 1)
    return page_html + snippet


class RenderedBrowser:
    """Headless Chromium for sites whose links are not in the first HTML response."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self.context = None

    async def __aenter__(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            sys.exit(
                "javascript sites need Playwright. "
                "Run: pip install -r requirements.txt && playwright install chromium"
            )
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage"],
            )
        except Exception as exc:
            await self._playwright.stop()
            sys.exit(
                "Could not launch Chromium for javascript sites. "
                "Run: playwright install chromium\n" + short_error(exc)
            )
        self.context = await self._browser.new_context(user_agent=USER_AGENT)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.context is not None:
            await self.context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


class NoBrowser:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


async def fetch_rendered(
    context,
    url: str,
    semaphore: asyncio.Semaphore,
    timeout_seconds: int,
) -> tuple[FetchedPage | None, str | None]:
    timeout_ms = max(timeout_seconds, RENDER_TIMEOUT_FLOOR_SECONDS) * 1000
    async with semaphore:
        page = await context.new_page()
        response = None
        try:
            try:
                response = await page.goto(url, wait_until="load", timeout=timeout_ms)
            except Exception as exc:
                if page.url in {"", "about:blank"}:
                    return None, short_error(exc)
            try:
                collected = await collect_rendered_links(page, RENDER_WAIT_SECONDS)
            except Exception:
                collected = {}
            status = response.status if response is not None else 200
            content_type = "text/html"
            if response is not None:
                content_type = response.headers.get("content-type", "") or content_type
            page_html = html_with_collected_links(await page.content(), collected)
            return (
                FetchedPage(
                    url=page.url,
                    status_code=status,
                    text=page_html,
                    content_type=content_type,
                ),
                None,
            )
        except Exception as exc:
            return None, short_error(exc)
        finally:
            await page.close()


async def read_sample(response: httpx.Response, limit: int = BOT_SAMPLE_BYTES) -> str:
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) >= limit:
            break
    return bytes(data[:limit]).decode("utf-8", "replace")


async def check_url(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
) -> tuple[int | None, str | None, str]:
    async with semaphore:
        try:
            async with client.stream("GET", url) as response:
                status = response.status_code
                kind = classify_status(status)
                if kind != "broken":
                    return status, None, kind
                sample = await read_sample(response)
                if is_bot_protection(response.headers, sample):
                    return status, "bot protection", "unchecked"
                return status, None, kind
        except httpx.HTTPError as exc:
            return None, short_error(exc), "broken"


def harvest(
    response: FetchedPage,
    host: str,
    seen_pages: set[str],
    queue: list[str],
    max_pages: int,
    outbound: dict[str, list[Source]],
) -> bool:
    """Record links from an HTML page. Return True when the page was parsed."""
    final_url = normalize_url(response.url)
    if not final_url or not same_host(final_url, host):
        return False
    if response.status_code >= 400 or not is_html_page(response):
        return False

    seen_pages.add(final_url)
    for link, anchor in extract_links(final_url, response.text):
        if same_host(link, host):
            if link not in seen_pages and link not in queue and len(seen_pages) + len(queue) < max_pages:
                queue.append(link)
        else:
            remember_source(outbound, link, final_url, anchor)
    return True


async def crawl_site(
    client: httpx.AsyncClient,
    start_url: str,
    max_pages: int,
    semaphore: asyncio.Semaphore,
    renderer: RenderedBrowser | None = None,
    timeout_seconds: int = 15,
) -> SiteResult:
    if renderer is None:
        response, error = await fetch_page(client, start_url, semaphore)
    else:
        response, error = await fetch_rendered(
            renderer.context, start_url, semaphore, timeout_seconds
        )
    if response is None:
        return SiteResult(start_url, 0, 0, [], [], error=error or "Could not fetch the start URL")
    if response.status_code >= 400:
        return SiteResult(
            start_url,
            0,
            0,
            [],
            [],
            error=f"Start URL returned HTTP {response.status_code}",
        )

    final_url = normalize_url(str(response.url)) or start_url
    host = hostname_of(final_url)
    if not host:
        return SiteResult(start_url, 0, 0, [], [], error="Start URL has no hostname after redirects")

    seen_pages: set[str] = {final_url}
    queue: list[str] = []
    outbound: dict[str, list[Source]] = defaultdict(list)
    pages_crawled = 1 if harvest(response, host, seen_pages, queue, max_pages, outbound) else 0
    if pages_crawled == 0:
        return SiteResult(start_url, 0, 0, [], [], error="Start URL did not return an HTML page")

    if renderer is not None:
        for url in await sitemap_page_urls(client, final_url, host):
            if url not in seen_pages and url not in queue and len(seen_pages) + len(queue) < max_pages:
                queue.append(url)

    while queue and len(seen_pages) < max_pages:
        batch: list[str] = []
        while queue and len(seen_pages) < max_pages:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            batch.append(page_url)
        if not batch:
            break

        if renderer is None:
            fetched = await asyncio.gather(
                *(fetch_page(client, page_url, semaphore) for page_url in batch)
            )
        else:
            fetched = await asyncio.gather(
                *(
                    fetch_rendered(renderer.context, page_url, semaphore, timeout_seconds)
                    for page_url in batch
                )
            )
        for page_url, (page_response, _page_error) in zip(batch, fetched):
            if page_response is None:
                continue
            if harvest(page_response, host, seen_pages, queue, max_pages, outbound):
                pages_crawled += 1

    urls = list(outbound)
    checked = await asyncio.gather(*(check_url(client, url, semaphore) for url in urls))
    broken: list[LinkResult] = []
    unchecked: list[LinkResult] = []
    for url, (status, link_error, kind) in zip(urls, checked):
        result = LinkResult(url, status, link_error, outbound[url])
        if kind == "broken":
            broken.append(result)
        elif kind == "unchecked":
            unchecked.append(result)

    broken.sort(key=lambda item: item.url)
    unchecked.sort(key=lambda item: item.url)
    return SiteResult(start_url, pages_crawled, len(urls), broken, unchecked)


def status_label(link: LinkResult) -> str:
    if link.status is not None and link.error:
        return f"HTTP {link.status} ({link.error})"
    if link.status is not None:
        return f"HTTP {link.status}"
    return link.error or "unreachable"


def format_sources(sources: list[Source]) -> list[str]:
    lines = []
    for source in sources[:MAX_SOURCES_SHOWN]:
        if source.anchor:
            lines.append(f"{source.page_url} ({source.anchor})")
        else:
            lines.append(source.page_url)
    remaining = len(sources) - MAX_SOURCES_SHOWN
    if remaining > 0:
        lines.append(f"+ {remaining} more pages")
    return lines


def broken_count(sites: list[SiteResult]) -> int:
    return sum(len(site.broken) for site in sites)


def review_error_phrase(count: int) -> str:
    if count == 1:
        return "1 review error"
    return f"{count} review errors"


def inaccessible_label(count: int) -> str:
    return f"Review: Inaccessible ({count})"


def subject_for(sites: list[SiteResult]) -> str:
    errors = broken_count(sites)
    failed = sum(1 for site in sites if site.error)
    if errors and failed:
        site_word = "site error" if failed == 1 else "site errors"
        return f"Outbound link report: {review_error_phrase(errors)}, {failed} {site_word}"
    if errors:
        return f"Outbound link report: {review_error_phrase(errors)}"
    if failed == 1:
        return "Outbound link report: 1 site could not be crawled"
    if failed:
        return f"Outbound link report: {failed} sites could not be crawled"
    return "Outbound link report: no review errors"


def render_text(sites: list[SiteResult], generated_at: str) -> str:
    lines = ["Outbound link report", f"Generated {generated_at}", ""]
    for site in sites:
        lines.append(site.start_url)
        if site.error:
            lines.append(f"  Error: {site.error}")
            lines.append("")
            continue
        lines.append(f"  Pages crawled: {site.pages_crawled}")
        lines.append(f"  Outbound links checked: {site.outbound_checked}")
        lines.append(f"  Review: Error: {len(site.broken)}")
        if site.broken:
            for link in site.broken:
                lines.append(f"  - {link.url}")
                lines.append(f"    {status_label(link)}")
                lines.append("    Found on:")
                for source in format_sources(link.sources):
                    lines.append(f"      {source}")
        else:
            lines.append("  No review errors.")
        if site.unchecked:
            lines.append(f"  {inaccessible_label(len(site.unchecked))}: {REVIEW_APP_URL}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(sites: list[SiteResult], generated_at: str) -> str:
    sections = []
    review_url = html.escape(REVIEW_APP_URL, quote=True)
    for site in sites:
        title = html.escape(site.start_url)
        if site.error:
            sections.append(
                "<section>"
                f"<h2>{title}</h2>"
                f"<p><strong>Error:</strong> {html.escape(site.error)}</p>"
                "</section>"
            )
            continue

        if site.broken:
            rows = []
            for link in site.broken:
                source_html = "<br>".join(html.escape(source) for source in format_sources(link.sources))
                rows.append(
                    "<tr>"
                    f"<td><a href=\"{html.escape(link.url, quote=True)}\">{html.escape(link.url)}</a></td>"
                    f"<td>{html.escape(status_label(link))}</td>"
                    f"<td>{source_html}</td>"
                    "</tr>"
                )
            error_block = (
                "<table>"
                "<tr><th>URL</th><th>Status</th><th>Found on</th></tr>"
                + "".join(rows)
                + "</table>"
            )
        else:
            error_block = "<p>No review errors.</p>"

        inaccessible = ""
        if site.unchecked:
            inaccessible = (
                f'<p><a href="{review_url}">{html.escape(inaccessible_label(len(site.unchecked)))}</a></p>'
            )

        sections.append(
            "<section>"
            f"<h2>{title}</h2>"
            "<p>"
            f"Pages crawled: {site.pages_crawled}<br>"
            f"Outbound links checked: {site.outbound_checked}<br>"
            f"Review: Error: {len(site.broken)}"
            "</p>"
            + error_block
            + inaccessible
            + "</section>"
        )

    style = (
        "body{font-family:Georgia,serif;color:#1a1a1a;margin:2rem;line-height:1.4}"
        "h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border-bottom:1px solid #ddd;text-align:left;padding:0.45rem 0.6rem;vertical-align:top}"
        "th{background:#f4f4f4}a{color:#0b57d0}"
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(subject_for(sites))}</title><style>{style}</style></head><body>"
        f"<h1>Outbound link report</h1><p>Generated {html.escape(generated_at)}</p>"
        + "".join(sections)
        + "</body></html>\n"
    )


def render_json(sites: list[SiteResult], generated_at: str) -> dict:
    payload_sites = []
    for site in sites:
        links = []
        for kind, group in (("broken", site.broken), ("unchecked", site.unchecked)):
            for link in group:
                links.append(
                    {
                        "url": link.url,
                        "kind": kind,
                        "status": status_label(link),
                        "sources": format_sources(link.sources),
                    }
                )
        payload_sites.append(
            {
                "url": site.start_url,
                "pagesCrawled": site.pages_crawled,
                "outboundChecked": site.outbound_checked,
                "error": site.error,
                "links": links,
            }
        )
    return {"generatedAt": generated_at, "sites": payload_sites}


def send_email(text: str, html_body: str, smtp: dict, subject: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp["sender"]
    message["To"] = ", ".join(smtp["recipients"])
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")

    if smtp["port"] == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=30)
    else:
        server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=30)
    try:
        server.ehlo()
        if smtp["port"] != 465:
            server.starttls()
            server.ehlo()
        server.login(smtp["user"], smtp["password"])
        server.send_message(message)
    finally:
        try:
            server.quit()
        except smtplib.SMTPServerDisconnected:
            pass


async def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    smtp = None if args.dry_run else load_smtp()
    timeout = httpx.Timeout(config["timeoutSeconds"])
    limits = httpx.Limits(
        max_connections=config["concurrency"],
        max_keepalive_connections=config["concurrency"],
    )
    semaphore = asyncio.Semaphore(config["concurrency"])
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    needs_browser = any(site["javascript"] for site in config["sites"])
    browser_manager = RenderedBrowser() if needs_browser else NoBrowser()
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        async with browser_manager as renderer:
            results: list[SiteResult] = []
            for site in config["sites"]:
                rendered = renderer if site["javascript"] else None
                label = "rendering" if rendered else "crawling"
                print(f"{label} {site['url']}", file=sys.stderr)
                results.append(
                    await crawl_site(
                        client,
                        site["url"],
                        site["maxPages"],
                        semaphore,
                        rendered,
                        config["timeoutSeconds"],
                    )
                )

    text = render_text(results, generated_at)
    document = render_html(results, generated_at)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(document)
    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as handle:
        json.dump(render_json(results, generated_at), handle, indent=2)
        handle.write("\n")
    print(text, end="")

    if args.dry_run:
        print(f"Dry run: email not sent. Wrote {REPORT_PATH} and {REPORT_JSON_PATH}", file=sys.stderr)
    else:
        assert smtp is not None
        send_email(text, document, smtp, subject_for(results))
        print(f"Email sent. Wrote {REPORT_PATH} and {REPORT_JSON_PATH}", file=sys.stderr)

    if any(site.error for site in results):
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check sites for broken outbound links and email a report."
    )
    parser.add_argument("--config", required=True, help="Path to config.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the report and skip email",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

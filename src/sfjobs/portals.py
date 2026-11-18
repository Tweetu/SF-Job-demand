"""Collect search-result counts from job portals with headless Chromium (Playwright).

Output rows: portal, portal_name, country, query, count, fresh_count, status
plus a list of (company, domain) found in schema.org JobPosting data for discovery.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from urllib.parse import quote_plus, urlparse

import yaml

from .common import CONFIG_DIR, USER_AGENT, get_logger, is_public_url, robots_allowed

log = get_logger("portals")

BLOCK_MARKERS = re.compile(
    r"just a moment|access denied|attention required|verify you are human|captcha|are you a robot|"
    r"unusual traffic|request blocked|forbidden", re.I)
ZERO_MARKERS = re.compile(r"\b(no (jobs|results|matching jobs)|0 (jobs|results)|keine (jobs|treffer))\b", re.I)


def load_config(path: str | None = None) -> dict:
    with open(path or os.path.join(CONFIG_DIR, "portals.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_url(template: str, query: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return template.replace("{q}", quote_plus(query)).replace("{slug}", slug)


def add_param(url: str, param: str) -> str:
    if not param:
        return url
    p = param.lstrip("?&")
    return url + ("&" if "?" in url else "?") + p


def parse_count(text: str, regexes: list[str], generic: list[str]) -> int | None:
    for rx in list(regexes or []) + list(generic or []):
        m = re.search(rx, text, re.I)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                n = int(digits)
                if 0 <= n < 10_000_000:
                    return n
    if ZERO_MARKERS.search(text):
        return 0
    return None


def extract_orgs(html: str) -> list[tuple[str, str]]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                org = d.get("hiringOrganization")
                if isinstance(org, dict) and org.get("name"):
                    url = org.get("sameAs") or org.get("url") or ""
                    if isinstance(url, list):
                        url = url[0] if url else ""
                    dom = urlparse(url).netloc.replace("www.", "") if url else ""
                    out.append((org["name"].strip(), dom))
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return out


async def _fetch(page, url: str, timeout_ms: int = 35000):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        title = (await page.title()) or ""
        text = await page.inner_text("body")
        html = await page.content()
        return title, text, html
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
        return None, None, None


async def _run_site(browser, portal: dict, country: str, site: dict, queries: list[str],
                    generic: list[str], deadline: float, sem: asyncio.Semaphore):
    rows, orgs = [], []
    async with sem:
        ctx = await browser.new_context(user_agent=USER_AGENT, locale="en-US", accept_downloads=False,
                                        service_workers="block", viewport={"width": 1366, "height": 900})
        # never let a portal page make the browser talk to your laptop/LAN
        async def _guard(route):
            if is_public_url(route.request.url) or route.request.url.startswith(("data:", "blob:")):
                await route.continue_()
            else:
                await route.abort()
        await ctx.route("**/*", _guard)
        page = await ctx.new_page()
        blocked = 0
        for i, q in enumerate(queries):
            if time.monotonic() > deadline:
                rows.append(_row(portal, country, q, None, None, "skipped_time"))
                continue
            if blocked >= 2:
                rows.append(_row(portal, country, q, None, None, "blocked"))
                continue
            url = build_url(site["url"], q)
            if not await asyncio.to_thread(robots_allowed, url):
                rows.append(_row(portal, country, q, None, None, "robots_disallowed"))
                continue
            title, text, html = await _fetch(page, url)
            if text is None:
                rows.append(_row(portal, country, q, None, None, "error"))
                continue
            if BLOCK_MARKERS.search(title) or (len(text) < 400 and BLOCK_MARKERS.search(text)):
                blocked += 1
                rows.append(_row(portal, country, q, None, None, "blocked"))
                continue
            count = parse_count(text, portal.get("count_regex", []), generic)
            fresh = None
            # freshness only for the headline query (saves time)
            if i == 0 and portal.get("fresh_param") and count:
                await asyncio.sleep(random.uniform(2, 4))
                _, ftext, _ = await _fetch(page, add_param(url, portal["fresh_param"]))
                if ftext:
                    fresh = parse_count(ftext, portal.get("count_regex", []), generic)
            orgs += extract_orgs(html or "")
            rows.append(_row(portal, country, q, count, fresh, "ok" if count is not None else "no_count"))
            await asyncio.sleep(random.uniform(3, 6))
        await ctx.close()
    log.info("portal %s/%s: %d ok of %d", portal["id"], country,
             sum(1 for r in rows if r["status"] == "ok"), len(rows))
    return rows, orgs


def _row(portal, country, q, count, fresh, status):
    return {"portal": portal["id"], "portal_name": portal["name"], "country": country, "query": q,
            "count": "" if count is None else count, "fresh_count": "" if fresh is None else fresh,
            "status": status}


async def _run_all(cfg: dict, budget_minutes: float, concurrency: int, only: set[str] | None,
                   countries: set[str] | None = None):
    from playwright.async_api import async_playwright

    deadline = time.monotonic() + budget_minutes * 60
    sem = asyncio.Semaphore(concurrency)
    queries = cfg["queries"]
    generic = cfg.get("generic_count_regex", [])
    async with async_playwright() as pw:
        # On Windows use the built-in, Microsoft-signed Edge (Smart App Control friendly); fresh temp profile,
        # never your own Edge profile. Elsewhere use Playwright's Chromium. Override with BROWSER_CHANNEL.
        channel = os.environ.get("BROWSER_CHANNEL", "msedge" if os.name == "nt" else "") or None
        browser = await pw.chromium.launch(headless=True, channel=channel,
                                           args=["--disable-blink-features=AutomationControlled"])
        tasks = []
        for portal in cfg["portals"]:
            if only and portal["id"] not in only:
                continue
            for country, site in portal["sites"].items():
                if countries and country not in countries:
                    continue
                tasks.append(_run_site(browser, portal, country, site, queries, generic, deadline, sem))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()
    rows, orgs = [], []
    for r in results:
        if isinstance(r, Exception):
            log.warning("portal task crashed: %s", r)
            continue
        rows += r[0]
        orgs += r[1]
    return rows, orgs


def run_portals(budget_minutes: float = 90, concurrency: int = 4, only: set[str] | None = None,
                queries: list[str] | None = None, countries: set[str] | None = None):
    cfg = load_config()
    if queries:
        cfg["queries"] = queries
    try:
        return asyncio.run(_run_all(cfg, budget_minutes, concurrency, only, countries))
    except Exception as e:
        log.error("portal stage failed: %s", e)
        return [], []

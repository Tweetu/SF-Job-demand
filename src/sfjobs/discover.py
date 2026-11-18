"""Detect which ATS each company uses and keep a registry (data/state/ats_registry.csv).

Order of detection:
  1. manual override in config/seed_companies.csv (ats, ats_token)
  2. scan the company's careers pages for ATS links (most accurate)
  3. probe public ATS feeds with slugs derived from the name/domain
"""
from __future__ import annotations

import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from .common import (CONFIG_DIR, STATE_DIR, csv_safe, get_logger, http_get, robots_allowed, safe_domain,
                     safe_name, slug_candidates, today_str)

log = get_logger("discover")

REGISTRY = os.path.join(STATE_DIR, "ats_registry.csv")
DISCOVERED = os.path.join(STATE_DIR, "discovered_companies.csv")
FIELDS = ["name", "domain", "category", "ats", "token", "method", "status", "checked_at"]

SUPPORTED = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}

PATTERNS = [
    ("workday", re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_\-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-\.%]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)")),
]
# Recognised but not (yet) collectable — recorded for coverage reporting
UNSUPPORTED = {
    "successfactors": re.compile(r"successfactors|jobs\.sap\.com|career\d*\.sapsf", re.I),
    "taleo": re.compile(r"taleo\.net", re.I),
    "oracle_cloud": re.compile(r"oraclecloud\.com/hcmUI/CandidateExperience", re.I),
    "icims": re.compile(r"icims\.com", re.I),
    "avature": re.compile(r"avature\.net", re.I),
    "eightfold": re.compile(r"eightfold\.ai", re.I),
    "phenom": re.compile(r"phenompeople|phenom\.com", re.I),
    "jobvite": re.compile(r"jobvite\.com", re.I),
    "brassring": re.compile(r"brassring\.com", re.I),
}
BAD_TOKENS = {"wday", "embed", "api", "jobs", "job", "careers", "en-us", "search", "login", "static", "assets"}
CAREER_LINK_RE = re.compile(r'href="([^"]+)"[^>]*>[^<]{0,60}(?:careers?|jobs|join us|work with us)', re.I)


def load_seed() -> list[dict]:
    rows = []
    with open(os.path.join(CONFIG_DIR, "seed_companies.csv"), encoding="utf-8") as f:
        rows += list(csv.DictReader(f))
    if os.path.exists(DISCOVERED):
        with open(DISCOVERED, encoding="utf-8") as f:
            known = {r["name"].lower() for r in rows}
            for r in csv.DictReader(f):
                if r["name"].lower() not in known:
                    rows.append({"name": r["name"], "domain": r.get("domain", ""), "category": "discovered",
                                 "ats": "", "ats_token": ""})
    return rows


def load_registry() -> dict[str, dict]:
    if not os.path.exists(REGISTRY):
        return {}
    with open(REGISTRY, encoding="utf-8") as f:
        return {r["name"]: r for r in csv.DictReader(f)}


def save_registry(reg: dict[str, dict]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(REGISTRY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(reg.values(), key=lambda x: x["name"].lower()):
            w.writerow({k: csv_safe(r.get(k, "")) for k in FIELDS})


def add_discovered(names_domains: list[tuple[str, str]]) -> int:
    existing = set()
    if os.path.exists(DISCOVERED):
        with open(DISCOVERED, encoding="utf-8") as f:
            existing = {r["name"].lower() for r in csv.DictReader(f)}
    seed = {r["name"].lower() for r in load_seed()}
    cleaned = [(safe_name(n), safe_domain(d)) for n, d in names_domains]
    new = [(n, d) for n, d in cleaned if len(n) >= 2 and n.lower() not in existing and n.lower() not in seed]
    if not new:
        return 0
    write_header = not os.path.exists(DISCOVERED)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(DISCOVERED, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["name", "domain", "first_seen"])
        seen = set()
        for n, d in new:
            if n.lower() in seen:
                continue
            seen.add(n.lower())
            w.writerow([csv_safe(n), d, today_str()])
    return len(seen)


# ---------------------------------------------------------------- detection
def _scan_html(html: str):
    for ats, rx in PATTERNS:
        m = rx.search(html)
        if not m:
            continue
        if ats == "workday":
            tenant, wd, site = m.groups()
            if site.lower() in BAD_TOKENS or not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", site):
                continue
            return ats, f"{tenant}|{wd}|{site}"
        tok = m.group(1)
        if tok.lower() not in BAD_TOKENS and re.fullmatch(r"[A-Za-z0-9_\-\.%]{1,80}", tok) and ".." not in tok:
            return ats, tok
    for name, rx in UNSUPPORTED.items():
        if rx.search(html):
            return f"unsupported:{name}", ""
    return None, None


def scan_careers(domain: str):
    if not domain:
        return None, None
    tried = 0
    urls = [f"https://{domain}/careers", f"https://careers.{domain}", f"https://jobs.{domain}",
            f"https://www.{domain}/careers", f"https://{domain}/en/careers", f"https://{domain}"]
    unsupported_hit = None
    domain = safe_domain(domain)
    if not domain:
        return None, None
    urls = [f"https://{domain}/careers", f"https://careers.{domain}", f"https://jobs.{domain}",
            f"https://www.{domain}/careers", f"https://{domain}/en/careers", f"https://{domain}"]
    for url in urls:
        if not robots_allowed(url):
            continue
        r = http_get(url, timeout=20, allow_redirects=True)
        tried += 1
        if r is None or r.status_code >= 400:
            continue
        body = r.text[:800_000] + " " + r.url
        ats, tok = _scan_html(body)
        if ats and not ats.startswith("unsupported"):
            return ats, tok
        if ats:
            unsupported_hit = (ats, tok)
        # follow up to 2 career-looking links one level deep
        for href in CAREER_LINK_RE.findall(r.text)[:2]:
            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if not href.startswith("http") or not robots_allowed(href):
                continue
            r2 = http_get(href, timeout=20, allow_redirects=True)
            if r2 is not None and r2.status_code < 400:
                ats2, tok2 = _scan_html(r2.text[:800_000] + " " + r2.url)
                if ats2 and not ats2.startswith("unsupported"):
                    return ats2, tok2
                if ats2:
                    unsupported_hit = (ats2, tok2)
        if tried >= 4 and unsupported_hit:
            break
    return unsupported_hit or (None, None)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def probe_slugs(name: str, domain: str):
    for slug in slug_candidates(name, domain):
        r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=15)
        if r is not None and r.status_code == 200:
            board_name = (r.json() or {}).get("name", "")
            if _similar(board_name, name) >= 0.6 or slug in board_name.lower().replace(" ", ""):
                return "greenhouse", slug
        if len(slug) >= 5:
            r = http_get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1", timeout=15)
            if r is not None and r.status_code == 200 and isinstance(r.json(), list) and r.json():
                return "lever", slug
            r = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=15)
            if r is not None and r.status_code == 200 and (r.json() or {}).get("jobs"):
                return "ashby", slug
        r = http_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1", timeout=15)
        if r is not None and r.status_code == 200 and (r.json() or {}).get("totalFound", 0) > 0:
            return "smartrecruiters", slug
    return None, None


def detect(company: dict) -> dict:
    name, domain = company["name"], company.get("domain", "")
    rec = {"name": name, "domain": domain, "category": company.get("category", ""),
           "checked_at": today_str()}
    if company.get("ats") and company.get("ats_token"):
        return {**rec, "ats": company["ats"], "token": company["ats_token"], "method": "override", "status": "ok"}
    ats, tok = scan_careers(domain)
    if ats and not ats.startswith("unsupported"):
        return {**rec, "ats": ats, "token": tok, "method": "careers_page", "status": "ok"}
    ats2, tok2 = probe_slugs(name, domain)
    if ats2:
        return {**rec, "ats": ats2, "token": tok2, "method": "slug_probe", "status": "ok"}
    if ats:
        return {**rec, "ats": ats, "token": "", "method": "careers_page", "status": "unsupported"}
    return {**rec, "ats": "", "token": "", "method": "", "status": "not_found"}


def _due(r: dict | None) -> bool:
    if r is None:
        return True
    try:
        checked = datetime.strptime(r.get("checked_at", ""), "%Y-%m-%d")
    except ValueError:
        return True
    age = datetime.utcnow() - checked
    if r.get("status") == "ok":
        return age > timedelta(days=90)
    if r.get("status") == "broken":
        return True
    return age > timedelta(days=30)


def run_discovery(max_companies: int = 400, workers: int = 8) -> dict[str, dict]:
    seed = load_seed()
    reg = load_registry()
    # overrides always win
    for c in seed:
        if c.get("ats") and c.get("ats_token"):
            reg[c["name"]] = detect(c)
    todo = [c for c in seed if _due(reg.get(c["name"]))][:max_companies]
    log.info("discovery: %d companies due (of %d)", len(todo), len(seed))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(detect, c): c for c in todo}
        for f in as_completed(futs):
            c = futs[f]
            try:
                reg[c["name"]] = f.result()
            except Exception as e:
                log.warning("detect failed for %s: %s", c["name"], e)
    save_registry(reg)
    return reg

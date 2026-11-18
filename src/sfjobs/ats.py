"""Collectors for public ATS job-board feeds.

Each collector returns (ok: bool, postings: list[dict]).
A posting dict has: source, token, job_id, title, location, country_hint, url, posted_at, text
`ok=False` means the feed could not be read this run -> postings are NOT marked closed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .common import get_logger, http_get, http_post, strip_html, utcnow

log = get_logger("ats")

# Server-side search terms (used where the ATS supports searching)
SEARCH_TERMS = ["salesforce", "sfdc", "mulesoft", "vlocity", "omnistudio", "agentforce", "tableau"]
# Vendor (Salesforce itself) — every job mentions "Salesforce", so search by product area instead
VENDOR_TERMS = [
    "Health Cloud", "Financial Services Cloud", "Life Sciences Cloud", "Manufacturing Cloud",
    "Consumer Goods Cloud", "Communications Cloud", "Energy and Utilities", "Public Sector",
    "Nonprofit", "Education Cloud", "OmniStudio", "Industries", "Data Cloud", "Agentforce",
    "Marketing Cloud", "Commerce Cloud", "Service Cloud", "Sales Cloud", "Revenue Cloud",
    "Field Service", "MuleSoft", "Tableau", "Slack",
]
TITLE_PREFILTER = re.compile(
    r"salesforce|sfdc|crm|mulesoft|vlocity|omni|agentforce|tableau|apex|lwc|cpq|marketing cloud|"
    r"commerce cloud|service cloud|sales cloud|health cloud|data cloud|einstein", re.I)

MAX_DETAILS_PER_COMPANY = 400


def _iso_ms(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _iso(s: str | None) -> str:
    return (s or "")[:10]


# ------------------------------------------------------------------ Greenhouse
def greenhouse(token: str):
    r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout=60)
    if r is None or r.status_code != 200:
        return False, []
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "source": "greenhouse", "token": token, "job_id": str(j.get("id")),
            "title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
            "country_hint": "", "url": j.get("absolute_url", ""),
            "posted_at": _iso(j.get("first_published") or j.get("updated_at")),
            "text": strip_html(j.get("content")),
        })
    return True, out


# ------------------------------------------------------------------ Lever
def lever(token: str):
    for host in ("api.lever.co", "api.eu.lever.co"):
        r = http_get(f"https://{host}/v0/postings/{token}?mode=json", timeout=60)
        if r is not None and r.status_code == 200:
            out = []
            for j in r.json():
                cats = j.get("categories") or {}
                lists = " ".join(f"{l.get('text','')} {strip_html(l.get('content'))}" for l in j.get("lists", []))
                out.append({
                    "source": "lever", "token": token, "job_id": j.get("id", ""),
                    "title": j.get("text", ""), "location": cats.get("location", ""),
                    "country_hint": j.get("country") or "", "url": j.get("hostedUrl", ""),
                    "posted_at": _iso_ms(j.get("createdAt")),
                    "text": " ".join([j.get("descriptionPlain") or "", lists, j.get("additionalPlain") or ""]),
                })
            return True, out
    return False, []


# ------------------------------------------------------------------ Ashby
def ashby(token: str):
    r = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false", timeout=60)
    if r is None or r.status_code != 200:
        return False, []
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        out.append({
            "source": "ashby", "token": token, "job_id": j.get("id", ""),
            "title": j.get("title", ""), "location": j.get("location", ""),
            "country_hint": addr.get("addressCountry", ""), "url": j.get("jobUrl", ""),
            "posted_at": _iso(j.get("publishedAt")),
            "text": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
        })
    return True, out


# ------------------------------------------------------------------ SmartRecruiters
def smartrecruiters(token: str):
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    found: dict[str, dict] = {}
    any_ok = False
    for term in SEARCH_TERMS:
        offset = 0
        while True:
            r = http_get(f"{base}?q={term}&limit=100&offset={offset}")
            if r is None or r.status_code != 200:
                break
            any_ok = True
            data = r.json()
            for j in data.get("content", []):
                if re.fullmatch(r"[A-Za-z0-9\-]{1,64}", str(j.get("id", ""))):
                    found[str(j["id"])] = j
            offset += 100
            if offset >= data.get("totalFound", 0) or offset >= 2000:
                break
    if not any_ok:
        return False, []
    out = []
    for i, (pid, j) in enumerate(found.items()):
        loc = j.get("location") or {}
        text = ""
        if i < MAX_DETAILS_PER_COMPANY:
            d = http_get(f"{base}/{pid}")
            if d is not None and d.status_code == 200:
                secs = ((d.json().get("jobAd") or {}).get("sections") or {})
                text = " ".join(strip_html((v or {}).get("text")) for v in secs.values())
        out.append({
            "source": "smartrecruiters", "token": token, "job_id": pid,
            "title": j.get("name", ""),
            "location": ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x),
            "country_hint": loc.get("country", ""),
            "url": f"https://jobs.smartrecruiters.com/{token}/{pid}",
            "posted_at": _iso(j.get("releasedDate")), "text": text,
        })
    return True, out


# ------------------------------------------------------------------ Workday
_POSTED_RE = re.compile(r"(\d+)\+?\s+days?", re.I)


def _workday_posted(s: str) -> str:
    s = (s or "").lower()
    now = utcnow()
    if "today" in s:
        return now.strftime("%Y-%m-%d")
    if "yesterday" in s:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = _POSTED_RE.search(s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    return ""


def workday(token: str, vendor: bool = False):
    """token = 'tenant|wdN|site'"""
    try:
        tenant, wd, site = token.split("|")
    except ValueError:
        return False, []
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    found: dict[str, dict] = {}
    any_ok = False
    terms = VENDOR_TERMS if vendor else SEARCH_TERMS
    for term in terms:
        offset, total = 0, None
        while True:
            r = http_post(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            if r is None or r.status_code != 200:
                break
            any_ok = True
            data = r.json()
            if total is None:
                total = data.get("total", 0) or 0
            posts = data.get("jobPostings", []) or []
            for j in posts:
                ep = j.get("externalPath") or ""
                if re.fullmatch(r"/job/[A-Za-z0-9_\-./%()]{1,300}", ep) and ".." not in ep:
                    found.setdefault(j["externalPath"], j)
            offset += 20
            if not posts or offset >= total or offset >= 1000:
                break
    if not any_ok:
        return False, []
    out = []
    for i, (path, j) in enumerate(found.items()):
        title = j.get("title", "")
        text, country, loc, posted, url = "", "", j.get("locationsText", ""), _workday_posted(j.get("postedOn")), ""
        if i < MAX_DETAILS_PER_COMPANY and (vendor or TITLE_PREFILTER.search(title) or len(found) <= 150):
            d = http_get(f"{api}{path}")
            if d is not None and d.status_code == 200:
                info = d.json().get("jobPostingInfo") or {}
                text = strip_html(info.get("jobDescription"))
                country = ((info.get("country") or {}).get("descriptor")) or ""
                loc = info.get("location") or loc
                posted = _iso(info.get("startDate")) or posted
                url = info.get("externalUrl") or ""
        out.append({
            "source": "workday", "token": token, "job_id": path.rsplit("_", 1)[-1] if "_" in path else path,
            "title": title, "location": loc, "country_hint": country,
            "url": url or f"{host}/{site}{path}", "posted_at": posted,
            "text": text,
        })
    return True, out


COLLECTORS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
}


def collect(ats: str, token: str, vendor: bool = False):
    fn = COLLECTORS.get(ats)
    if not fn:
        return False, []
    try:
        if ats == "workday":
            return fn(token, vendor=vendor)
        return fn(token)
    except Exception as e:  # never let one company break the run
        log.warning("collector %s/%s crashed: %s", ats, token, e)
        return False, []

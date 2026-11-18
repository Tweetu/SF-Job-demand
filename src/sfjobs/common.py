"""Shared helpers: polite HTTP session, per-host throttling, text utils, logging."""
from __future__ import annotations

import html as _html
import ipaddress
import socket
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
STATE_DIR = os.path.join(DATA_DIR, "state")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
REPORT_DIR = os.path.join(ROOT, "reports")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 sf-job-demand-research"
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


log = get_logger("common")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return utcnow().strftime("%Y-%m-%d")


def week_label(d: datetime | None = None) -> str:
    d = d or utcnow()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------- throttling
class HostThrottle:
    """Ensures a minimum interval between requests to the same host (thread-safe)."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            last = self._last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last[host] = time.monotonic()


THROTTLE = HostThrottle(float(os.environ.get("MIN_HOST_INTERVAL", "0.8")))

_session_local = threading.local()


def session() -> requests.Session:
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        s.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        _session_local.s = s
    return s


# ---------------------------------------------------------------- safety
_HOST_OK: dict[str, bool] = {}
MAX_BYTES = 5_000_000  # never read more than 5 MB from any response


def is_public_url(url: str) -> bool:
    """Only https/http to public internet hosts. Blocks localhost, LAN (192.168.x etc.),
    cloud metadata endpoints and raw IPs, so external data can never point the crawler
    at your laptop, router or office network."""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("https", "http") or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in _HOST_OK:
        return _HOST_OK[host]
    ok = True
    try:
        ipaddress.ip_address(host)
        ok = False  # raw IP addresses are never legitimate careers sites
    except ValueError:
        if "." not in host or host.endswith((".local", ".internal", ".lan", ".localhost", ".corp", ".home")):
            ok = False
        else:
            try:
                for info in socket.getaddrinfo(host, None):
                    ip = ipaddress.ip_address(info[4][0])
                    if not ip.is_global:
                        ok = False
                        break
            except OSError:
                ok = True  # unresolvable here (e.g. behind a proxy) -> let the request fail normally
    _HOST_OK[host] = ok
    return ok


def _read_capped(r: requests.Response) -> requests.Response:
    cl = r.headers.get("Content-Length")
    if cl and cl.isdigit() and int(cl) > MAX_BYTES:
        raise requests.RequestException("response too large")
    chunks, size = [], 0
    for chunk in r.iter_content(65536):
        size += len(chunk)
        if size > MAX_BYTES:
            raise requests.RequestException("response too large")
        chunks.append(chunk)
    r._content = b"".join(chunks)
    return r


def _request(method: str, url: str, timeout: float, allow_redirects: bool = True, **kw):
    for _ in range(6):  # follow at most 5 redirects, checking each hop
        if not is_public_url(url):
            log.debug("blocked non-public url %s", url)
            return None
        THROTTLE.wait(url)
        try:
            r = session().request(method, url, timeout=timeout, allow_redirects=False, stream=True, **kw)
        except requests.RequestException as e:
            log.debug("%s failed %s: %s", method, url, e)
            return None
        if allow_redirects and r.is_redirect and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            if method == "POST":
                return None  # never re-post to a redirected host
            continue
        try:
            return _read_capped(r)
        except requests.RequestException as e:
            log.debug("read failed %s: %s", url, e)
            return None
        finally:
            r.close()
    return None


def http_get(url: str, timeout: float = 25, allow_redirects: bool = True, **kw) -> requests.Response | None:
    return _request("GET", url, timeout, allow_redirects=allow_redirects, **kw)


def http_post(url: str, json_body: dict, timeout: float = 25, **kw) -> requests.Response | None:
    return _request("POST", url, timeout, allow_redirects=False, json=json_body, **kw)


# ---------------------------------------------------------------- robots.txt
_ROBOTS: dict[str, RobotFileParser | None] = {}
RESPECT_ROBOTS = os.environ.get("RESPECT_ROBOTS", "1") != "0"


def robots_allowed(url: str) -> bool:
    """True if robots.txt allows our crawler to fetch url (missing/unreadable robots.txt = allowed)."""
    if not RESPECT_ROBOTS:
        return True
    u = urlparse(url)
    base = f"{u.scheme}://{u.netloc}"
    if base not in _ROBOTS:
        rp = None
        r = http_get(base + "/robots.txt", timeout=15)
        if r is not None and r.status_code == 200:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
        _ROBOTS[base] = rp
    rp = _ROBOTS[base]
    return True if rp is None else rp.can_fetch("sf-job-demand-research", url)


# ---------------------------------------------------------------- untrusted text
_NAME_OK = re.compile(r"[^\w\s&.,'()+\-/]", re.U)


def safe_name(s: str, maxlen: int = 80) -> str:
    """Company names scraped from the web: strip markup/control chars, @mentions, links; cap length."""
    s = strip_html(s or "")
    s = _NAME_OK.sub("", s)
    s = re.sub(r"https?\S*|www\.\S*", "", s, flags=re.I)
    return _WS_RE.sub(" ", s).strip()[:maxlen]


def safe_domain(d: str) -> str:
    d = (d or "").lower().strip().strip(".")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", d):
        return ""
    return d


def csv_safe(v):
    """Prevent spreadsheet formula injection when CSVs are opened in Excel."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


# ---------------------------------------------------------------- text utils
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = _html.unescape(s)
    s = _TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(sr|senior|jr|junior|i{1,3}|iv|lead|staff|principal)\b", " ", s)
    return _WS_RE.sub(" ", s).strip()


def slug_candidates(name: str, domain: str = "") -> list[str]:
    n = name.lower().replace("&", "and")
    n = re.sub(r"[^a-z0-9 ]+", "", n)
    words = n.split()
    cands = []
    if words:
        cands += ["".join(words), "-".join(words)]
        if len(words) > 1:
            cands.append(words[0])
    if domain:
        stem = domain.lower().split(".")[0]
        cands.append(stem)
    out = []
    for c in cands:
        if len(c) >= 3 and c not in out:
            out.append(c)
    return out

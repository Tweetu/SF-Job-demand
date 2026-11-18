"""Persistent posting history: first_seen / last_seen / closed_at per posting."""
from __future__ import annotations

import csv
import gzip
import os
from datetime import datetime, timedelta

from .common import DATA_DIR, STATE_DIR, csv_safe, norm_title

INDEX = os.path.join(STATE_DIR, "postings_index.csv.gz")
METRICS_DIR = os.path.join(DATA_DIR, "metrics")
FIELDS = ["key", "source", "token", "company", "category", "job_id", "title", "norm_title", "location",
          "country", "url", "posted_at", "clouds", "roles", "first_seen", "last_seen", "closed_at",
          "status", "reopen_count", "repost"]
KEEP_CLOSED_DAYS = 400


def _d(s: str):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


def load_index() -> dict[str, dict]:
    if not os.path.exists(INDEX):
        return {}
    with gzip.open(INDEX, "rt", encoding="utf-8", newline="") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def save_index(idx: dict[str, dict]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = INDEX + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in idx.values():
            w.writerow({k: csv_safe(r.get(k, "")) for k in FIELDS})
    os.replace(tmp, INDEX)


def update_index(idx: dict[str, dict], current: dict[str, dict], ok_feeds: set[str], run_date: str) -> dict:
    """Mutates idx. Returns counters."""
    stats = {"new": 0, "closed": 0, "reopened": 0, "reposts": 0, "kept_open_feed_failed": 0}
    rd = _d(run_date)

    # recently closed lookup for repost detection
    recent_closed = {}
    for r in idx.values():
        if r["status"] == "closed":
            cd = _d(r.get("closed_at", ""))
            if cd and (rd - cd).days <= 60:
                recent_closed[(r["company"], r["norm_title"], r["country"])] = r["key"]

    for key, p in current.items():
        rec = idx.get(key)
        if rec is None:
            rec = {**p, "key": key, "norm_title": norm_title(p["title"]), "first_seen": run_date,
                   "last_seen": run_date, "closed_at": "", "status": "open", "reopen_count": "0", "repost": "0"}
            if (rec["company"], rec["norm_title"], rec["country"]) in recent_closed:
                rec["repost"] = "1"
                stats["reposts"] += 1
            idx[key] = rec
            stats["new"] += 1
        else:
            if rec["status"] == "closed":
                rec["status"] = "open"
                rec["closed_at"] = ""
                rec["reopen_count"] = str(int(rec.get("reopen_count") or 0) + 1)
                stats["reopened"] += 1
            for k in ("title", "location", "country", "url", "clouds", "roles", "category", "company"):
                if p.get(k):
                    rec[k] = p[k]
            if p.get("posted_at") and not rec.get("posted_at"):
                rec["posted_at"] = p["posted_at"]
            rec["norm_title"] = norm_title(rec["title"])
            rec["last_seen"] = run_date

    for key, rec in idx.items():
        if rec["status"] != "open" or key in current:
            continue
        feed = f"{rec['source']}|{rec['token']}"
        if feed in ok_feeds:
            rec["status"] = "closed"
            rec["closed_at"] = run_date
            stats["closed"] += 1
        else:
            stats["kept_open_feed_failed"] += 1

    # prune very old closed postings
    for key in [k for k, r in idx.items() if r["status"] == "closed"
                and _d(r["closed_at"]) and (rd - _d(r["closed_at"])).days > KEEP_CLOSED_DAYS]:
        del idx[key]
    return stats


def start_date(rec: dict):
    fs = _d(rec.get("first_seen", ""))
    pa = _d(rec.get("posted_at", ""))
    if pa and fs and pa <= fs and (fs - pa).days <= 365:
        return pa
    return fs


def days_open(rec: dict, run_date: str) -> int | None:
    s = start_date(rec)
    e = _d(rec.get("closed_at") or run_date)
    if s and e:
        return max(0, (e - s).days)
    return None


def append_csv(path: str, rows: list[dict], fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: csv_safe(r.get(k, "")) for k in fields})


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

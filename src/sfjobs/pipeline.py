"""Weekly run: discover -> collect -> classify -> update history -> portals -> metrics -> report.

Usage:
  python -m sfjobs.pipeline                 # full run
  python -m sfjobs.pipeline --skip-portals  # company feeds only
  python -m sfjobs.pipeline --only-portals naukri,seek --skip-collect
"""
from __future__ import annotations

import argparse
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import report
from .ats import collect
from .common import get_logger, today_str, week_label
from .discover import SUPPORTED, add_discovered, load_registry, load_seed, run_discovery, save_registry
from .geo import country_of
from .portals import run_portals
from .state import METRICS_DIR, append_csv, load_index, read_csv, save_index, update_index
from .taxonomy import get_taxonomy

log = get_logger("pipeline")

WEEKLY_FIELDS = ["week", "run_date", "cloud", "country", "open", "new", "closed"]
PORTAL_FIELDS = ["week", "run_date", "portal", "portal_name", "country", "query", "count", "fresh_count", "status"]
RUN_FIELDS = ["week", "run_date", "companies_tracked", "feeds_ok", "feeds_failed", "relevant_open",
              "new", "closed", "reposts", "portal_sites", "portal_ok_rows", "portal_blocked_rows",
              "discovered_new", "minutes"]


def replace_week(path: str, rows: list[dict], fields: list[str], week: str) -> None:
    existing = [r for r in read_csv(path) if r.get("week") != week]
    if os.path.exists(path):
        os.remove(path)
    append_csv(path, existing + rows, fields)


def collect_all(reg: dict, workers: int = 8):
    tax = get_taxonomy()
    feeds: dict[str, dict] = {}
    for r in reg.values():
        if r.get("status") in ("ok", "broken") and r.get("ats") in SUPPORTED and r.get("token"):
            feeds.setdefault(f"{r['ats']}|{r['token']}", r)  # dedupe shared feeds
    log.info("collecting %d company feeds", len(feeds))
    current, ok_feeds, failed = {}, set(), []
    raw_total = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(collect, r["ats"], r["token"], r.get("category") == "vendor"): (fk, r)
                for fk, r in feeds.items()}
        for f in as_completed(futs):
            fk, r = futs[f]
            ok, posts = f.result()
            if not ok:
                failed.append(r["name"])
                if r.get("status") == "ok":
                    r["status"] = "broken"   # re-detected on next discovery
                continue
            r["status"] = "ok"
            ok_feeds.add(fk)
            raw_total += len(posts)
            for p in posts:
                c = tax.classify(p["title"], p.get("text", ""))
                if not c["relevant"]:
                    continue
                key = f"{fk}|{p['job_id']}"
                current[key] = {
                    "source": r["ats"], "token": r["token"], "company": r["name"],
                    "category": r.get("category", ""), "job_id": p["job_id"], "title": p["title"],
                    "location": p.get("location", ""), "country": country_of(p.get("location"), p.get("country_hint")),
                    "url": p.get("url", ""), "posted_at": p.get("posted_at", ""),
                    "clouds": ";".join(c["clouds"]), "roles": ";".join(c["roles"]),
                }
    log.info("feeds ok=%d failed=%d raw postings=%d relevant=%d", len(ok_feeds), len(failed), raw_total, len(current))
    return current, ok_feeds, failed


def weekly_rows(idx: dict, run_date: str, week: str) -> list[dict]:
    agg = defaultdict(Counter)
    for r in idx.values():
        is_open = r["status"] == "open"
        is_new = r["first_seen"] == run_date
        is_closed = r["status"] == "closed" and r["closed_at"] == run_date
        if not (is_open or is_closed):
            continue
        for cloud in (r["clouds"] or "salesforce_general").split(";"):
            for country in (r["country"], "ALL"):
                k = (cloud, country)
                if is_open:
                    agg[k]["open"] += 1
                if is_new:
                    agg[k]["new"] += 1
                if is_closed:
                    agg[k]["closed"] += 1
    return [{"week": week, "run_date": run_date, "cloud": c, "country": co, **v} for (c, co), v in agg.items()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-discovery", action="store_true")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-portals", action="store_true")
    ap.add_argument("--only-portals", default="")
    ap.add_argument("--portal-budget", type=float, default=float(os.environ.get("PORTAL_BUDGET_MIN", "90")))
    ap.add_argument("--max-discovery", type=int, default=int(os.environ.get("MAX_DISCOVERY", "400")))
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    run_date, week = today_str(), week_label()
    log.info("=== run %s (%s) ===", run_date, week)

    reg = load_registry() if args.skip_discovery else run_discovery(max_companies=args.max_discovery)
    cats = {c["name"]: c.get("category", "") for c in load_seed()}
    for r in reg.values():
        if r["name"] in cats:
            r["category"] = cats[r["name"]]

    idx = load_index()
    stats = {"new": 0, "closed": 0, "reposts": 0}
    failed, ok_feeds = [], set()
    if not args.skip_collect:
        current, ok_feeds, failed = collect_all(reg)
        save_registry(reg)
        stats = update_index(idx, current, ok_feeds, run_date)
        save_index(idx)
        log.info("history: %s", stats)

    portal_rows, discovered_new = [], 0
    if not args.skip_portals:
        only = set(x for x in args.only_portals.split(",") if x) or None
        portal_rows, orgs = run_portals(budget_minutes=args.portal_budget, only=only)
        discovered_new = add_discovered(orgs)
        for r in portal_rows:
            r.update({"week": week, "run_date": run_date})
        replace_week(os.path.join(METRICS_DIR, "portal_counts.csv"), portal_rows, PORTAL_FIELDS, week)

    if not args.skip_collect:
        replace_week(os.path.join(METRICS_DIR, "weekly_cloud_country.csv"),
                     weekly_rows(idx, run_date, week), WEEKLY_FIELDS, week)

    run = {
        "week": week, "run_date": run_date,
        "companies_tracked": sum(1 for r in reg.values() if r.get("status") == "ok"),
        "feeds_ok": len(ok_feeds), "feeds_failed": len(failed),
        "relevant_open": sum(1 for r in idx.values() if r["status"] == "open"),
        "new": stats.get("new", 0), "closed": stats.get("closed", 0), "reposts": stats.get("reposts", 0),
        "portal_sites": len({(r["portal"], r["country"]) for r in portal_rows}),
        "portal_ok_rows": sum(1 for r in portal_rows if r["status"] == "ok"),
        "portal_blocked_rows": sum(1 for r in portal_rows if r["status"] == "blocked"),
        "discovered_new": discovered_new,
        "minutes": round((time.monotonic() - t0) / 60, 1),
    }
    replace_week(os.path.join(METRICS_DIR, "runs.csv"), [run], RUN_FIELDS, week)

    report.build(idx=idx, reg=reg, run=run, failed_feeds=failed, run_date=run_date, week=week)
    log.info("done in %.1f min", run["minutes"])


if __name__ == "__main__":
    main()

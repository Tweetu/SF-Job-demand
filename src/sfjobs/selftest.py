"""Quick local check (~5-10 min). Reads live sites but WRITES NOTHING to data/.

  python -m sfjobs.selftest            # everything
  python -m sfjobs.selftest --no-portals
"""
from __future__ import annotations

import argparse
import time

from .ats import collect
from .common import http_get, http_post
from .discover import detect, load_seed
from .portals import load_config, run_portals
from .taxonomy import get_taxonomy

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-portals", action="store_true")
    ap.add_argument("--companies", default="Salesforce,Slalom,Databricks,Stripe,Coinbase,Accenture,Deloitte,Infosys")
    args = ap.parse_args(argv)
    t0 = time.time()

    print("\n1) Internet access")
    r = http_get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs")
    check("can reach public job feeds", r is not None, f"status={getattr(r, 'status_code', 'no response')}")
    if r is None:
        print("\n  No internet from Python. If you are on an office network/VPN, try home Wi-Fi or set HTTPS_PROXY.")
        return

    print("\n2) Classifier")
    t = get_taxonomy()
    c1 = t.classify("Salesforce Health Cloud Developer", "OmniStudio FlexCards, Apex")
    c2 = t.classify("Account Executive", "You will use Salesforce CRM daily")
    check("tags an Industries job", "health_cloud" in c1["clouds"], str(c1["clouds"]))
    check("ignores a sales rep job", not c2["relevant"])

    print("\n3) Workday search (Salesforce careers)")
    r = http_post("https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs",
                  {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "Health Cloud"})
    ok = r is not None and r.status_code == 200
    check("Workday endpoint answers", ok, f"total Health Cloud jobs at Salesforce: {r.json().get('total') if ok else '-'}")

    print("\n4) Company detection + feed collection")
    seed = {c["name"]: c for c in load_seed()}
    for name in [n.strip() for n in args.companies.split(",") if n.strip()]:
        c = seed.get(name) or {"name": name, "domain": "", "category": "", "ats": "", "ats_token": ""}
        rec = detect(c)
        if rec["status"] != "ok":
            check(f"{name}: detect", rec["status"] == "unsupported",
                  f"-> {rec['status']} {rec['ats']} (unsupported/not_found is normal for some companies)")
            continue
        ok, posts = collect(rec["ats"], rec["token"], vendor=c.get("category") == "vendor") \
            if rec["ats"] != "workday" else _workday_light(rec["token"])
        rel = sum(1 for p in posts if t.classify(p["title"], p.get("text", ""))["relevant"])
        check(f"{name}: {rec['ats']} ({rec['method']})", ok, f"{len(posts)} jobs read, {rel} Salesforce-relevant")

    if not args.no_portals:
        print("\n5) Job portals (2 queries x a few countries, ~3-5 min)")
        rows, orgs = run_portals(budget_minutes=6, concurrency=4, queries=["Salesforce", "Salesforce Health Cloud"],
                                 countries={"India", "United States", "United Kingdom", "Australia", "United Arab Emirates"})
        by_site = {}
        for r in rows:
            by_site.setdefault((r["portal_name"], r["country"]), []).append(r)
        for (p, ctry), rs in sorted(by_site.items()):
            head = next((x for x in rs if x["query"] == "Salesforce"), rs[0])
            check(f"{p} / {ctry}", head["status"] == "ok",
                  f"count={head['count'] or '-'} fresh={head['fresh_count'] or '-'} status={head['status']}")
        print(f"  companies found in portal pages: {len(set(orgs))}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n==> {passed}/{len(RESULTS)} checks passed in {time.time()-t0:.0f}s")
    print("    A few portal FAILs are expected (blocked/changed pages) - the weekly run skips them automatically.")
    print("    Send the FAIL lines to Claude if core checks (1-4) fail.\n")


def _workday_light(token):
    """Only first page of one search, to keep the self-test fast."""
    tenant, wd, site = token.split("|")
    r = http_post(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                  {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "salesforce"})
    if r is None or r.status_code != 200:
        return False, []
    return True, [{"title": j.get("title", ""), "text": ""} for j in r.json().get("jobPostings", [])]


if __name__ == "__main__":
    main()

"""Build reports/summary.md (weekly email via GitHub issue) and reports/dashboard.html."""
from __future__ import annotations

import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .common import REPORT_DIR, get_logger
from .state import METRICS_DIR, days_open, read_csv
from .taxonomy import get_taxonomy

log = get_logger("report")


def _md(s) -> str:
    """Escape untrusted text (company names etc.) for GitHub markdown tables."""
    s = str(s)
    for ch in "\\|`*_[]<>#!":
        s = s.replace(ch, "\\" + ch)
    return s.replace("@", "@\u200b")  # no accidental @mentions


def _pct(a, b):
    return f"{(100 * a / b):.0f}%" if b else "–"


def _delta(cur, prev):
    if prev in (None, 0):
        return "new" if cur else "–"
    d = (cur - prev) / prev * 100
    arrow = "▲" if d > 2 else ("▼" if d < -2 else "▬")
    return f"{arrow} {d:+.0f}%"


def _spark(vals):
    bars = "▁▂▃▄▅▆▇█"
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return bars[3] * len(vals)
    return "".join(bars[int((v - lo) / (hi - lo) * 7)] for v in vals)


def compute(idx: dict, reg: dict, run: dict, failed_feeds: list, run_date: str, week: str) -> dict:
    tax = get_taxonomy()
    rd = datetime.strptime(run_date, "%Y-%m-%d")
    open_recs = [r for r in idx.values() if r["status"] == "open"]

    # ---- per cloud
    cloud_open, cloud_new, cloud_closed = Counter(), Counter(), Counter()
    cloud_country = defaultdict(Counter)
    cloud_company = defaultdict(Counter)
    cloud_role = defaultdict(Counter)
    ttc = defaultdict(list)
    eight_weeks_ago = rd - timedelta(weeks=8)
    for r in idx.values():
        clouds = (r["clouds"] or "salesforce_general").split(";")
        is_open = r["status"] == "open"
        for c in clouds:
            if is_open:
                cloud_open[c] += 1
                cloud_country[c][r["country"]] += 1
                cloud_company[c][r["company"]] += 1
                for role in (r["roles"] or "other").split(";"):
                    cloud_role[c][role] += 1
                if r["first_seen"] == run_date:
                    cloud_new[c] += 1
            elif r["closed_at"]:
                if r["closed_at"] == run_date:
                    cloud_closed[c] += 1
                cd = datetime.strptime(r["closed_at"], "%Y-%m-%d")
                if cd >= eight_weeks_ago:
                    d = days_open(r, run_date)
                    if d is not None:
                        ttc[c].append(d)

    # ---- trend from history
    hist = read_csv(os.path.join(METRICS_DIR, "weekly_cloud_country.csv"))
    weeks = sorted({h["week"] for h in hist})[-8:]
    trend = defaultdict(dict)
    for h in hist:
        if h["country"] == "ALL" and h["week"] in weeks:
            trend[h["cloud"]][h["week"]] = int(h["open"] or 0)
    prev_week = weeks[-2] if len(weeks) >= 2 else None

    def cloud_row(c):
        series = [trend[c].get(w) for w in weeks]
        med = statistics.median(ttc[c]) if len(ttc[c]) >= 5 else None
        return {
            "key": c, "label": tax.label(c), "group": tax.group_of(c),
            "open": cloud_open[c], "new": cloud_new[c], "closed": cloud_closed[c],
            "wow": _delta(cloud_open[c], trend[c].get(prev_week) if prev_week else None),
            "spark": _spark(series), "series": series,
            "median_days_to_close": med, "ttc_n": len(ttc[c]),
            "top_countries": cloud_country[c].most_common(3),
            "top_companies": cloud_company[c].most_common(5),
            "roles": cloud_role[c].most_common(4),
        }

    all_keys = list(tax.clouds.keys()) + ["salesforce_general"]
    clouds = [cloud_row(c) for c in all_keys]
    clouds.sort(key=lambda x: -x["open"])

    # ---- countries
    industries = set(tax.industries_keys())
    country_tot, country_ind, country_top_ind = Counter(), Counter(), defaultdict(Counter)
    for r in open_recs:
        cl = set((r["clouds"] or "").split(";"))
        country_tot[r["country"]] += 1
        ind = cl & industries
        if ind:
            country_ind[r["country"]] += 1
            for c in ind:
                country_top_ind[r["country"]][c] += 1
    countries = [{
        "country": c, "open": n, "industries": country_ind[c], "industries_share": _pct(country_ind[c], n),
        "top_industries": [(tax.label(k), v) for k, v in country_top_ind[c].most_common(2)],
    } for c, n in country_tot.most_common(25)]

    # ---- evergreen / quality
    evergreen = sum(1 for r in open_recs if (days_open(r, run_date) or 0) > 90
                    or int(r.get("reopen_count") or 0) > 0 or r.get("repost") == "1")

    # ---- portals (this week)
    prow = [p for p in read_csv(os.path.join(METRICS_DIR, "portal_counts.csv")) if p["week"] == week]
    psites = defaultdict(lambda: {"headline": None, "fresh": None, "industries_sum": 0, "ok": 0, "rows": 0,
                                  "blocked": 0, "name": ""})
    ind_queries = {"Salesforce Financial Services Cloud", "Salesforce Health Cloud", "Salesforce Life Sciences Cloud",
                   "Salesforce Manufacturing Cloud", "Salesforce Consumer Goods Cloud",
                   "Salesforce Communications Cloud", "Salesforce Energy Utilities Cloud", "Salesforce Public Sector",
                   "Salesforce Nonprofit Cloud", "Salesforce Education Cloud", "Salesforce Insurance",
                   "OmniStudio", "Vlocity"}
    for p in prow:
        s = psites[(p["portal"], p["country"])]
        s["name"] = p["portal_name"]
        s["rows"] += 1
        if p["status"] == "blocked":
            s["blocked"] += 1
        if p["status"] != "ok":
            continue
        s["ok"] += 1
        cnt = int(p["count"]) if p["count"] != "" else 0
        if p["query"] == "Salesforce":
            s["headline"] = cnt
            s["fresh"] = int(p["fresh_count"]) if p["fresh_count"] != "" else None
        if p["query"] in ind_queries:
            s["industries_sum"] += cnt
    portals_by_country = defaultdict(list)
    portal_health = []
    for (pid, country), s in psites.items():
        healthy = s["rows"] and s["ok"] / s["rows"] >= 0.5
        if not healthy:
            portal_health.append({"portal": s["name"], "country": country,
                                  "issue": "blocked" if s["blocked"] else "count not found"})
            continue
        fresh_ratio = (s["fresh"] / s["headline"]) if (s["fresh"] is not None and s["headline"]) else None
        portals_by_country[country].append({
            "portal": s["name"], "salesforce_jobs": s["headline"], "fresh_7d": s["fresh"],
            "freshness": f"{fresh_ratio*100:.0f}%" if fresh_ratio is not None else "–",
            "industries_index": s["industries_sum"],
            # Score: fresh volume matters most, then total volume, then industries depth
            "score": (s["fresh"] or 0) * 3 + (s["headline"] or 0) + s["industries_sum"] * 0.5,
        })
    for c in portals_by_country:
        portals_by_country[c].sort(key=lambda x: -x["score"])

    # ---- coverage
    status_ct = Counter(r.get("status") for r in reg.values())
    unsupported_ct = Counter(r.get("ats", "").replace("unsupported:", "") for r in reg.values()
                             if r.get("status") == "unsupported")
    ats_ct = Counter(r.get("ats") for r in reg.values() if r.get("status") == "ok")

    runs = read_csv(os.path.join(METRICS_DIR, "runs.csv"))
    return {
        "week": week, "run_date": run_date, "run": run, "weeks": weeks,
        "clouds": clouds, "countries": countries, "evergreen": evergreen, "open_total": len(open_recs),
        "portals_by_country": dict(portals_by_country), "portal_health": portal_health,
        "coverage": {"status": dict(status_ct), "unsupported": dict(unsupported_ct), "ats": dict(ats_ct)},
        "failed_feeds": failed_feeds, "history_weeks": len(runs),
    }


# ------------------------------------------------------------------ markdown
def to_markdown(m: dict) -> str:
    run = m["run"]
    L = []
    L.append(f"# Salesforce Job Demand — {m['week']} (run {m['run_date']})\n")
    L.append(f"**{m['open_total']:,}** open Salesforce ecosystem postings tracked across "
             f"**{run['feeds_ok']}** company career feeds · **{run['new']:,}** new this run · "
             f"**{run['closed']:,}** closed · history: {m['history_weeks']} week(s)\n")
    if m["history_weeks"] < 4:
        L.append("> ℹ️ Trends and time-to-close become reliable after ~4–8 weekly runs. "
                 "Until then, focus on the *open* counts and the portal ranking.\n")

    def cloud_table(rows):
        L.append("| Cloud | Open | New | Closed | WoW | Trend | Median days to close | Top countries |")
        L.append("|---|---:|---:|---:|---|---|---:|---|")
        for c in rows:
            med = f"{c['median_days_to_close']:.0f} (n={c['ttc_n']})" if c["median_days_to_close"] is not None else "–"
            tc = ", ".join(f"{_md(k)} {v}" for k, v in c["top_countries"])
            L.append(f"| {c['label']} | {c['open']:,} | {c['new']} | {c['closed']} | {c['wow']} | {c['spark']} | {med} | {tc} |")
        L.append("")

    def section(title, rows):
        L.append(title + "\n")
        active = [c for c in rows if c["open"] or c["closed"]]
        if active:
            cloud_table(active)
        quiet = [c["label"] for c in rows if not (c["open"] or c["closed"])]
        if quiet:
            L.append(f"_No postings this week:_ {', '.join(quiet)}\n")

    section("## 🏭 Industries Clouds", [c for c in m["clouds"] if c["group"] == "industries"])
    section("## ☁️ Core, Data & AI, Platform", [c for c in m["clouds"] if c["group"] != "industries"])

    L.append("## 🌍 Top countries\n")
    L.append("| Country | Open | Industries | Share | Top Industries clouds |")
    L.append("|---|---:|---:|---:|---|")
    for c in m["countries"][:15]:
        ti = ", ".join(f"{_md(k)} {v}" for k, v in c["top_industries"])
        L.append(f"| {_md(c['country'])} | {c['open']:,} | {c['industries']} | {c['industries_share']} | {ti} |")
    L.append("")

    L.append("## 🏢 Who is hiring (Industries clouds)\n")
    for c in [c for c in m["clouds"] if c["group"] == "industries" and c["open"]][:8]:
        comp = ", ".join(f"{_md(k)} ({v})" for k, v in c["top_companies"])
        L.append(f"- **{c['label']}**: {comp}")
    L.append("")

    L.append("## 📊 Best job portals by country\n")
    L.append("Ranked by fresh (last ~7 days) Salesforce postings, then total volume, then Industries depth. "
             "Portal counts are search-result counts — use them to compare portals, not as exact demand.\n")
    for country in sorted(m["portals_by_country"], key=lambda c: -sum(p["salesforce_jobs"] or 0 for p in m["portals_by_country"][c])):
        ps = m["portals_by_country"][country]
        L.append(f"**{_md(country)}**: " + " · ".join(
            f"{i+1}. {_md(p['portal'])} ({(p['salesforce_jobs'] or 0):,} jobs, fresh {p['freshness']}, industries idx {p['industries_index']:,})"
            for i, p in enumerate(ps[:4])))
    L.append("")

    L.append("## 🩺 Health & coverage\n")
    cov = m["coverage"]
    L.append(f"- Companies with a working feed: **{cov['status'].get('ok', 0)}** "
             f"({', '.join(f'{k} {v}' for k, v in sorted(cov['ats'].items(), key=lambda x: -x[1]))})")
    if cov["unsupported"]:
        L.append(f"- On ATS not yet collectable: {', '.join(f'{k} {v}' for k, v in sorted(cov['unsupported'].items(), key=lambda x: -x[1]))}")
    L.append(f"- No careers system detected: {cov['status'].get('not_found', 0)}")
    L.append(f"- Evergreen/reposted share of open postings: {_pct(m['evergreen'], m['open_total'])}")
    L.append(f"- New companies discovered from portals this run: {run.get('discovered_new', 0)}")
    if m["failed_feeds"]:
        L.append(f"- ⚠️ Feeds that failed this run ({len(m['failed_feeds'])}, auto re-detected next run): "
                 + ", ".join(_md(x) for x in sorted(m["failed_feeds"])[:25]))
    if m["portal_health"]:
        L.append(f"- ⚠️ Portal sites not readable ({len(m['portal_health'])}): "
                 + ", ".join(f"{_md(p['portal'])}/{_md(p['country'])} ({p['issue']})" for p in m["portal_health"][:30]))
    L.append(f"- Run time: {run['minutes']} min")
    L.append("\n_Full interactive dashboard: download `reports/dashboard.html` from the repo and open it in a browser._")
    return "\n".join(L)


# ------------------------------------------------------------------ html
HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'">
<title>Salesforce Job Demand — __WEEK__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js" crossorigin="anonymous"></script>
<style>
:root{--bg:#f7f8fa;--card:#fff;--ink:#1d2433;--mute:#6b7385;--line:#e4e7ee;--a:#0b5cab;--b:#2e844a;--c:#dd7a01}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:24px 32px;background:#032d60;color:#fff}header h1{margin:0;font-size:22px}header p{margin:4px 0 0;opacity:.8}
main{padding:24px 32px;display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(460px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
.card h2{margin:0 0 12px;font-size:15px}.wide{grid-column:1/-1}
.kpis{display:flex;gap:28px;flex-wrap:wrap}.kpi b{display:block;font-size:26px}.kpi span{color:var(--mute)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--mute);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
select{padding:4px 8px;border-radius:6px;border:1px solid var(--line)}.note{color:var(--mute);font-size:12px}
</style></head><body>
<header><h1>Salesforce Job Demand</h1><p>__WEEK__ · run __DATE__ · company career feeds + job portals</p></header>
<main>
<section class="card wide"><div class="kpis" id="kpis"></div></section>
<section class="card"><h2>Industries Clouds — open postings</h2><canvas id="ind"></canvas></section>
<section class="card"><h2>Industries Clouds — weekly trend</h2><canvas id="trend"></canvas>
<p class="note">Trend lines fill in as weekly history accumulates.</p></section>
<section class="card"><h2>All clouds — open postings</h2><canvas id="all"></canvas></section>
<section class="card"><h2>Top countries</h2><canvas id="ctry"></canvas></section>
<section class="card wide"><h2>Cloud detail</h2><table id="cloudtbl"></table></section>
<section class="card wide"><h2>Best job portals by country
 <select id="pc"></select></h2><table id="ptbl"></table>
<p class="note">Score = 3×fresh(7d) + total + 0.5×Industries index. Counts are portal search results.</p></section>
<section class="card wide"><h2>Health & coverage</h2><div id="health"></div></section>
</main>
<script>
const M = __DATA__;
const C = ['#0b5cab','#2e844a','#dd7a01','#ba0517','#7526e3','#06a59a','#c23934','#3a49da','#8a6d3b','#5a6b7b','#d83a8a','#4b8f29','#9050e9','#e2a400'];
const fmt = n => (n==null?'–':Number(n).toLocaleString());
const e = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const k = M.run;
document.getElementById('kpis').innerHTML = [
 ['Open ecosystem postings', fmt(M.open_total)], ['New this run', fmt(k.new)], ['Closed this run', fmt(k.closed)],
 ['Company feeds', fmt(k.feeds_ok)], ['Weeks of history', M.history_weeks]
].map(([a,b])=>`<div class="kpi"><b>${b}</b><span>${a}</span></div>`).join('');
const ind = M.clouds.filter(c=>c.group==='industries').sort((a,b)=>b.open-a.open);
new Chart(document.getElementById('ind'),{type:'bar',data:{labels:ind.map(c=>c.label),datasets:[{label:'Open',data:ind.map(c=>c.open),backgroundColor:'#0b5cab'}]},options:{indexAxis:'y',plugins:{legend:{display:false}}}});
new Chart(document.getElementById('trend'),{type:'line',data:{labels:M.weeks,datasets:ind.slice(0,8).map((c,i)=>({label:c.label,data:c.series,borderColor:C[i],backgroundColor:C[i],tension:.25,spanGaps:true}))},options:{plugins:{legend:{position:'bottom',labels:{boxWidth:10}}}}});
const al = M.clouds.filter(c=>c.open>0);
new Chart(document.getElementById('all'),{type:'bar',data:{labels:al.map(c=>c.label),datasets:[{label:'Open',data:al.map(c=>c.open),backgroundColor:al.map(c=>c.group==='industries'?'#0b5cab':c.group==='data_ai'?'#7526e3':c.group==='core'?'#2e844a':'#8a94a6')}]},options:{indexAxis:'y',plugins:{legend:{display:false}}}});
const ct = M.countries.slice(0,15);
new Chart(document.getElementById('ctry'),{type:'bar',data:{labels:ct.map(c=>c.country),datasets:[{label:'All Salesforce',data:ct.map(c=>c.open),backgroundColor:'#8a94a6'},{label:'Industries',data:ct.map(c=>c.industries),backgroundColor:'#0b5cab'}]},options:{plugins:{legend:{position:'bottom'}}}});
document.getElementById('cloudtbl').innerHTML = '<tr><th>Cloud</th><th>Open</th><th>New</th><th>Closed</th><th>WoW</th><th>Median days to close</th><th>Top countries</th><th>Top companies</th><th>Roles</th></tr>' +
 M.clouds.filter(c=>c.open||c.closed).map(c=>`<tr><td>${e(c.label)}</td><td class="n">${fmt(c.open)}</td><td class="n">${c.new}</td><td class="n">${c.closed}</td><td>${e(c.wow)}</td><td class="n">${c.median_days_to_close==null?'–':Math.round(c.median_days_to_close)+' (n='+c.ttc_n+')'}</td><td>${e(c.top_countries.map(x=>x[0]+' '+x[1]).join(', '))}</td><td>${e(c.top_companies.slice(0,3).map(x=>x[0]+' '+x[1]).join(', '))}</td><td>${e(c.roles.map(x=>x[0]+' '+x[1]).join(', '))}</td></tr>`).join('');
const pc = document.getElementById('pc'), countries = Object.keys(M.portals_by_country).sort();
pc.innerHTML = countries.map(c=>`<option>${e(c)}</option>`).join('');
function drawP(){const rows=M.portals_by_country[pc.value]||[];document.getElementById('ptbl').innerHTML='<tr><th>#</th><th>Portal</th><th>Salesforce jobs</th><th>Fresh 7d</th><th>Freshness</th><th>Industries index</th></tr>'+rows.map((p,i)=>`<tr><td>${i+1}</td><td>${e(p.portal)}</td><td class="n">${fmt(p.salesforce_jobs)}</td><td class="n">${fmt(p.fresh_7d)}</td><td class="n">${e(p.freshness)}</td><td class="n">${fmt(p.industries_index)}</td></tr>`).join('');}
pc.onchange=drawP; if(countries.length){pc.value=countries.includes('India')?'India':countries[0];drawP();}
const cov=M.coverage;
document.getElementById('health').innerHTML = `<p>Working company feeds: <b>${cov.status.ok||0}</b> (${e(Object.entries(cov.ats).map(([a,b])=>a+' '+b).join(', '))})</p>
<p>On ATS not yet collectable: ${e(Object.entries(cov.unsupported).map(([a,b])=>a+' '+b).join(', '))||'–'} · No careers system detected: ${cov.status.not_found||0}</p>
<p>Failed feeds this run: ${M.failed_feeds.length? e(M.failed_feeds.join(', ')):'none'}</p>
<p>Unreadable portal sites: ${M.portal_health.length? e(M.portal_health.map(p=>p.portal+'/'+p.country+' ('+p.issue+')').join(', ')):'none'}</p>`;
</script></body></html>"""


def build(idx, reg, run, failed_feeds, run_date, week):
    os.makedirs(REPORT_DIR, exist_ok=True)
    m = compute(idx, reg, run, failed_feeds, run_date, week)
    md = to_markdown(m)
    if len(md) > 60000:
        md = md[:60000] + "\n\n_(truncated — see dashboard)_"
    with open(os.path.join(REPORT_DIR, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md)
    data = json.dumps(m, default=str)
    safe_data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = HTML.replace("__WEEK__", week).replace("__DATE__", run_date).replace("__DATA__", safe_data)
    with open(os.path.join(REPORT_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(REPORT_DIR, "data.json"), "w", encoding="utf-8") as f:
        f.write(data)
    log.info("report written to %s", REPORT_DIR)
    return m

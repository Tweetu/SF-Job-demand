# Salesforce Job Demand Tracker

Automatically measures global demand for Salesforce clouds, with a focus on **Industries clouds**.
It runs every Monday on GitHub Actions and emails you a summary. It is free and needs no API keys or paid accounts.

## What it does each week

1. **Discover**: for about 300 seed companies (Salesforce partners and integrators, plus large Industry Cloud customers), it detects which hiring system each one uses. Companies found on job portals are added automatically.
2. **Collect**: it reads public job feeds from Greenhouse, Lever, Ashby, SmartRecruiters and Workday.
3. **Classify**: it tags each posting with Salesforce clouds and roles (`config/taxonomy.yaml`). Non-ecosystem jobs, such as "sales rep using Salesforce", are filtered out.
4. **Track history**: it records first seen, last seen and closed dates for every posting. From these it works out new and closed postings, trends, days-to-close and reposts. If a feed fails one week, its postings are **not** wrongly marked as closed.
5. **Portals**: it checks 22 job portals across 24 countries with a headless browser and ranks the portals per country.
6. **Report**: it opens a GitHub issue with the weekly summary, and GitHub emails it to you. It also saves an interactive `reports/dashboard.html`.

LinkedIn, Indeed, Glassdoor and Upwork are excluded on purpose: their terms forbid scraping and they block cloud servers. Most of their Salesforce jobs come from company career sites, which this tool reads directly.

## Test first — easiest: on GitHub (recommended)

After uploading (setup steps below), go to **Actions → "Self-test (safe, saves nothing)" → Run workflow**.
It runs in a throwaway GitHub machine, nothing touches your laptop, and the PASS/FAIL results appear on the run's summary page (~10 min).
Then run the weekly workflow.

## Test locally on Windows (optional)

**Smart App Control blocks the .bat files?** That's expected for downloaded scripts; don't turn Smart App Control off.
Either right-click the **zip → Properties → tick "Unblock" → OK** *before* extracting, or skip the .bat files and type the
commands below in Command Prompt. On Windows the tool uses the built-in Microsoft Edge (no extra browser download).
If Smart App Control still blocks a Python library file (`.pyd`), use the GitHub self-test above or WSL instead.

Manual commands (Command Prompt, inside the unzipped folder):
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python -m sfjobs.selftest
```

Using the .bat files instead:

1. Install Python 3.12 from python.org and tick **"Add python.exe to PATH"**.
2. Double-click **`setup_local.bat`**. Takes about 5 min, once.
3. Double-click **`selftest.bat`**. Takes about 5–10 min and saves nothing. It checks internet access, the classifier, Workday, company detection for 8 companies, and a few portals in 5 countries.
4. (Optional) Double-click **`run_local_small.bat`** for a real mini-run: 25 companies plus 10 minutes of portals. It opens the dashboard and prints the weekly summary.
5. Double-click **`reset_data.bat`** before uploading to GitHub, so test data doesn't become week 1.

Some portal FAILs are normal. If checks 1–4 fail, send the output to Claude.
On an office network or VPN, use home Wi-Fi if the internet check fails.

## One-time setup (about 30 minutes)

1. **Create the repo.** On github.com, click **New repository**, name it `sf-job-demand`, choose **Private**, and leave "Add README" unticked. Click **Create**.
2. **Upload the code** (do not upload the `.venv` folder if you ran the local test). Use either option:
   - **Git** (in Command Prompt, inside the unzipped folder):
     ```
     git init
     git add .
     git commit -m "Initial commit"
     git branch -M main
     git remote add origin https://github.com/<your-username>/sf-job-demand.git
     git push -u origin main
     ```
   - **Browser**: on the empty repo page, click **uploading an existing file** and drag in *everything inside* the unzipped folder, including the `.github` folder. Then click Commit.
     Afterwards, check that `.github/workflows/weekly.yml` appears in the repo.
3. **Allow the workflow to save data.** Go to repo **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and click Save.
4. **First run.** Go to the **Actions** tab. If prompted, enable workflows. Select **Weekly Salesforce job demand → Run workflow**.
   The first run takes about 2–3 hours because it detects every company's hiring system. Later runs take about 1–1.5 hours.
5. **Email.** You're automatically watching your own repo, so each weekly issue arrives by email.
   To check, look at the **Watch** button: it should say "All Activity" or "Participating and @mentions" with Issues on.

After that, nothing more is needed. It runs every Monday at 06:47 IST.

## Reading the results

- **Weekly email / issue**: Industries cloud table (open, new, closed, week-over-week change, trend, median days to close, top countries), top countries, who is hiring, best portals per country, and health.
- **Dashboard**: open `reports/dashboard.html` in the repo, click **Download raw file**, and open it in your browser.
- **Raw data**: `data/metrics/*.csv` (weekly history) and `data/state/postings_index.csv.gz` (every posting).

How to read the numbers:
- **Open counts** = current demand. These are reliable from week 1.
- **Trends** become meaningful after about 4 weeks. **Days-to-close** becomes meaningful after about 6–8 weeks.
- **Portal counts** are search-result counts. Use them to *compare portals*, not as exact demand.
- **Evergreen share** = postings open for more than 90 days or reposted. Many consulting firms post always-open pipeline roles.

## When something breaks (about once a month or less)

- **A whole run fails**: GitHub emails you "Run failed". Open the run, copy the red error lines, and paste them to Claude.
- **"Feeds that failed" or "Portal sites not readable" in the summary**: no action needed if it happens once, because these are re-detected automatically. If the same portal is unreadable for 3 or more weeks, paste its name to Claude, or delete it from `config/portals.yaml`.

## Customising (optional)

| To… | Edit |
|---|---|
| Add companies | `config/seed_companies.csv`: name, domain, category. Leave `ats` blank and it gets auto-detected. |
| Pin a company's hiring system | Fill `ats` + `ats_token`. For Workday the token is `tenant|wdN|SiteName`, taken from the careers URL `https://tenant.wdN.myworkdayjobs.com/SiteName`. |
| Add keywords or clouds | `config/taxonomy.yaml` |
| Add a job portal | `config/portals.yaml` (copy an existing entry) |
| Change the schedule | `.github/workflows/weekly.yml`, the `cron` line (time is in UTC) |
| Run company feeds only | Actions → Run workflow → tick "Skip job-portal counts" |

## Limits

- Coverage = companies on supported hiring systems (Workday, Greenhouse, Lever, Ashby, SmartRecruiters). Companies on SuccessFactors, Taleo, iCIMS and similar are listed in the health section as "not yet collectable".
- China, Japan and Korea portal coverage is thin.
- Hiring speed is estimated from how long postings stay up. Real hire dates aren't public.
- Free private-repo quota is 2,000 Actions minutes a month. Weekly runs use about 300–500.

## Security & compliance

**On your laptop**
- It runs as your normal user. It needs no admin rights, passwords, API keys or logins. Everything stays inside this folder and `.venv`.
- The headless browser uses a fresh, empty profile. It never touches your Chrome, cookies or saved logins, and downloads and service workers are disabled.
- **Network guard:** every request, including redirects and anything a portal page tries to load, is blocked unless it goes to a public internet host. Data scraped from the web can't make it contact your router, office network, localhost or cloud metadata addresses.
- Responses are capped at 5 MB. YAML is loaded with `safe_load`. No scraped content is ever executed.
- Library versions are pinned exactly in `requirements.txt`. Playwright downloads Chromium from Microsoft's official CDN.

**Untrusted web content**
- Company names found on portals are cleaned: markup, links and @mentions are stripped, and length is capped.
- The dashboard escapes all text and has a strict Content-Security-Policy. The weekly summary escapes Markdown.
- CSV cells starting with `= + - @` get a leading `'`, so a malicious job title can't run a formula in Excel.

**On GitHub**
- The repo is private, there are no stored secrets, and the built-in token is limited to this repo (contents + issues). It expires after each run.
- The workflow only runs on its schedule or when you run it by hand. Pull requests and outside input can't trigger it.
- Only official GitHub actions are used (`actions/checkout`, `setup-python`, `upload-artifact`).
- Recommended: turn on 2FA for your GitHub account, and enable **Settings → Code security → Dependabot alerts** (free, alerts only).

**Being a polite crawler**
- `robots.txt` is respected for careers pages and job portals. Disallowed pages are skipped and shown as `robots_disallowed` in the report. To turn this off, set `RESPECT_ROBOTS=0`, but keeping it on is recommended.
- Requests to each host are throttled, with random delays on portals. LinkedIn, Indeed, Glassdoor and Upwork are excluded.
- Only job metadata is stored: title, company, location, link and dates. Job descriptions are read in memory for tagging and then discarded, so no recruiter names or emails are kept.

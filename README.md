# JSDO Weekly Social Media Reporting

Automated weekly reporting pipeline and interactive Arabic dashboard for the
**Jouf Strategic Development Office** (المكتب الاستراتيجي لتطوير منطقة الجوف),
benchmarked against nine peer regional development authorities.

The pipeline collects follower counts, regional news, and public comments;
classifies comment sentiment with an LLM tuned for Saudi dialect and sarcasm;
stores everything in SQLite (optionally mirrored to Google Sheets); and emails
a weekly summary linking to a live RTL dashboard.

No social media API keys, OAuth flows, admin logins, or account passwords are
used anywhere. Followers and comments come from public web scraping via Apify.

---

## Quick start

```powershell
# 1. Install
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install -e .

# 2. Configure (see "Environment variables" below)
Copy-Item .env.example .env
notepad .env

# 3. Check what is wired up
python -m aljouf_report doctor

# 4. See the dashboard immediately with synthetic data (no API keys needed)
python -m aljouf_report seed-demo
python -m aljouf_report dashboard      # http://localhost:8501
```

On macOS/Linux use `.venv/bin/pip` and `source .venv/bin/activate`.

### Running for real

```powershell
python -m aljouf_report run
```

The pipeline is **fail-soft**: every stage records its problems into the run
summary instead of aborting. A missing `APIFY_TOKEN` still produces a news
report; a broken scraper still produces follower data for the other platforms.
Warnings appear in the CLI output, the weekly email, and `logs/pipeline.log`.

---

## What gets collected

| Stage | Source | Needs a key? | Window |
|---|---|---|---|
| Follower counts | Apify actors (public profiles) | `APIFY_TOKEN` | current snapshot |
| News & trends | Google News RSS + GDELT 2.0 | no | 14 days |
| Comments | Apify actors (public posts/replies) | `APIFY_TOKEN` | 7 days |
| Sentiment | OpenAI | `OPENAI_API_KEY` | 7 days |

Windows, keywords, and platform lists are all set in `config/settings.yaml`.

### Tracked accounts

`config/accounts.yaml` holds all ten entities, transcribed from
*Benchmarked Entities.docx*. One is marked `owned: true` (ours); the rest are
benchmarks. Platforms the document marked `--` are simply omitted, and the
pipeline skips them rather than reporting them as failures.

| Entity | LinkedIn | Instagram | YouTube | X |
|---|---|---|---|---|
| **الجوف (ours)** | ✅ | ✅ | ✅ | ✅ |
| مكة المكرمة | ✅ | ✅ | ✅ | ✅ |
| المدينة المنورة | ✅ | ✅ | ✅ | ✅ |
| المنطقة الشرقية | ✅ | ✅ | ✅ | ✅ |
| عسير | ✅ | ✅ | ✅ | ✅ |
| حائل | ✅ | ✅ | ✅ | ✅ |
| الأحساء | ✅ | ✅ | — | ✅ |
| الباحة | ✅ | — | — | ✅ |
| جازان | ✅ | — | ✅ | ✅ |
| الحدود الشمالية | ✅ | ✅ | — | ✅ |

To re-import after the source document changes:

```powershell
python tools\read_docx.py "path\to\Benchmarked Entities.docx" dump.json
```

Then update `config/accounts.yaml` from the dump.

---

## Environment variables

Copy `.env.example` to `.env`. Everything is optional — the pipeline degrades
rather than failing — but each missing key removes a feature.

### Apify — followers and comments

| Variable | Purpose |
|---|---|
| `APIFY_TOKEN` | Runs the public-web scrapers. [Get one here](https://console.apify.com/account/integrations). |

This is how the project satisfies the "no official APIs, no admin login"
constraint: Apify actors read public profile and post pages, so there is no
OAuth consent screen and no credential for any social account.

### OpenAI — sentiment

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for sentiment classification. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Use `gpt-4o` for the hardest sarcasm cases. |

### Google Sheets mirror (optional)

SQLite is always written; Sheets is a convenience mirror.

| Variable | Purpose |
|---|---|
| `GOOGLE_SHEETS_ID` | The ID from the spreadsheet URL. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to the service-account JSON key. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The whole key inline, for CI secrets. |

Setup: create a service account in Google Cloud, enable the Sheets API,
download the JSON key, then **share the spreadsheet with the service account's
`client_email` as Editor**. That last step is the one people miss.

Tabs written: `Followers`, `News`, `Comments`, `Runs`.

### Email (optional)

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | Port 465 switches to implicit SSL. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | — | For Gmail use an **App Password**. |
| `SMTP_USE_TLS` | `true` | STARTTLS on non-465 ports. |
| `EMAIL_FROM` | SMTP username | Display sender. |
| `EMAIL_RECIPIENTS` | from `settings.yaml` | Comma-separated override. |
| `DASHBOARD_URL` | `http://localhost:8501` | The link in the email. |

Default recipients (from the source document) are in
`config/settings.yaml`: `a.shuqair@jsdo.gov.sa`, `exc.social@jsdo.gov.sa`,
`laith.louid@jo.ey.com`.

Preview the email without sending:

```powershell
python -m aljouf_report send-email --preview   # writes logs\email_preview.html
```

### Misc

| Variable | Default |
|---|---|
| `LOG_LEVEL` | `INFO` |
| `SQLITE_PATH` | `data/aljouf.db` |

---

## CLI

```
python -m aljouf_report doctor         # which integrations are configured
python -m aljouf_report run            # the full weekly pipeline
python -m aljouf_report init-db        # create the schema only
python -m aljouf_report seed-demo      # synthetic data, no keys needed
python -m aljouf_report dashboard      # launch Streamlit
python -m aljouf_report send-email     # re-send or --preview the last run
```

`run` accepts `--skip-followers`, `--skip-news`, `--skip-comments`,
`--skip-sentiment`, `--skip-sheets`, and `--skip-email`, which is useful for
testing one stage at a time:

```powershell
# News only: needs no credentials at all
python -m aljouf_report run --skip-followers --skip-comments --skip-sentiment --skip-email
```

`seed-demo --reset` wipes the database first; `--weeks N` controls how much
history to generate (default 8, which gives the growth chart something to show).

---

## Dashboard

```powershell
python -m aljouf_report dashboard --port 8501
# or: streamlit run dashboard/app.py
```

Four tabs, all right-to-left:

- **نمو المتابعين** — our follower growth vs. competitors over time, with our
  line emphasised; a grouped per-platform bar chart; a detail table; and a list
  of accounts that failed to collect, with the reason.
- **تحليل المشاعر** — a donut of Positive/Negative/Neutral, a stacked bar by
  platform, and a filterable table of every scored comment with its reason.
- **الأخبار والرصد** — filterable news table (keyword, language, title search)
  plus counts by keyword and top publishing domains.
- **مراجعة التعليقات السلبية** — negative comments with their Arabic reasons,
  as a sortable table and as review cards, with CSV export.

RTL is handled in `dashboard/theme.py`: Streamlit renders LTR by default, so
widget labels, dataframe cells, tabs, metrics, and the sidebar each need
explicit flipping, and Plotly legends and axes are configured separately.
`.streamlit/config.toml` pins a light theme because the custom card styling is
designed for light surfaces.

### Deploying the dashboard

For a shareable `DASHBOARD_URL`, the simplest options are Streamlit Community
Cloud (point it at `dashboard/app.py`) or any VM running:

```bash
streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0
```

The dashboard only reads SQLite, so it can run on the same host as the pipeline.

---

## Scheduling

### Windows (local)

```powershell
# From an elevated PowerShell prompt
.\deploy\register_windows_task.ps1                  # Sundays at 09:00
.\deploy\register_windows_task.ps1 -Time 07:30 -DayOfWeek Monday
.\deploy\register_windows_task.ps1 -Semimonthly     # 1st and 15th at 09:00
```

The `-Semimonthly` switch matches the cadence in the source document
(*كل 1 و15 من كل شهر الساعة 9 صباحاً*). Verify with:

```powershell
Start-ScheduledTask -TaskName 'JSDO Weekly Social Report'
Get-ScheduledTaskInfo -TaskName 'JSDO Weekly Social Report'
```

### GitHub Actions (hosted)

`.github/workflows/weekly-report.yml` runs Sundays at 06:00 UTC (09:00 Riyadh)
and can be triggered manually. Add `APIFY_TOKEN`, `OPENAI_API_KEY`,
`GOOGLE_SHEETS_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and the `SMTP_*` values as
repository secrets, and `EMAIL_RECIPIENTS` / `DASHBOARD_URL` as variables.
The commented-out second cron adds the 1st/15th cadence.

---

## Architecture

```
config/
  accounts.yaml          Entities and handles (from Benchmarked Entities.docx)
  settings.yaml          Windows, keywords, Apify actor IDs, recipients
src/aljouf_report/
  config.py              YAML + .env loading into typed models
  models.py              Normalised records and dedupe hashes
  pipeline.py            Stage orchestration; fail-soft; report helpers
  cli.py                 Typer commands
  demo.py                Synthetic data generator
  collectors/
    apify.py             Actor runner with retries and run_safe()
    extract.py           Schema-drift-tolerant field extraction
    followers.py         Batched follower collection per platform
    comments.py          Two-stage posts-then-comments collection
    news.py              Google News RSS + GDELT, rate-limited
  analysis/
    prompts.py           Saudi-dialect system prompt + few-shot examples
    sentiment.py         Batched classification, validated against a schema
  storage/
    schema.sql           Tables, indexes, and the scored-comments view
    database.py          Repository with upsert semantics
    sheets.py            Google Sheets mirror
  notify/mailer.py       RTL HTML email
dashboard/
  app.py                 Streamlit app
  theme.py               RTL CSS and Plotly layout
deploy/                  Windows Task Scheduler registration + run wrapper
tests/                   52 tests
```

### Three design decisions worth knowing

**Scraper output is treated as untrusted and unstable.** Apify actors are
community-maintained, and a follower count may arrive as `followersCount`,
`followers`, `follower_count`, `stats.followerCount`, or
`legacy.followers_count` depending on the actor and its version. Rather than
hard-coding one shape, `collectors/extract.py` tries a list of likely paths and
then falls back to a recursive search for follower-shaped keys. This is why all
actor IDs in `settings.yaml` are swappable without code changes — pick a
different actor from the Apify Store and the normaliser usually still works.

**Sentiment results are cached by content hash.** `sentiments` is keyed on
`comment_hash`, not on a row id, so re-running the pipeline only pays the LLM
for comments it has never scored. Re-runs within a week are nearly free.

**Failed runs can't corrupt the report.** A run where collection failed writes
rows with `NULL` follower counts. The follower KPI and the email's
week-over-week delta both skip runs with no counts, so an outage shows up as a
warning rather than as a sudden drop to zero.

### Sentiment prompt

`analysis/prompts.py` targets three things generic Arabic sentiment models get
wrong on Gulf government accounts:

1. **Dialect and slang** — Najdi/Hijazi vocabulary (`وش`, `هبال`, `فشلة`,
   `جبتوها`) that is rare in MSA training data.
2. **Sarcasm** — praise phrasing that carries criticism is extremely common in
   replies to government accounts. *"يا سلام عليكم 👏👏 نفس الكلام من ثلاث
   سنوات"* reads as Positive literally and is scored **Negative** here.
3. **Prayer and set phrases** — `الله يعطيكم العافية` is genuine praise while
   `الله يصلح الحال` is a complaint, though both look religious and positive.

Output per comment: a label (`Positive`/`Negative`/`Neutral`) and a one-sentence
Arabic reason. OpenAI structured outputs guarantee a parseable, closed label
set; results whose ids don't match the batch are discarded rather than risk
mislabelling a different comment.

---

## Data model

```
runs                 one row per pipeline execution
follower_snapshots   unique (run_id, entity_key, platform) — the growth series
news_articles        unique (url_hash) — dedupes across keywords and sources
comments             unique (comment_hash) — dedupes across runs
sentiments           keyed by comment_hash — doubles as the LLM cache
v_comments_scored    view joining comments to sentiments
```

Query it directly:

```sql
-- Our follower growth by platform
SELECT r.started_at, f.platform, f.followers
FROM follower_snapshots f JOIN runs r ON r.id = f.run_id
WHERE f.owned = 1 AND f.followers IS NOT NULL
ORDER BY r.started_at;

-- This week's negative comments
SELECT published_at, platform, text, reason_ar
FROM v_comments_scored
WHERE label = 'Negative' AND published_at >= datetime('now', '-7 days')
ORDER BY published_at DESC;
```

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

52 tests covering follower-count parsing across actor schema variants and
abbreviated forms (`1.2M`), timestamp parsing across scraper formats, SQLite
upsert and dedupe behaviour, the sentiment cache, malformed and hallucinated LLM
output, and the failed-run reporting edge case.

---

## Troubleshooting

**Arabic shows as `?????` in the terminal.** Set `PYTHONUTF8=1` (the
`deploy/run_weekly.ps1` wrapper does this automatically).

**GDELT reports `429 Too Many Requests`.** GDELT throttles anonymous clients to
roughly one query every five seconds. The collector paces and retries
automatically; an occasional give-up is logged as a warning and Google News
already covers the same keywords. To skip GDELT entirely, remove it from
`news.sources` in `config/settings.yaml`.

**Follower counts are all empty.** Run `python -m aljouf_report doctor`. If
`APIFY_TOKEN` is set, check the per-account reasons in the dashboard's
"حسابات لم يتم جمع بياناتها" panel — an actor may have been renamed or may need
a paid Apify plan. Actor IDs are swappable in `config/settings.yaml`.

**Sheets sync is silently skipped.** It needs both `GOOGLE_SHEETS_ID` and
resolvable credentials, and the spreadsheet must be shared with the service
account's `client_email` as Editor.

**Gmail rejects the SMTP login.** Use an App Password, not the account
password, and keep 2FA enabled on the account.

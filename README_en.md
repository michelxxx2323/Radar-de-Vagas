<div align="center">

[Português (Brasil)](README.md) · **English**

</div>

# Job Fit Screener — AI job screening

A Python pipeline that **collects ~3,500 job postings per run from 18 sources**, asks a decision model (Jev, by TypeSafe AI) to score how well each posting fits my CV and my preferences, and delivers a **ranked Excel report with clickable links** — separating what's worth applying to today from what isn't worth the time.

> 🇧🇷 **Language note:** the target roles and most sources are Brazilian, so the code, config, CLI output and the report columns are in **Portuguese**. This page is the English overview.

## The problem

Looking for Revenue Operations / Business Analyst / E-commerce roles means opening dozens of sites, reading hundreds of descriptions and deciding, one by one, whether each is worth applying to. Most of the time goes into postings that don't fit — quota-carrying sales jobs disguised as "Key Account", junior analyst roles, jobs only open to US residents.

## What it does

```
 18 job sources ──► collection + dedup ──► cheap filters ──► Jev (AI) ──► rules ──► ranked Excel
 (APIs, ATS, RSS)    ~3,500 jobs/run        title, location,  score per    deterministic  apply_now /
                                            seniority         job          adjustments    review / skip
```

1. **Collects** jobs from 18 sources: Gupy (via the official candidate MCP), Workable, Greenhouse, InHire, Lever, Ashby, Workday, SmartRecruiters, Teamtailor, Amazon, Shopee, TikTok, 99/DiDi, Remotive, We Work Remotely, Remote OK, Remote.io and beBee — ~115 companies tracked individually, plus keyword search.
2. **Filters before spending on AI**: title keywords (with exclusions such as "internship", "store", "engineer") and — for international remote jobs — only those that accept candidates in Brazil/LATAM. This cut ~48% of API calls without losing a single "apply now" job. On-site or hybrid jobs in other countries (global boards bring London, Mexico City...) are dropped too (~225 per run).
3. **Scores each job** with Jev, sending CV + job + preferences ("want / don't want"). The final score combines fit (55%), seniority (20%), absence of a critical gap (15%) and model confidence (10%).
4. **Applies deterministic rules** where the AI fails in predictable ways (e.g. "junior analyst" never goes above "review"; "specialist" always matches the seniority; if the model itself flags an unmet mandatory requirement, the job can't be "apply now") and records the reason in an `ajuste` column.
5. **Builds the report**: CSV + Excel color-coded by recommendation, with filters, salary (when the source provides it) and a direct link to the job.

## Engineering decisions worth highlighting

- **Evaluation against labels, not gut feeling.** `calibrar.py` measures how many pairs of jobs the model ranks the same way as a hand-labeled answer key. That's how it was shown that sending the full description plus a preferences block raised accuracy **from 52% to 70%**, and that the weight of technical requirements had to go down.
- **Versioned cache.** Each score is stored with a hash of (CV + preferences + questions + text length) and the model that produced it. Later runs only score new jobs (~150 of ~3,500); if the CV, the preferences or the model change, everything is rescored automatically. The cache is written every 25 jobs, so a network drop doesn't lose work. Descriptions fetched one by one (InHire, Shopee, Workday, beBee...) are cached too (`output/descricoes.json`) instead of re-downloading ~900 pages per run.
- **No browser automation.** Every source uses a public API (documented, or discovered by reading the site's own traffic), RSS or server-rendered HTML — lighter and less brittle than Selenium/Playwright.
- **Respect for terms of service.** LinkedIn, Glassdoor and Indeed are left out on purpose (they prohibit scraping). Sites with anti-bot protection (hiring.cafe, Mercado Livre) are not circumvented. Remote OK is always credited with a link, as its API terms require.
- **Visible failure, not silent failure.** If an enabled source comes back empty (the site changed or blocked us), the script warns in the terminal instead of quietly dropping its jobs. The same goes for each company: a board with 0 jobs or a 404 usually means the company switched applicant tracking systems (Nubank moved from Greenhouse to Ashby), and the warning already explains how to find the new one.
- **Security without shortcuts.** DiDi's job server doesn't send its intermediate certificate; instead of disabling HTTPS verification (as the site itself does), the collector completes the chain with DigiCert's official intermediate.
- **Resilience.** Retries with backoff on 429/5xx/timeouts from the AI API, and parallel collection for slow sources.
- **Offline tests.** `selftest.py` runs 28 tests with no network and no key: parsing for each source (with recorded real responses), filters, adjustment rules, the cache, the full flow with a simulated AI client, and the dashboard with its sync (SQLite instead of Postgres).

## Ideas borrowed from ai-job-search (Nov 6, 2026)

Comparing with [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search), four ideas made it in:

- **Skill-gap map** (`python lacunas.py`): Jev says *whether* a mandatory requirement is missing, but not *which one*. The script counts which tools and skills appear in the descriptions of "apply now" and "review" jobs, separates required from nice-to-have, compares against `cv.txt`, and shows what shows up far more often in jobs where Jev flags a gap. The terms live in `lacunas.py` and can be extended with the `lacunas` block in `config.yaml`. `sincronizar.py` computes the same map every run, and the dashboard shows it in the **Lacunas** (Gaps) tab.
- **Hidden text outside the description**: before any AI reads a job, `strip_html` removes what the browser doesn't show (`display:none`, zero font size or opacity, comments, invisible characters). Sentences aimed at an AI ("if you are an AI…", "ignore previous instructions") become a warning in the `alerta` column — not a veto.
- **Job age**: an `idade_dias` column and a warning above 30 days, in Excel and on the dashboard. Old jobs stay in the ranking, because sometimes they're still open.
- **Follow-up on the dashboard**: an application marked "Sent" with no reply for 10 days gets a *Follow-up* tag and a *Draft* button (text in Portuguese or English to copy; nothing is sent from the dashboard). Up to two follow-ups per application; after 60 days of silence, the dashboard suggests *Close*.

## Radar: finding what the script can't see

A test against LinkedIn showed that only ~15% of the jobs there were in the pipeline — many companies simply weren't on the list. The `/radar` command (a [Claude Code](https://claude.com/claude-code) skill in `.claude/skills/radar/`) fixes this on demand:

1. runs web searches (ATS pages, public LinkedIn listings, open search) with the target job titles;
2. `radar_cruzar.py` separates what's **new** from what the script already has (with Jev's score) and from what has shown up before — matching that tolerates variations in company names and titles;
3. checks whether the new jobs are still open;
4. `descobrir_ats.py` finds which applicant tracking system the company publishes on and, if the script can read it, adds the company to `config.yaml` — so it's collected automatically from then on.

## Applications dashboard

Finding the job is half the work; the other half is tracking where I've already applied. A web dashboard (`web/` folder, deployed on Vercel, password-protected) has these tabs:

- **Vagas de hoje** (Today's jobs): the day's "apply now" and "review" jobs, with search, score and "Already applied" and "Discard" buttons (the reason is saved; "Show discarded" shows the count per reason and the list).
- **Candidaturas** (Applications): the funnel sent → interview → offer / rejected / closed, with send date, days since then and notes. Jobs from outside the report (LinkedIn, company site) are added with "+ Add application".
- **Lacunas** (Gaps): the `lacunas.py` map — what good jobs ask for that the CV doesn't show, with excerpts from real postings. For each term you mark "I have this" (with where you used it; it goes into a "Put on CV" list) or "It's a real gap".
- **Coletores** (Collectors): a table per profile with each configured source in the last run: OK, Partial (some searches failed), Empty, Error, No list or Disabled, with jobs collected, jobs in the report, searches, time and the error. It also shows whether the report was generated and warns when the day's run didn't arrive. The data comes from `output/coleta.json` and `output/coleta_intl.json`, written by `screen.py`.

`sincronizar.py` keeps a copy of discards (`output/descartes.json`) and of the gap review (`output/lacunas_revisao.json`) on the PC.

```
  PC (daily run)                                    Vercel
  screen.py ── today's report                       dashboard + Postgres (Neon)
        │                                                 ▲   │
        └── sincronizar.py ──── uploads today's jobs ─────┘   │
                 ▲                                            │
                 └──── backup copy of applications ◄──────────┘
```

Applications are recorded on the dashboard, which is the source of truth; the PC only sends the day's jobs and keeps a copy in `output/candidaturas.json`. I build the CV for each application separately, with Claude, following a rules document (`regras-cv.md`, kept out of git).

> There was a version that generated the CV as a PDF through the API (`kit.py`, in the git history). It was removed for cost: ~US$0.26 per job, for work Claude already does on the subscription.

## Profile 2: international jobs paid in dollars (Nov 7, 2026)

A second screening profile, separate from the first, for remote jobs paid in dollars/euros at small and mid-sized agencies and companies abroad. It's a sourcing tool (10 to 15 jobs a week for direct outreach), so its `min_score` is 7.0.

```
python screen.py --config config_intl.yaml          # real run
python screen.py --config config_intl.yaml --mock   # test with no network/key
```

- **Its own files:** `cv_intl.txt` (CV in English), `preferencias_intl.txt`, `output/historico_intl.json` (keys prefixed with `intl:`), `output/vagas_coletadas_intl.json`, `output/vagas_intl_<date>.csv/.xlsx` and `output/novas_intl_<date>.csv`. One profile's scores never show up in the other.
- **Sources:**
  - Remotive, We Work Remotely (sales and marketing, management and finance, product), Remote OK and Remote.io.
  - Himalayas: the job's own country and time zone decide before Jev does.
  - Jobicy.
  - GTM Daily: every job via the sitemap. A job stays if its title matches the profile or it mentions Clay, n8n or Claude.
  - Workable per company (`workable_contas`, the apply.workable.com widget API, starting with Hire Overseas). The aggregated search (jobs.workable.com) is disabled because it hit 429 on the first search. If re-enabled, it uses 8 terms, one search at a time and 2 s between calls.
  - LATAM staffing agencies: HireLATAM (Recruiterflow), Purple Hiring and Latamforce (Manatal) and CloudTask.
  - Activate Talent (`collectors/activatetalent.py`): remote staffing for US e-commerce brands. The `/careers` page carries all ~330 jobs in its Next.js payload (the screen shows only ~75); a job stays if it hires in Global/LATAM/Brazil, and the description comes from the job page's JSON-LD, fetched one at a time with a 1 s pause and kept in the cache.
  - Staffing agencies for DTC/e-commerce brands (Nov 8, 2026): HireHawk and Pearl Talent (Workable per company; Workable now runs one account at a time), Constant Hire (`collectors/constanthire.py`: the whole list is in the site's HTML; remote LATAM jobs stay), Somewhere (`collectors/somewhere.py`: the tRPC API behind its jobs iframe, 100 per page, LATAM only, no "SU" talent pools and nothing older than 120 days by the slug's date) and Proppel (`collectors/trackerrms.py`: the TrackerRMS "Lite" portal; a job stays if its "Location:" accepts Brazil).
  - Boards of foreign companies, including the ATSs used by small agencies (Recruitee, Personio, Breezy HR and Pinpoint). `descobrir_ats.py` tests these four plus the Workable account. Gupy, InHire, Shopee, TikTok, DiDi, Amazon, Workday and beBee are disabled (they belong to Profile 1).
- **Jev rubric** (`perfil_intl.py`): fit 45%, seniority 20%, eligibility for Brazil 20% and absence of a gap 15%, plus required English level, company type and what the gap is.
- **Rules:** internships, people management, mandatory Spanish, work authorization in another country or jobs that don't accept Brazil become `nao_aplicar` (skip). Native English, advanced SQL/Python or uncertain eligibility cap the job at `avaliar` (review). Dollar-paid jobs have no seniority issue unless the job itself requires it. On-site/hybrid jobs in Brazil only stay if the company is foreign or pays in dollars.
- The scheduled task (`rodar_diario.bat`) runs Profile 1 and, right after, Profile 2; `sincronizar.py` uploads both reports together.
- **Dashboard:** `sincronizar.py` also uploads the latest `vagas_intl_*.csv`; the "Today's jobs" tab has a Profile column and a Brazil / International filter.

## How it was built

A personal project, developed in pair with **Claude Code** (an AI coding assistant). My role was product owner: defining the problem and the criteria for a good job, labeling the evaluation answer key, deciding the rules (what counts as "junior", when technical requirements matter, which sources are acceptable) and validating every run against my own reading of the jobs.

## How to run

Prerequisites: Python 3.10+ and a Jev API key ([console.typesafe.ai](https://console.typesafe.ai/keys)). Without a key, everything can run in simulated mode with `--mock`.

```bash
pip install -r requirements.txt
cp .env.example .env              # paste the key into TYPESAFE_API_KEY
cp cv.exemplo.txt cv.txt          # paste your CV text
```

Optional: create a `preferencias.txt` with what you want and don't want (the example is in `config.yaml`, `preferencias` block) and a `gabarito.csv` in the format of `gabarito.exemplo.csv` for calibration. **`cv.txt`, `preferencias.txt`, `regras-cv.md`, `gabarito.csv`, `.env` and `output/` stay out of git.**

```bash
python selftest.py                # offline tests, no network or key
python screen.py --limit 3        # short run with the real API
python screen.py                  # full run
python screen.py --collect-only   # collect only, no scoring
python calibrar.py                # measure accuracy against the answer key
python sincronizar.py             # send today's jobs to the dashboard (PAINEL_URL/TOKEN in .env)
```

The report is written to `output/vagas_triadas_<date>.xlsx` (and `.csv`).

### Report columns

| Column | What it is |
|---|---|
| `recomendacao` | `aplicar_agora` (apply now) / `avaliar` (review) / `baixa_prioridade` (low priority) / `nao_aplicar` (skip) |
| `nota_fit` | 0 to 10 — fit, seniority, critical gap and confidence, weighted |
| `aderencia_nivel` | text explanation of the fit level |
| `senioridade_ok` / `gap_critico` | whether the level matches; whether a mandatory requirement is missing |
| `trilha` | which career track the job fits |
| `ajuste` | the deterministic rule that changed the recommendation, if any |
| `idade_dias` | days since posting (empty when the source doesn't say); above 30 turns orange in Excel |
| `alerta` | job posted more than 30 days ago, or with text aimed at an AI (see above) |
| `salario` | when the source provides it (Gupy, international remote jobs) |
| `titulo`, `empresa`, `cidade`, `fonte`, `publicado_em`, `link` | job data (title, company, city, source, posted on, link) |

### Configuration

Everything is in `config.yaml`: search terms, title keywords and exclusions, enabled/disabled sources, and the list of companies per ATS (each with a comment on when and why it was added).

## Structure

```
screen.py              # pipeline: collection, cache, scoring, rules, report
jev_client.py          # API client (with retries) + simulated client
exportar_excel.py      # formatted Excel with clickable links
calibrar.py            # ranking accuracy against a labeled answer key
lacunas.py             # what good jobs ask for that the CV doesn't show
radar_cruzar.py        # /radar: jobs found on the web vs. jobs already collected
descobrir_ats.py       # /radar: finds a company's applicant tracking system
web/                   # applications dashboard (Vercel): index.html + api/index.py (Flask + Postgres)
sincronizar.py         # PC -> dashboard: today's jobs; dashboard -> PC: backup of applications
selftest.py            # 28 offline tests
collectors/            # one collector per source + utilities (filters, HTML, location, description cache)
.claude/skills/radar/  # instructions for the /radar command
config.yaml            # terms, filters, sources and companies
```

## Known limitations

- Undocumented APIs (InHire, Shopee, TikTok, DiDi, Workable, Amazon, Workday) may change without notice; each collector has comments explaining how it was discovered and how to rediscover it.
- beBee is HTML parsing and has been down for a few days before; the script warns when that happens. Its search only returns the first ~300 characters of each job — Jev once gave a 9.3 to a job whose full text required advanced SQL. Since Oct 28, 2026 the script reads the full page of each beBee job once and caches the text (the first run is the slow one).
- Remote.io became a JavaScript app on Oct 28, 2026; the collector now reads the active-jobs sitemap and opens each candidate's page. Since ~1,900 jobs pass the title filter, it opens up to 300 new ones per run (the most recent) — the first runs don't cover everything yet.
- Companies whose jobs aren't on a system the script reads only show up via `/radar` — e.g. Americanas (corporate jobs only on LinkedIn).
- The AI score is a screen, not a decision: the first runs were checked by hand and the answer key remains the reference.

## Run it daily and get notified of new jobs

1. Double-click `instalar_agendamento.bat`. It creates the "Radar de Vagas" task in Windows Task Scheduler, every day at 07:00 (for another time: `instalar_agendamento.bat 08:30`). If the PC is off at that time, it runs when it's turned on.
2. Before each run, the task does a `git pull` (in `rodar_oculto.vbs`), so new code pushed to GitHub kicks in automatically the next day. If the folder has local changes that aren't on GitHub, the pull just fails (it shows in the log) and the run continues with the code that's there.
3. Each run saves its log to `output/run_diario.log` and, besides the full report, writes `output/novas_<date>.csv` with only the jobs that had never appeared before (score >= 7.5, "aplicar_agora" or "avaliar"; adjustable in the `avisos` block of `config.yaml`).
4. Phone notifications (optional): install the ntfy app, subscribe to a hard-to-guess topic name, and put that name in `NTFY_TOPIC` in `.env` (or `setx NTFY_TOPIC topic-name`). Details in `novidades.py`.

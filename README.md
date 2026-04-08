# ⚡ NOzempic

> *No shortcuts. No Ozempic. Just work.*

A private, automated fitness accountability system for two competitors. Every Thursday, each participant receives a personalized weekly email with competitive standings, a coach's roast, and a private health brief — all powered by real wearable data.

---

## What It Does

- Pulls weekly activity data from **Oura Ring** (Kam) and **WHOOP** (Nili)
- Parses **Renpho body composition scans** uploaded weekly as PDFs
- Scores both participants across three pillars: body composition, activity, and weekly improvement
- Generates a **two-segment weekly email** via Claude AI:
  - **Segment 1 — The Dispatch**: group standings and a coach's roast (deltas only, no private data)
  - **Segment 2 — Personal Brief**: your full numbers, trends, and actionable tips (private to you)
- Sends emails automatically every **Thursday at 9AM ET** via Gmail

---

## Participants

| Name | Device | Role |
|------|--------|------|
| Kam  | Oura Ring | Admin & competitor |
| Nili | WHOOP     | Competitor |

---

## Scoring System

Scores are out of 100 points, combining three pillars:

| Pillar | Weight | Source |
|--------|--------|--------|
| Body Composition Score | 40% | Renpho scan (Body Score) |
| Activity Score | 30% | Oura or WHOOP composite |
| Weekly Improvement | 30% | Week-over-week weight & body fat delta |

See [`NOzempic_Scoring_Guide.docx`](./NOzempic_Scoring_Guide.docx) for full details and worked examples.

---

## Project Structure

```
NOzempic/
├── connectors/
│   ├── oura.py          # Oura Ring API v2 (Personal Access Token)
│   ├── whoop.py         # WHOOP API v1 (OAuth2, auto-refresh)
│   └── renpho.py        # Renpho PDF parser (pdfplumber)
├── engine/
│   ├── scoring.py       # Scoring & ranking logic
│   └── generator.py     # Claude AI email content generation
├── mailer/
│   └── sender.py        # Gmail SMTP sender
├── onboarding/
│   ├── nili_onboarding.html   # Onboarding questionnaire (GitHub Pages)
│   ├── whoop_callback.html    # WHOOP OAuth callback page (GitHub Pages)
│   └── privacy.html           # Privacy policy (GitHub Pages)
├── data/
│   └── weekly/          # Drop Renpho PDFs here before each run
├── .github/
│   └── workflows/
│       ├── weekly_email.yml          # Runs every Thursday 9AM ET
│       ├── test_email.yml            # Manual test trigger
│       ├── whoop_generate_link.yml   # Step 1: generate WHOOP auth link
│       └── whoop_exchange_code.yml   # Step 2: exchange code for tokens
├── weekly_run.py        # Main weekly orchestrator
├── test_email.py        # End-to-end test script
├── config.json          # Participant config & scoring weights
└── requirements.txt
```

---

## Setup

### Prerequisites

- GitHub account with this repo
- Anthropic API account with credits ([console.anthropic.com](https://console.anthropic.com))
- Gmail account with App Password enabled
- Oura Ring account with a Personal Access Token
- WHOOP account with a developer app registered

### GitHub Secrets Required

Add all of these at `Settings → Secrets and variables → Actions`:

| Secret | Description |
|--------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `OURA_PERSONAL_ACCESS_TOKEN` | From cloud.ouraring.com/personal-access-tokens |
| `WHOOP_CLIENT_ID` | From developer.whoop.com |
| `WHOOP_CLIENT_SECRET` | From developer.whoop.com |
| `WHOOP_ACCESS_TOKEN` | Generated via WHOOP Setup workflows |
| `WHOOP_REFRESH_TOKEN` | Generated via WHOOP Setup workflows |
| `GMAIL_ADDRESS` | Gmail address used to send emails |
| `GMAIL_APP_PASSWORD` | 16-char App Password from Google Account settings |
| `ADMIN_EMAIL` | Email address for test emails |

### Connecting WHOOP (Nili)

1. Register an app at [developer.whoop.com](https://developer.whoop.com) with redirect URI:
   `https://kamness26.github.io/NOzempic/onboarding/whoop_callback.html`
2. Add `WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET` to GitHub Secrets
3. Run **"WHOOP Setup — Step 1"** workflow → open the generated link → approve
4. Copy the curl command from the callback page → run in Terminal
5. Add the returned `WHOOP_ACCESS_TOKEN` and `WHOOP_REFRESH_TOKEN` to GitHub Secrets

### Adding Renpho Scans

Before each Thursday run, upload the PDF to `data/weekly/` as `kam_latest.pdf`. The workflow will parse it automatically.

---

## Running

### Weekly (automatic)
The `weekly_email.yml` workflow runs every Thursday at 9AM ET via cron.

### Manual test
Go to **Actions → Test Email → Run workflow** to fire a test email immediately.

---

## Privacy

- **Segment 1** (shared): point gaps and coach commentary only — no absolute personal metrics
- **Segment 2** (private): your full data, sent only to you
- No data is stored permanently; everything is fetched fresh each week
- WHOOP tokens are stored only as encrypted GitHub Secrets

---

## Tech Stack

- **Python 3.11** — core runtime
- **Anthropic Claude API** — email content generation (`claude-opus-4-5`)
- **Oura API v2** — activity, readiness, sleep, HRV
- **WHOOP API v1** — recovery, strain, sleep
- **pdfplumber** — Renpho PDF parsing
- **Gmail SMTP** — email delivery
- **GitHub Actions** — weekly scheduling and secret management
- **GitHub Pages** — onboarding and OAuth callback hosting

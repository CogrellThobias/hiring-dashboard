# Hiring System Dashboard

Live Q2 2026 dashboard for Alva Labs Hiring System. Auto-refreshes every 10 min via GitHub Actions.

**Live URL:** Update after Pages is enabled — typically `https://<your-org>.github.io/hiring-dashboard/`

## How it works

```
GitHub Actions (cron */10) ──► scripts/generate.py ──► docs/index.html
                                       │                       │
                                       ▼                       ▼
                              HubSpot API              GitHub Pages
                              (HS deals)               (TV browser)
```

1. **GitHub Actions** runs every 10 min (or on manual trigger)
2. **`scripts/generate.py`** fetches HubSpot deals (`dealtype = "New business (hiring system)"`)
3. It injects the data into **`template.html`** and writes the result to **`docs/index.html`**
4. GitHub Pages serves `docs/index.html` at the live URL
5. The HTML has `<meta http-equiv="refresh" content="600">` — TV browser reloads every 10 min

## Data sources

| Card | Source | Refreshes |
|---|---|---|
| Q2 Logo Target (won/lost) | HubSpot live | every 10 min |
| Existing Logos | HubSpot live | every 10 min |
| Open Pipeline by Stage | HubSpot live | every 10 min |
| Open Deals with Close Date in Q2 | HubSpot live | every 10 min |
| Product Insights (HS jobs/hires MoM) | **Static snapshot** in `scripts/generate.py` | manual update |

Lightdash data (Product Insights) is a static snapshot because the metrics change daily, not hourly. Update `INSIGHTS_SNAPSHOT` in `scripts/generate.py` when needed.

## Local development

```bash
cd hiring-dashboard
pip install -r requirements.txt
HUBSPOT_TOKEN="pat-na1-..." python scripts/generate.py
open docs/index.html
```

## Setup checklist

- [ ] Push to GitHub repo
- [ ] Add `HUBSPOT_TOKEN` secret in repo Settings → Secrets and variables → Actions
- [ ] Enable Pages: Settings → Pages → Source: `Deploy from a branch` → `main` / `/docs`
- [ ] Wait ~1 min for first Action run, then visit live URL

## Configuration

Edit constants at the top of `scripts/generate.py`:

- `Q2_TARGET`: Quarterly logo target (default 15)
- `Q2_START` / `Q2_END`: Quarter date range
- `HS_DEALTYPE`: HubSpot dealtype value identifying Hiring System deals
- `INSIGHTS_SNAPSHOT`: Static HS jobs/hires per month

## TV display

Open the live URL in a TV browser, Chromecast, Apple TV, or Raspberry Pi kiosk. The page is designed for 1080p and auto-refreshes via the meta tag — no manual intervention needed.

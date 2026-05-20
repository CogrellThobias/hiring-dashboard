"""
Hiring System Dashboard Generator
Fetches HubSpot deals data and writes docs/index.html for GitHub Pages.

Environment variables required:
- HUBSPOT_TOKEN: HubSpot API token (private app access token)

Lightdash data (HS jobs/hires MoM, existing logos) is kept as a static
snapshot in this script - update manually when needed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

# ============================================================
# CONFIG
# ============================================================

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip()
HUBSPOT_BASE = "https://api.hubapi.com"

LIGHTDASH_TOKEN = os.environ.get("LIGHTDASH_TOKEN", "").strip()
LIGHTDASH_BASE = "https://eu1.lightdash.cloud/api/v1"
LIGHTDASH_PROJECT = "c18ef346-6a50-4786-849c-77495aaf3962"

# Alva internal orgs (excluded from Product Insights)
ALVA_INTERNAL_ORG_IDS = [
    11,           # Alva Labs main
    309392277,    # Alva Labs (duplicate)
    750396559,    # Alva Job Marketplace Beta
    671700569,    # alvalabs2
    1987056611,   # Alva Labs 2
    1858502011,   # test Alva
    2014729123,   # Alva Labs Test Customer
]
INSIGHTS_START_MONTH = "2026-01-01"

# Source of truth for "is this a Hiring System deal" — set by RevOps on every deal
# in both Sales and Customer pipelines.
PRODUCT_TYPE_HS = "Hiring System"

SALES_PIPELINE    = "default"
CUSTOMER_PIPELINE = "816314391"

# Won/Lost stage IDs across both pipelines
WON_STAGES  = {"931715", "1205100833"}   # Sales:Won, Customer:Won
LOST_STAGES = {"931716", "1205100834"}   # Sales:Lost, Customer:Lost

# Sales pipeline stage IDs → human-readable labels
STAGE_LABELS = {
    "1331053572":             "Meeting Booked",
    "presentationscheduled":  "Discovery Completed",
    "contractsent":           "Solution Presented",
    "6131468":                "Proposal Sent",
    "996022":                 "Contract Sent",
    "931715":                 "Closed Won",
    "931716":                 "Closed Lost",
    # Customer pipeline
    "1205100828":             "Identified Opportunity",
    "1205100829":             "Validating Benefits",
    "1205100830":             "Confirmed Value",
    "1205100831":             "Negotiating",
    "1205100832":             "Verbal Agreement",
    "1205100833":             "Closed Won",
    "1205100834":             "Closed Lost",
}
# Stages counted as "pipeline" (Meeting Booked deliberately excluded —
# a deal only counts as pipeline once it reaches Discovery Completed).
OPEN_STAGE_ORDER = [
    "Discovery Completed",
    "Solution Presented",
    "Proposal Sent",
    "Contract Sent",
]

Q2_START = "2026-04-01"
Q2_END   = "2026-06-30"
Q2_TARGET = 15

# ============================================================
# STATIC LIGHTDASH SNAPSHOT
# Update these manually when needed (changes daily, not time-critical).
# ============================================================

INSIGHTS_FALLBACK = {
    "labels": ["Jan", "Feb", "Mar", "Apr", "May"],
    "jobs":   [63, 40, 81, 28, 6],
    "hires":  [13,  9, 14, 10, 4],
}

# ============================================================
# HUBSPOT API HELPERS
# ============================================================

def hs_search(filter_groups: list, properties: list) -> list[dict]:
    """Paginate through HubSpot deal search results."""
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    out: list[dict] = []
    body = {"filterGroups": filter_groups, "properties": properties, "limit": 100}
    while True:
        r = requests.post(
            f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
            headers=headers, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            return out
        body["after"] = after


def hs_deal_to_company_map(deal_ids: list[str]) -> dict[str, int]:
    """Batch-fetch deal → company associations."""
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    mapping: dict[str, int] = {}
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i : i + 100]
        body = {"inputs": [{"id": did} for did in chunk]}
        r = requests.post(
            f"{HUBSPOT_BASE}/crm/v4/associations/deals/companies/batch/read",
            headers=headers, json=body, timeout=30,
        )
        r.raise_for_status()
        for result in r.json().get("results", []):
            if result.get("to"):
                mapping[result["from"]["id"]] = result["to"][0]["toObjectId"]
    return mapping


def hs_companies(company_ids: list[int]) -> dict[int, str]:
    """Batch-fetch company names."""
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    names: dict[int, str] = {}
    ids = list({int(c) for c in company_ids})
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        body = {"inputs": [{"id": c} for c in chunk], "properties": ["name"]}
        r = requests.post(
            f"{HUBSPOT_BASE}/crm/v3/objects/companies/batch/read",
            headers=headers, json=body, timeout=30,
        )
        r.raise_for_status()
        for c in r.json().get("results", []):
            names[int(c["id"])] = c.get("properties", {}).get("name") or "Unknown"
    return names


# ============================================================
# LIGHTDASH API HELPERS
# ============================================================

def ld_run_query(explore: str, dimensions: list[str], metrics: list[str],
                 filters: dict, sorts: list[dict] | None = None,
                 limit: int = 100) -> list[dict]:
    """Run a Lightdash metric query and return raw rows."""
    body = {
        "exploreName": explore,
        "dimensions": dimensions,
        "metrics": metrics,
        "filters": filters,
        "sorts": sorts or [],
        "tableCalculations": [],
        "limit": limit,
    }
    r = requests.post(
        f"{LIGHTDASH_BASE}/projects/{LIGHTDASH_PROJECT}/explores/{explore}/runQuery",
        headers={"Authorization": f"ApiKey {LIGHTDASH_TOKEN}", "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Lightdash error: {data}")
    return data["results"]["rows"]


def ld_row_value(row: dict, field: str):
    """Extract raw value from a Lightdash row (nested under value.raw)."""
    return row.get(field, {}).get("value", {}).get("raw")


def fetch_active_contract_org_ids() -> list[int]:
    """Get organization_integer_ids with at least one active contract."""
    rows = ld_run_query(
        explore="contract_details",
        dimensions=["contract_details_organization_integer_id"],
        metrics=[],
        filters={"dimensions": {"id": "root", "and": [
            {"id":"a","target":{"fieldId":"contract_details_is_active_contract"},
             "operator":"equals","values":[True]},
            {"id":"b","target":{"fieldId":"contract_details_organization_integer_id"},
             "operator":"notEquals","values":ALVA_INTERNAL_ORG_IDS},
        ]}},
        limit=5000,
    )
    ids = []
    for r in rows:
        v = ld_row_value(r, "contract_details_organization_integer_id")
        if v is not None:
            ids.append(int(v))
    return ids


def fetch_insights_mom() -> dict:
    """Fetch HS jobs/hires per month from 2026-01 onwards, active subscriptions only."""
    active_ids = fetch_active_contract_org_ids()
    print(f"  → {len(active_ids)} active contract orgs", file=sys.stderr)

    common_filters = [
        {"id":"a","target":{"fieldId":"job_position_details_hiring_success_enabled"},
         "operator":"equals","values":[True]},
        {"id":"c","target":{"fieldId":"job_position_details_organization_integer_id"},
         "operator":"equals","values":active_ids},
    ]

    # HS jobs created per month
    jobs_rows = ld_run_query(
        explore="job_position_details",
        dimensions=["job_position_details_created_timestamp_month"],
        metrics=["job_position_details_count_job_positions"],
        filters={"dimensions": {"id":"root","and":[
            *common_filters,
            {"id":"d","target":{"fieldId":"job_position_details_created_timestamp_month"},
             "operator":"greaterThanOrEqual","values":[INSIGHTS_START_MONTH]},
        ]}},
        sorts=[{"fieldId":"job_position_details_created_timestamp_month","descending":False}],
        limit=24,
    )

    # HS positions filled per month (first hired date)
    hires_rows = ld_run_query(
        explore="job_position_details",
        dimensions=["job_position_details_first_hired_date_month"],
        metrics=["job_position_details_count_job_positions"],
        filters={"dimensions": {"id":"root","and":[
            *common_filters,
            {"id":"d","target":{"fieldId":"job_position_details_first_hired_date_month"},
             "operator":"greaterThanOrEqual","values":[INSIGHTS_START_MONTH]},
        ]}},
        sorts=[{"fieldId":"job_position_details_first_hired_date_month","descending":False}],
        limit=24,
    )

    # Merge into month-aligned arrays
    MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    jobs_by_month  = {}
    hires_by_month = {}
    for r in jobs_rows:
        m = ld_row_value(r, "job_position_details_created_timestamp_month")
        v = ld_row_value(r, "job_position_details_count_job_positions")
        if m: jobs_by_month[m[:7]] = int(v or 0)
    for r in hires_rows:
        m = ld_row_value(r, "job_position_details_first_hired_date_month")
        v = ld_row_value(r, "job_position_details_count_job_positions")
        if m: hires_by_month[m[:7]] = int(v or 0)

    # Build aligned arrays through current month
    now = datetime.now(timezone.utc)
    months: list[str] = []
    cur_year, cur_month = 2026, 1
    while (cur_year, cur_month) <= (now.year, now.month):
        months.append(f"{cur_year}-{cur_month:02d}")
        cur_month += 1
        if cur_month > 12:
            cur_month = 1; cur_year += 1

    return {
        "labels": [MONTH_NAMES[int(m.split('-')[1])-1] for m in months],
        "jobs":   [jobs_by_month.get(m, 0)  for m in months],
        "hires":  [hires_by_month.get(m, 0) for m in months],
    }


# ============================================================
# FETCH + TRANSFORM
# ============================================================

def fetch_hs_deals() -> list[dict]:
    """All HS deals across both Sales and Customer pipelines, enriched with company."""
    props = [
        "dealname", "amount", "closedate", "createdate",
        "dealstage", "pipeline", "dealtype", "product_type", "contract_start_date",
    ]
    deals = hs_search(
        [{"filters": [{"propertyName": "product_type", "operator": "EQ", "value": PRODUCT_TYPE_HS}]}],
        props,
    )
    # Enrich with company name
    deal_ids = [d["id"] for d in deals]
    deal_to_co = hs_deal_to_company_map(deal_ids) if deal_ids else {}
    co_names = hs_companies(list(deal_to_co.values())) if deal_to_co else {}
    for d in deals:
        p = d.get("properties", {})
        co_id = deal_to_co.get(d["id"])
        p["_company_id"]   = co_id
        p["_company_name"] = co_names.get(co_id, "Unknown") if co_id else "Unknown"
        # Fallback: use part before " - " in dealname if company is Unknown
        if p["_company_name"] in ("Unknown", "") and " - " in (p.get("dealname") or ""):
            p["_company_name"] = p["dealname"].split(" - ")[0]
        elif p["_company_name"] in ("Unknown", "") and " – " in (p.get("dealname") or ""):
            p["_company_name"] = p["dealname"].split(" – ")[0]
        p["_amount"] = float(p.get("amount") or 0)
    return deals


def build_dashboard_data(deals: list[dict], _insights_data: dict) -> dict[str, Any]:
    """Aggregate HubSpot data for dashboard rendering."""
    won = [d for d in deals if d["properties"].get("dealstage") in WON_STAGES]
    lost = [d for d in deals if d["properties"].get("dealstage") in LOST_STAGES]
    open_deals = [
        d for d in deals
        if d["properties"].get("dealstage") not in WON_STAGES | LOST_STAGES
    ]

    # ---- Existing HS Logos (unique companies with at least one Won HS deal) ----
    # If a company has multiple Won deals (e.g., Sales New + Customer Renewal),
    # pick the most recent one to represent the logo's current ARR.
    won_by_company: dict[int, dict] = {}
    for d in sorted(won, key=lambda x: x["properties"].get("closedate") or ""):
        co_id = d["properties"].get("_company_id")
        if not co_id:
            co_id = f"_name:{d['properties'].get('_company_name', 'Unknown')}"
        # later iteration overwrites — gives us most recent Won per company
        won_by_company[co_id] = {
            "company": d["properties"]["_company_name"],
            "arr": d["properties"]["_amount"],
            "close": (d["properties"].get("closedate") or "")[:10],
        }
    logos = sorted(won_by_company.values(), key=lambda x: x["close"], reverse=True)

    # ---- Q2 won = unique companies with any Won HS deal in Q2 (Sales OR Customer) ----
    q2_won_companies: set = set()
    for d in won:
        close = (d["properties"].get("closedate") or "")[:10]
        if Q2_START <= close <= Q2_END:
            co_id = d["properties"].get("_company_id") or d["properties"].get("_company_name")
            q2_won_companies.add(co_id)
    q2_won = len(q2_won_companies)

    q2_lost = sum(
        1 for d in lost
        if Q2_START <= (d["properties"].get("closedate") or "")[:10] <= Q2_END
    )

    # ---- Open pipeline by stage ----
    stage_agg: dict[str, dict] = {}
    for d in open_deals:
        stage_id = d["properties"].get("dealstage")
        label = STAGE_LABELS.get(stage_id, stage_id)
        if label not in stage_agg:
            stage_agg[label] = {"stage": label, "count": 0, "arr": 0.0}
        stage_agg[label]["count"] += 1
        stage_agg[label]["arr"]   += d["properties"]["_amount"]
    # Order stages by progression, drop empty
    stages = [
        {"stage": s, "count": stage_agg[s]["count"], "arr": stage_agg[s]["arr"]}
        for s in OPEN_STAGE_ORDER
        if s in stage_agg and stage_agg[s]["count"] > 0
    ]

    # ---- Open deals with close date in Q2 (Meeting Booked excluded — not pipeline) ----
    q2_open = [
        {
            "company": d["properties"]["_company_name"],
            "stage": STAGE_LABELS.get(d["properties"].get("dealstage"), "Meeting Booked"),
            "close": (d["properties"].get("closedate") or "")[:10],
            "arr": d["properties"]["_amount"],
        }
        for d in open_deals
        if Q2_START <= (d["properties"].get("closedate") or "")[:10] <= Q2_END
        and STAGE_LABELS.get(d["properties"].get("dealstage")) in OPEN_STAGE_ORDER
    ]
    q2_open.sort(key=lambda x: x["close"])

    # ---- Pipe movement: deals created today, last 7d, last 30d ----
    pipe_movement = compute_pipe_movement(deals)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "q2_target": Q2_TARGET,
        "q2_won": q2_won,
        "q2_lost": q2_lost,
        "logos": logos,
        "stages": stages,
        "q2_open_deals": q2_open,
        "insights": _insights_data,
        "pipe_movement": pipe_movement,
    }


def compute_pipe_movement(deals: list[dict]) -> dict:
    """Count HS deals by createdate: today, this 7 days vs prev 7, this 30 days vs prev 30."""
    now = datetime.now(timezone.utc)
    today_start    = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago       = now - timedelta(days=7)
    two_weeks_ago  = now - timedelta(days=14)
    month_ago      = now - timedelta(days=30)
    two_months_ago = now - timedelta(days=60)

    def parse(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    today = wk_curr = wk_prev = mo_curr = mo_prev = 0
    for d in deals:
        created = parse(d["properties"].get("createdate"))
        if not created:
            continue
        if created >= today_start:
            today += 1
        if created >= week_ago:
            wk_curr += 1
        elif created >= two_weeks_ago:
            wk_prev += 1
        if created >= month_ago:
            mo_curr += 1
        elif created >= two_months_ago:
            mo_prev += 1

    def delta_pct(curr: int, prev: int) -> int | None:
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100)

    return {
        "today": today,
        "week":  {"current": wk_curr, "previous": wk_prev, "delta_pct": delta_pct(wk_curr, wk_prev)},
        "month": {"current": mo_curr, "previous": mo_prev, "delta_pct": delta_pct(mo_curr, mo_prev)},
    }


# ============================================================
# RENDER
# ============================================================

def render(data: dict[str, Any], template_path: Path, output_path: Path) -> None:
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # Inject between the /*__DATA__*/ markers
    rendered = template.replace("/*__DATA__*/{}", payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(f"✓ Wrote {output_path} ({size_kb:.1f} KB)")


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    if not HUBSPOT_TOKEN:
        print("ERROR: HUBSPOT_TOKEN env var is missing", file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parent.parent
    template_path = root / "template.html"
    output_path   = root / "docs" / "index.html"

    print(f"Fetching HubSpot HS deals (product_type='{PRODUCT_TYPE_HS}')…")
    t0 = time.time()
    deals = fetch_hs_deals()
    print(f"  → {len(deals)} deals in {time.time()-t0:.1f}s")

    # Fetch Lightdash Product Insights (with fallback if API fails)
    if LIGHTDASH_TOKEN:
        try:
            print("Fetching Lightdash Product Insights…")
            t0 = time.time()
            insights = fetch_insights_mom()
            print(f"  → {len(insights['labels'])} months in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  ⚠ Lightdash fetch failed ({e}), using fallback snapshot", file=sys.stderr)
            insights = INSIGHTS_FALLBACK
    else:
        print("  No LIGHTDASH_TOKEN — using fallback snapshot")
        insights = INSIGHTS_FALLBACK

    data = build_dashboard_data(deals, insights)
    print(
        f"  Q2 won: {data['q2_won']}/{data['q2_target']} · "
        f"Logos all-time: {len(data['logos'])} · "
        f"Open pipeline: {sum(s['count'] for s in data['stages'])} deals · "
        f"Q2 open deals: {len(data['q2_open_deals'])}"
    )

    render(data, template_path, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

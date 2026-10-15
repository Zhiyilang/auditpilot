"""Receipts: structured JSON + a self-contained, shareable HTML report.

The HTML report has zero external assets (inline CSS, no JS, no fonts) so it
can be emailed to an accountant or attached to a dispute and still render.
"""

from __future__ import annotations

import html
import json
from string import Template
from typing import Optional

from .brain import explain_findings

_CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:#f6f7f9;color:#1c2333;margin:0;padding:24px;line-height:1.5}
.card{max-width:880px;margin:0 auto;background:#fff;border-radius:14px;
padding:28px 32px;box-shadow:0 1px 3px rgba(16,24,40,.08)}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#5b6478;font-size:13px;margin-bottom:20px}
.badge{display:inline-block;background:#e8f5e9;color:#1b5e20;border-radius:999px;
padding:3px 12px;font-size:12px;font-weight:600;margin-bottom:16px}
.metrics{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}
.metric{flex:1;min-width:150px;background:#f2f4f8;border-radius:10px;padding:14px 16px}
.metric .v{font-size:22px;font-weight:700}
.metric .k{font-size:12px;color:#5b6478;text-transform:uppercase;letter-spacing:.04em}
h2{font-size:15px;margin:26px 0 8px;text-transform:uppercase;letter-spacing:.05em;color:#3a4358}
table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}
th{background:#f2f4f8;text-align:left;padding:8px 10px;border-bottom:2px solid #e3e7ef}
td{padding:8px 10px;border-bottom:1px solid #edf0f5;vertical-align:top}
.ev{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;color:#5b6478}
.hike{color:#b3261e;font-weight:600}
.drop{color:#1b5e20;font-weight:600}
.note{font-size:12px;color:#5b6478;margin-top:24px;border-top:1px solid #edf0f5;padding-top:12px}
"""

_PAGE = Template("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AuditPilot report — $monthly_total/month recurring</title>
<style>$css</style></head><body><div class="card">
<h1>AuditPilot — recurring-charge audit</h1>
<div class="sub">Generated locally · $generated_at · statement: $txn_count transactions ($skipped rows skipped)</div>
<span class="badge">100% offline — your statement never left this machine</span>
<div class="metrics">
<div class="metric"><div class="v">$monthly_total</div><div class="k">recurring / month</div></div>
<div class="metric"><div class="v">$annual_total</div><div class="k">recurring / year</div></div>
<div class="metric"><div class="v hike">$recoverable</div><div class="k">recoverable now</div></div>
<div class="metric"><div class="v hike">$potential</div><div class="k">potential savings / yr</div></div>
<div class="metric"><div class="v">$score/100</div><div class="k">leakage score ($grade)</div></div>
</div>
<h2>Explanation</h2><p>$explanation</p>
<h2>Recurring charges ($sub_count)</h2>
$subs_table
<h2>Price changes ($hike_count)</h2>
$hikes_table
<h2>Duplicate charges ($dup_count)</h2>
$dups_table
<h2>Trial conversions ($trial_count)</h2>
$trials_table
<h2>Savings plan</h2>
$plan_table
<div class="note">Every finding cites its source line in the original statement.
Risk score = min(100, 35 x duplicates + 12 x price changes + 8 x trial conversions).
AuditPilot is deterministic and offline; amounts are read, never inferred.</div>
</div></body></html>""")


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _money(v) -> str:
    return f"${v:,.2f}"


def _rows(headers, rows) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="ev">none detected</td></tr>'
    return f"<table><tr>{head}</tr>{body}</table>"


def _evidence_cell(items, limit=4) -> str:
    shown = items[:limit]
    text = "<br>".join(
        f"line {e['line']}: {_esc(e['raw'])}" for e in shown
    )
    if len(items) > limit:
        text += f"<br>(+{len(items) - limit} more)"
    return f'<span class="ev">{text}</span>'


def build_receipt(audit: dict, generated_at: str) -> dict:
    """Structured receipt (JSON-serializable, evidence included)."""
    return {
        "tool": "AuditPilot",
        "version": audit.get("version", "1.0.0"),
        "generated_at": generated_at,
        "input": {
            "mode": audit["parse"]["mode"],
            "transactions": audit["parse"]["transactions"],
            "rows_skipped": audit["parse"]["skipped"],
            "charge_direction": "positive" if audit["parse"]["charge_sign"] > 0 else "negative",
        },
        "summary": {
            "subscription_count": len(audit["subscriptions"]),
            "monthly_total": audit["monthly_total"],
            "annual_total": round(audit["monthly_total"] * 12, 2),
            "recoverable_now": audit["recoverable_now"],
            "potential_annual_savings": audit["potential_annual_savings"],
        },
        "risk": audit["risk"],
        "subscriptions": audit["subscriptions"],
        "price_hikes": audit["price_hikes"],
        "duplicates": audit["duplicates"],
        "trials": audit["trials"],
        "savings_plan": audit["savings_plan"],
        "explanation": audit["explanation"],
        "trace": audit["trace"],
    }


def render_html(receipt: dict) -> str:
    subs_rows = [
        (
            _esc(s["merchant"]),
            s["cadence"],
            _money(s["latest_amount"]),
            _money(s["monthly_equivalent"]),
            f"{s['count']}x",
            _evidence_cell(s["evidence"], 3),
        )
        for s in receipt["subscriptions"]
    ]
    hikes_rows = [
        (
            _esc(h["merchant"]),
            f"{_money(h['from_amount'])} &rarr; {_money(h['to_amount'])}",
            f'<span class="{"hike" if h["delta"] > 0 else "drop"}">'
            f'{"+" if h["delta"] > 0 else ""}{_money(h["delta"])} ({h["delta_pct"]}%)</span>',
            h["changed_on"],
            _money(h["annual_delta"]),
            _evidence_cell(h["evidence"], 2),
        )
        for h in receipt["price_hikes"]
    ]
    dups_rows = [
        (
            _esc(d["merchant"]),
            _money(d["amount"]),
            f"{d['times']}x",
            _money(d["recoverable"]),
            d["confidence"],
            _evidence_cell(d["evidence"], 3),
        )
        for d in receipt["duplicates"]
    ]
    trials_rows = [
        (
            _esc(t["merchant"]),
            _money(t["trial_amount"]),
            _money(t["full_amount"]),
            t["converted_on"],
            _evidence_cell(t["evidence"], 2),
        )
        for t in receipt["trials"]
    ]
    plan_rows = [
        (
            _esc(a["action"].replace("_", " ")),
            _esc(a["merchant"]),
            _money(a.get("save_now", 0.0)),
            _money(a.get("save_annual", 0.0)),
            _esc(a["detail"]),
        )
        for a in receipt["savings_plan"]["actions"]
    ]
    s = receipt["summary"]
    return _PAGE.substitute(
        css=_CSS,
        monthly_total=_money(s["monthly_total"]),
        annual_total=_money(s["annual_total"]),
        recoverable=_money(s["recoverable_now"]),
        potential=_money(s["potential_annual_savings"]),
        score=receipt["risk"]["score"],
        grade=receipt["risk"]["grade"],
        generated_at=_esc(receipt["generated_at"]),
        txn_count=receipt["input"]["transactions"],
        skipped=receipt["input"]["rows_skipped"],
        explanation=_esc(receipt["explanation"]),
        sub_count=s["subscription_count"],
        subs_table=_rows(
            ["merchant", "cadence", "latest", "/month", "seen", "evidence"], subs_rows
        ),
        hike_count=len(receipt["price_hikes"]),
        hikes_table=_rows(
            ["merchant", "change", "delta", "since", "/year", "evidence"], hikes_rows
        ),
        dup_count=len(receipt["duplicates"]),
        dups_table=_rows(
            ["merchant", "amount", "times", "recoverable", "confidence", "evidence"], dups_rows
        ),
        trial_count=len(receipt["trials"]),
        trials_table=_rows(
            ["merchant", "trial", "full price", "converted", "evidence"], trials_rows
        ),
        plan_table=_rows(
            ["action", "merchant", "save now", "save / year", "detail"], plan_rows
        ),
    )


def receipt_json(receipt: dict) -> str:
    return json.dumps(receipt, ensure_ascii=False, indent=2)

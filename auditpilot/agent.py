"""The AuditPilot agent loop.

PERCEIVE -> PARSE -> DETECT -> PLAN -> EXPLAIN -> RECEIPT

Every stage is timed and recorded in a trace so the UI (and the demo video)
can show the agent thinking. There is no hidden step: each stage's output is
pure data derived from the stage before it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

from .statement import ParseResult, Txn, parse_statement
from .detectors import (
    detect_creep,
    detect_duplicates,
    detect_fees,
    detect_price_changes,
    detect_recurring,
    detect_trials,
)
from .brain import explain_findings
from . import receipts

STAGES = ["PERCEIVE", "PARSE", "DETECT", "PLAN", "EXPLAIN", "RECEIPT"]


def _stage(name: str, detail: str, t0: float, **extra) -> dict:
    entry = {
        "stage": name,
        "detail": detail,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    entry.update(extra)
    return entry


def build_savings_plan(
    recurring: List[dict],
    hikes: List[dict],
    duplicates: List[dict],
    trials: List[dict],
    fees: Optional[List[dict]] = None,
    creep: Optional[List[dict]] = None,
) -> dict:
    """Turn findings into an ordered, grounded action list.

    Only actions derived from findings are proposed — no invented advice.
    """
    fees = fees or []
    creep = creep or []
    actions = []
    recoverable_now = 0.0
    potential_annual = 0.0

    for d in duplicates:
        recoverable_now += d["recoverable"]
        actions.append({
            "action": "dispute_duplicate",
            "merchant": d["merchant"],
            "save_now": d["recoverable"],
            "save_annual": 0.0,
            "priority": 1,
            "detail": (
                f"{d['times']} identical {d['merchant']} charges of ${d['amount']:.2f} "
                f"within {d['window_days']} day(s) — ask the issuer for a chargeback."
            ),
            "evidence": d["evidence"],
        })
    for f in fees:
        if not f["avoidable"]:
            continue
        potential_annual += f["annualized"]
        if f["category"] in ("overdraft", "late_payment"):
            hint = "the balance ran dry — fix the buffer, not the fee"
        else:
            hint = "usually avoidable with one settings change"
        actions.append({
            "action": "stop_avoidable_fees",
            "merchant": f["merchant"],
            "save_now": 0.0,
            "save_annual": f["annualized"],
            "priority": 1,
            "detail": (
                f"{f['count']}x {f['category'].replace('_', ' ')} fee "
                f"(${f['total']:.2f} total, ~${f['annualized']:.2f}/yr at this rate) — {hint}."
            ),
            "evidence": f["evidence"],
        })
    for c in creep:
        potential_annual += c["annual_delta"]
        actions.append({
            "action": "review_price_creep",
            "merchant": c["merchant"],
            "save_now": 0.0,
            "save_annual": c["annual_delta"],
            "priority": 2,
            "detail": (
                f"{c['merchant']} crept from ${c['from_amount']:.2f} to "
                f"${c['to_amount']:.2f} over {c['step_count']} sub-5% steps "
                f"(+{c['total_pct']}%) — never alerted, now ~${c['annual_delta']:.2f}/yr "
                f"more than {c['first_seen'][:4]}."
            ),
            "evidence": c["evidence"],
        })
    for t in trials:
        actions.append({
            "action": "check_trial_conversion",
            "merchant": t["merchant"],
            "save_now": 0.0,
            "save_annual": round(t["full_amount"] * 12, 2),
            "priority": 2,
            "detail": (
                f"A ${t['trial_amount']:.2f} charge turned into "
                f"${t['full_amount']:.2f} on {t['converted_on']} — cancel if unused."
            ),
            "evidence": t["evidence"],
        })
    for h in hikes:
        potential_annual += h["annual_delta"]
        direction = "rose" if h["delta"] > 0 else "dropped"
        actions.append({
            "action": "review_price_change",
            "merchant": h["merchant"],
            "save_now": 0.0,
            "save_annual": h["annual_delta"] if h["delta"] > 0 else 0.0,
            "priority": 2,
            "detail": (
                f"{h['merchant']} {direction} from ${h['from_amount']:.2f} to "
                f"${h['to_amount']:.2f} on {h['changed_on']} "
                f"({'+' if h['delta'] > 0 else ''}{h['delta_pct']}%) — "
                f"downgrade, switch, or cancel if the value is gone."
            ),
            "evidence": h["evidence"],
        })
    for s in recurring:
        actions.append({
            "action": "review_subscription",
            "merchant": s["merchant"],
            "save_now": 0.0,
            "save_annual": round(s["monthly_equivalent"] * 12, 2),
            "priority": 3,
            "detail": (
                f"{s['merchant']} bills {s['cadence']} at "
                f"${s['latest_amount']:.2f} "
                f"(${s['monthly_equivalent']:.2f}/month equivalent)."
            ),
            "evidence": s["evidence"][:3],
        })

    actions.sort(key=lambda a: (a["priority"], -(a["save_now"] + a["save_annual"])))
    monthly_total = round(sum(s["monthly_equivalent"] for s in recurring), 2)
    return {
        "actions": actions,
        "recoverable_now": round(recoverable_now, 2),
        "potential_annual_savings": round(potential_annual, 2),
        "monthly_total": monthly_total,
        "annual_total": round(monthly_total * 12, 2),
    }


def risk_score(
    duplicates: List[dict], hikes: List[dict], trials: List[dict],
    fees: Optional[List[dict]] = None,
) -> dict:
    """Public, reproducible leakage score.

    score = min(100, 35*duplicates + 12*price_changes + 8*trial_conversions
                    + 6*avoidable_fee_categories)

    Fees enter as categories (not dollars): three overdrafts and one ATM fee
    are one behaviour problem, not four — and the score must stay comparable
    across statements of different sizes.
    """
    fees = fees or []
    fee_cats = len([f for f in fees if f.get("avoidable")])
    score = min(100, 35 * len(duplicates) + 12 * len(hikes)
                + 8 * len(trials) + 6 * fee_cats)
    if score <= 20:
        grade = "LOW"
    elif score <= 50:
        grade = "MODERATE"
    elif score <= 75:
        grade = "HIGH"
    else:
        grade = "CRITICAL"
    return {
        "score": score,
        "grade": grade,
        "formula": ("min(100, 35*duplicates + 12*price_changes + "
                    "8*trial_conversions + 6*avoidable_fee_categories)"),
    }


def audit_statement(text: str, min_count: int = 3) -> dict:
    """Full audit. Returns a JSON-serializable dict (see receipts.build_receipt)."""
    trace: List[dict] = []
    t0 = time.perf_counter()

    # PERCEIVE -----------------------------------------------------------
    n_lines = len([ln for ln in text.splitlines() if ln.strip()])
    trace.append(_stage(
        "PERCEIVE", f"statement received: {n_lines} non-empty lines, {len(text)} chars", t0
    ))

    # PARSE --------------------------------------------------------------
    t = time.perf_counter()
    parse: ParseResult = parse_statement(text)
    charges = parse.charges()
    trace.append(_stage(
        "PARSE",
        f"{len(parse.transactions)} transactions parsed ({parse.skipped} rows skipped), "
        f"mode={parse.mode}, charges={'positive' if parse.charge_sign > 0 else 'negative'} "
        f"amounts -> {len(charges)} charge rows",
        t,
        transactions=len(parse.transactions),
        skipped=parse.skipped,
    ))

    # DETECT -------------------------------------------------------------
    t = time.perf_counter()
    recurring = detect_recurring(charges, min_count=min_count)
    hikes = detect_price_changes(charges)
    duplicates = detect_duplicates(charges)
    trials = detect_trials(charges)
    fees = detect_fees(charges)
    creep = detect_creep(charges)
    trace.append(_stage(
        "DETECT",
        f"{len(recurring)} recurring, {len(hikes)} price changes, "
        f"{len(duplicates)} duplicates, {len(trials)} trial conversions, "
        f"{len(fees)} fee categories, {len(creep)} price creeps",
        t,
        recurring=len(recurring),
        price_changes=len(hikes),
        duplicates=len(duplicates),
        trials=len(trials),
        fees=len(fees),
        creeps=len(creep),
    ))

    # PLAN ---------------------------------------------------------------
    t = time.perf_counter()
    plan = build_savings_plan(recurring, hikes, duplicates, trials,
                              fees=fees, creep=creep)
    trace.append(_stage(
        "PLAN",
        f"{len(plan['actions'])} actions, ${plan['recoverable_now']:.2f} recoverable now, "
        f"${plan['potential_annual_savings']:.2f}/yr potential savings",
        t,
    ))

    # EXPLAIN ------------------------------------------------------------
    t = time.perf_counter()
    risk = risk_score(duplicates, hikes, trials, fees=fees)
    summary = {
        "subscriptions": recurring,
        "price_hikes": hikes,
        "duplicates": duplicates,
        "trials": trials,
        "fees": fees,
        "creep": creep,
        "monthly_total": plan["monthly_total"],
    }
    explanation, brain_name = explain_findings(summary)
    trace.append(_stage("EXPLAIN", explanation[:240], t, brain=brain_name))

    # RECEIPT ------------------------------------------------------------
    t = time.perf_counter()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    audit = {
        "version": "1.0.0",
        "generated_at": generated_at,
        "parse": {
            "mode": parse.mode,
            "transactions": len(parse.transactions),
            "skipped": parse.skipped,
            "charge_sign": parse.charge_sign,
        },
        "subscriptions": recurring,
        "price_hikes": hikes,
        "duplicates": duplicates,
        "trials": trials,
        "fees": fees,
        "creep": creep,
        "monthly_total": plan["monthly_total"],
        "recoverable_now": plan["recoverable_now"],
        "potential_annual_savings": plan["potential_annual_savings"],
        "savings_plan": plan,
        "risk": risk,
        "explanation": explanation,
        "trace": trace,
    }
    receipt = receipts.build_receipt(audit, generated_at)
    trace.append(_stage(
        "RECEIPT",
        f"JSON receipt + HTML report built ({len(receipt['subscriptions'])} subscriptions, "
        f"score {risk['score']}/100 {risk['grade']})",
        t,
    ))
    audit["receipt"] = receipt
    return audit


def audit_to_html(audit: dict) -> str:
    return receipts.render_html(audit["receipt"])

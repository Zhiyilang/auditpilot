"""Detectors: recurring charges, price changes, duplicates, trial conversions,
bank/processing fees, and gradual price creep.

Every detector returns findings as plain dicts with an ``evidence`` list
(source line numbers + raw rows), so no finding is ever unsourced.

Cadence buckets (center days, tolerance):
  weekly    7   +-2      biweekly  14  +-3      monthly   30.4 +-5
  quarterly 91  +-10     semiannual 182 +-15    annual    365  +-20
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Dict, List, Optional

from .statement import Txn, merchant_key, display_name

CADENCES = [
    ("weekly", 7, 2),
    ("biweekly", 14, 3),
    ("monthly", 30.4, 5),
    ("quarterly", 91, 10),
    ("semiannual", 182, 15),
    ("annual", 365, 20),
]


def _group_by_merchant(charges: List[Txn]) -> Dict[str, List[Txn]]:
    groups: Dict[str, List[Txn]] = {}
    for t in charges:
        groups.setdefault(merchant_key(t.description), []).append(t)
    for txs in groups.values():
        txs.sort(key=lambda t: (t.date, t.line_no))
    return groups


def _median(xs: List[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _mag(t: Txn) -> float:
    """Charge magnitude. Exports differ in sign convention (debits may be
    negative); detectors always work with the positive money-out amount."""
    return abs(t.amount)


def _best_cadence(median_days: float) -> Optional[tuple]:
    best = None
    for name, center, tol in CADENCES:
        dist = abs(median_days - center)
        if dist <= tol and (best is None or dist < best[3]):
            best = (name, center, tol, dist)
    return best[:3] if best else None


# --------------------------------------------------------------------------
# 1. recurring charges (subscriptions)
# --------------------------------------------------------------------------

def detect_recurring(charges: List[Txn], min_count: int = 3) -> List[dict]:
    findings = []
    for key, txs in _group_by_merchant(charges).items():
        if len(txs) < min_count and not (len(txs) >= 2 and _annual_only(txs)):
            continue
        if _is_fee_merchant(txs[0].description):
            continue  # fees are leakage, not subscriptions — see detect_fees
        intervals = [(txs[i + 1].date - txs[i].date).days for i in range(len(txs) - 1)]
        if not intervals:
            continue
        med = _median(intervals)
        cadence = _best_cadence(med)
        if cadence is None:
            continue
        name, center, tol = cadence
        # annual charges with only 2 sightings: accept without interval test
        if len(txs) >= 3:
            within = sum(1 for d in intervals if abs(d - center) <= tol)
            consistency = within / len(intervals)
        else:
            consistency = 1.0 if abs(med - center) <= tol else 0.0
        if consistency < 0.6:
            continue
        amounts = [round(_mag(t), 2) for t in txs]
        latest = amounts[-1]
        variable = (max(amounts) - min(amounts)) > 0.10 * max(amounts) if max(amounts) else False
        monthly_equiv = latest / 12.0 if name == "annual" else latest
        if name == "weekly":
            monthly_equiv = latest * 52 / 12
        elif name == "biweekly":
            monthly_equiv = latest * 26 / 12
        elif name == "quarterly":
            monthly_equiv = latest / 3
        elif name == "semiannual":
            monthly_equiv = latest / 6
        findings.append({
            "type": "RECURRING",
            "merchant": display_name(txs[-1].description),
            "merchant_key": key,
            "cadence": name,
            "count": len(txs),
            "latest_amount": latest,
            "monthly_equivalent": round(monthly_equiv, 2),
            "variable_amounts": bool(variable),
            "first_seen": txs[0].date.isoformat(),
            "last_seen": txs[-1].date.isoformat(),
            "confidence": round(consistency, 2),
            "evidence": [t.as_evidence() for t in txs],
        })
    findings.sort(key=lambda f: -f["monthly_equivalent"])
    return findings


def _annual_only(txs: List[Txn]) -> bool:
    """2 sightings that are ~1 year apart -> annual subscription."""
    if len(txs) != 2:
        return False
    gap = abs((txs[1].date - txs[0].date).days)
    return 330 <= gap <= 400


# --------------------------------------------------------------------------
# 2. price changes (silent hikes / discounts)
# --------------------------------------------------------------------------

def _cluster_levels(amounts: List[float], rel_tol: float = 0.03) -> List[float]:
    """Merge near-identical amounts into 'levels' (first-seen order)."""
    levels: List[float] = []
    for a in amounts:
        for lv in levels:
            if abs(a - lv) <= max(0.02, rel_tol * max(abs(lv), abs(a))):
                break
        else:
            levels.append(a)
    return levels


def detect_price_changes(charges: List[Txn]) -> List[dict]:
    findings = []
    for key, txs in _group_by_merchant(charges).items():
        if len(txs) < 3:
            continue
        amounts = [round(_mag(t), 2) for t in txs]
        levels = _cluster_levels(amounts)
        if len(levels) < 2 or len(levels) > 6:
            continue
        seq = []
        for a in amounts:
            for i, lv in enumerate(levels):
                if abs(a - lv) <= max(0.02, 0.03 * max(abs(lv), abs(a))):
                    seq.append(i)
                    break
        # walk transitions; a step counts when both levels repeat (or the
        # new level is the most recent charge after a stable old level),
        # the relative change is material, and the step *persists*: every
        # later charge stays at/above the new level (hikes) — this is what
        # separates a real price change from seasonal bill variation.
        for i in range(len(seq) - 1):
            if seq[i] == seq[i + 1]:
                continue
            old_level = levels[seq[i]]
            new_level = levels[seq[i + 1]]
            old_count = seq.count(seq[i])
            new_count = seq.count(seq[i + 1])
            is_last = i + 1 == len(seq) - 1
            stable_step = old_count >= 2 and new_count >= 2
            fresh_step = old_count >= 3 and new_count >= 1 and is_last
            if not (stable_step or fresh_step):
                continue
            delta = new_level - old_level
            if abs(delta) < 0.50 or abs(delta) / max(old_level, 0.01) < 0.05:
                continue
            if delta > 0:
                persists = all(_mag(txs[j]) >= new_level * 0.98 for j in range(i + 2, len(txs)))
            else:
                persists = all(_mag(txs[j]) <= new_level * 1.02 for j in range(i + 2, len(txs)))
            if not persists:
                continue
            months_since = max(0, (date.today() - txs[i + 1].date).days) // 30
            findings.append({
                "type": "PRICE_HIKE" if delta > 0 else "PRICE_DROP",
                "merchant": display_name(txs[-1].description),
                "merchant_key": key,
                "from_amount": old_level,
                "to_amount": new_level,
                "delta": round(delta, 2),
                "delta_pct": round(100.0 * delta / old_level, 1) if old_level else None,
                "changed_on": txs[i + 1].date.isoformat(),
                "monthly_delta": round(delta, 2),
                "annual_delta": round(delta * 12, 2),
                "extra_paid_so_far": round(delta * months_since, 2),
                "evidence": [txs[i].as_evidence(), txs[i + 1].as_evidence()],
            })
    findings.sort(key=lambda f: f["changed_on"])
    return findings


# --------------------------------------------------------------------------
# 3. duplicate charges
# --------------------------------------------------------------------------

def detect_duplicates(charges: List[Txn], window_days: int = 2) -> List[dict]:
    findings = []
    for key, txs in _group_by_merchant(charges).items():
        buckets: Dict[float, List[Txn]] = {}
        for t in txs:
            buckets.setdefault(round(_mag(t), 2), []).append(t)
        for amount, group in buckets.items():
            group.sort(key=lambda t: t.date)
            cluster = [group[0]]
            for t in group[1:]:
                if (t.date - cluster[-1].date).days <= window_days:
                    cluster.append(t)
                else:
                    if len(cluster) >= 2:
                        findings.append(_dup_finding(key, cluster, amount))
                    cluster = [t]
            if len(cluster) >= 2:
                findings.append(_dup_finding(key, cluster, amount))
    findings.sort(key=lambda f: f["evidence"][0]["date"])
    return findings


def _dup_finding(key: str, cluster: List[Txn], amount: float) -> dict:
    same_day = len({t.date for t in cluster}) == 1
    return {
        "type": "DUPLICATE_CHARGE",
        "merchant": display_name(cluster[-1].description),
        "merchant_key": key,
        "amount": amount,
        "times": len(cluster),
        "window_days": (cluster[-1].date - cluster[0].date).days,
        "recoverable": round(amount * (len(cluster) - 1), 2),
        "confidence": "HIGH" if same_day else "MEDIUM",
        "evidence": [t.as_evidence() for t in cluster],
    }


# --------------------------------------------------------------------------
# 4. free-trial conversions
# --------------------------------------------------------------------------

def detect_trials(charges: List[Txn], window_days: int = 45) -> List[dict]:
    findings = []
    for key, txs in _group_by_merchant(charges).items():
        if len(txs) < 2:
            continue
        first, rest = txs[0], txs[1:]
        rest_amounts = [_mag(t) for t in rest]
        med = _median(rest_amounts)
        gap = (rest[0].date - first.date).days
        if gap > window_days or med <= 0:
            continue
        if _mag(first) <= max(1.0, 0.25 * med):
            findings.append({
                "type": "TRIAL_CONVERTED",
                "merchant": display_name(first.description),
                "merchant_key": key,
                "trial_amount": round(_mag(first), 2),
                "full_amount": round(med, 2),
                "converted_on": rest[0].date.isoformat(),
                "confidence": "CAUTION",
                "evidence": [first.as_evidence(), rest[0].as_evidence()],
            })
    return findings


# --------------------------------------------------------------------------
# 5. bank / processing fees (pure leakage — money out, no service received)
# --------------------------------------------------------------------------

# category -> (keywords matched against the uppercased description, avoidable?)
# "avoidable" = the customer can usually stop paying it (overdraft, ATM
# choice, paper statements). Foreign-transaction fees are informational:
# they are the price of a choice, not a mistake.
FEE_CATEGORIES = [
    ("overdraft", (["OVERDRAFT", "NSF", "INSUFFICIENT FUNDS", "RETURNED ITEM"], True)),
    ("late_payment", (["LATE FEE", "LATE PAYMENT", "PAST DUE FEE"], True)),
    ("atm", (["ATM FEE", "OUT-OF-NETWORK", "OUT OF NETWORK"], True)),
    ("maintenance", (["MAINTENANCE FEE", "MONTHLY FEE", "SERVICE CHARGE",
                      "ACCOUNT FEE"], True)),
    ("fx", (["FOREIGN TRANSACTION FEE", "FX FEE", "CURRENCY CONVERSION",
             "INTL TRANSACTION FEE"], False)),
    ("paper_statement", (["PAPER STATEMENT", "STATEMENT FEE"], True)),
    ("inactivity", (["INACTIVITY FEE", "DORMANT ACCOUNT"], True)),
]


def _fee_category(description: str):
    up = description.upper()
    for cat, (keywords, avoidable) in FEE_CATEGORIES:
        if any(k in up for k in keywords):
            return cat, avoidable
    return None, False


def _is_fee_merchant(description: str) -> bool:
    return _fee_category(description)[0] is not None


def detect_fees(charges: List[Txn]) -> List[dict]:
    """Aggregate fee-type charges by category.

    Fees are the one leakage class with no service on the other side of the
    ledger: every dollar is recoverable only in the future tense. The
    detector reports the total per category, whether it is avoidable, and
    how often it hit — a single maintenance fee may be the account's terms,
    three overdrafts in four months is a behaviour.
    """
    groups: Dict[str, List[Txn]] = {}
    avoidable_of: Dict[str, bool] = {}
    for t in charges:
        cat, avoidable = _fee_category(t.description)
        if cat is None:
            continue
        groups.setdefault(cat, []).append(t)
        avoidable_of[cat] = avoidable
    findings = []
    for cat, txs in groups.items():
        txs.sort(key=lambda t: (t.date, t.line_no))
        total = round(sum(_mag(t) for t in txs), 2)
        span_days = max(1, (txs[-1].date - txs[0].date).days)
        months = max(1.0, span_days / 30.4)
        # annualize only repeated fees: extrapolating a single occurrence to a
        # yearly rate invents a frequency the statement never showed
        annualized = round(total * 12.0 / months, 2) if len(txs) >= 2 else total
        findings.append({
            "type": "FEE_LEAKAGE",
            "category": cat,
            "merchant": display_name(txs[-1].description),
            "merchant_key": merchant_key(txs[-1].description),
            "count": len(txs),
            "total": total,
            "avoidable": avoidable_of[cat],
            "annualized": annualized,
            "first_seen": txs[0].date.isoformat(),
            "last_seen": txs[-1].date.isoformat(),
            "confidence": "HIGH" if len(txs) >= 2 else "CAUTION",
            "evidence": [t.as_evidence() for t in txs],
        })
    findings.sort(key=lambda f: -f["total"])
    return findings


# --------------------------------------------------------------------------
# 6. price creep (death by a thousand cuts — below the hike detector's floor)
# --------------------------------------------------------------------------

def detect_creep(
    charges: List[Txn],
    min_charges: int = 4,
    min_levels: int = 3,
    max_step_pct: float = 5.0,
    min_total_pct: float = 4.0,
) -> List[dict]:
    """Gradual price creep: many small steps, each below the price-change
    detector's 5% floor, that add up.

    A merchant going 12.00 -> 12.49 -> 12.99 -> 13.49 never trips a "price
    hike" alert (each step is <5%) but has silently raised its price 12.4%.
    This is the pattern subscription accountants actually see, and it is
    invisible to threshold-based alerting by construction.
    """
    findings = []
    for key, txs in _group_by_merchant(charges).items():
        if len(txs) < min_charges or _is_fee_merchant(txs[0].description):
            continue
        amounts = [round(_mag(t), 2) for t in txs]
        levels = _cluster_levels(amounts)
        if len(levels) < min_levels:
            continue
        seq = []
        for a in amounts:
            for i, lv in enumerate(levels):
                if abs(a - lv) <= max(0.02, 0.03 * max(abs(lv), abs(a))):
                    seq.append(i)
                    break
        # every step must be an increase in VALUE (a dip means it is not a
        # creep — compare amounts, not level indices: a new lower level still
        # gets a fresh index and would sneak through an index comparison)
        if any(levels[seq[i + 1]] < levels[seq[i]] for i in range(len(seq) - 1)):
            continue
        steps = []
        for i in range(len(seq) - 1):
            if seq[i] == seq[i + 1]:
                continue
            old_lv, new_lv = levels[seq[i]], levels[seq[i + 1]]
            pct = 100.0 * (new_lv - old_lv) / max(old_lv, 0.01)
            if pct >= max_step_pct or pct <= 0:
                continue  # a >=5% jump is a price change, not creep
            steps.append({
                "on": txs[i + 1].date.isoformat(),
                "from_amount": old_lv,
                "to_amount": new_lv,
                "pct": round(pct, 1),
                "evidence": [txs[i].as_evidence(), txs[i + 1].as_evidence()],
            })
        if len(steps) < min_levels - 1:
            continue
        first_lv, last_lv = levels[seq[0]], levels[seq[-1]]
        total_pct = 100.0 * (last_lv - first_lv) / max(first_lv, 0.01)
        if total_pct < min_total_pct:
            continue
        intervals = [(txs[i + 1].date - txs[i].date).days for i in range(len(txs) - 1)]
        per_year = 365.0 / max(1.0, _median(intervals))
        findings.append({
            "type": "PRICE_CREEP",
            "merchant": display_name(txs[-1].description),
            "merchant_key": key,
            "from_amount": first_lv,
            "to_amount": last_lv,
            "steps": steps,
            "step_count": len(steps),
            "total_pct": round(total_pct, 1),
            "annual_delta": round((last_lv - first_lv) * per_year, 2),
            "first_seen": txs[0].date.isoformat(),
            "last_seen": txs[-1].date.isoformat(),
            "evidence": ([txs[0].as_evidence(), txs[-1].as_evidence()]
                         + [e for s in steps for e in s["evidence"]][:6]),
        })
    findings.sort(key=lambda f: -f["annual_delta"])
    return findings

"""Detectors: recurring charges, price changes, duplicates, trial conversions.

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

"""Explanation brain.

OfflineBrain: deterministic, evidence-cited narrative — always available,
no network, no key. This is the default and the privacy story.

LLMBrain: optional OpenAI-compatible polish (AUDITPILOT_LLM_* env vars).
Any failure falls back to the offline narrative; the demo never breaks.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


def _fmt(v: float) -> str:
    return f"${v:,.2f}"


class OfflineBrain:
    """Deterministic narrator: turns findings into plain-English sentences."""

    name = "offline-deterministic"

    def explain(self, summary: dict) -> str:
        subs = summary["subscriptions"]
        hikes = summary["price_hikes"]
        dups = summary["duplicates"]
        trials = summary["trials"]
        fees = summary.get("fees", [])
        creep = summary.get("creep", [])
        monthly = summary["monthly_total"]

        parts = []
        if subs:
            top = ", ".join(
                f"{s['merchant']} ({_fmt(s['monthly_equivalent'])}/mo)" for s in subs[:3]
            )
            parts.append(
                f"Found {len(subs)} recurring charges worth {_fmt(monthly)} per month "
                f"({_fmt(monthly * 12)} per year). Biggest: {top}."
            )
        else:
            parts.append("No recurring charges detected in this statement.")
        if hikes:
            worst = max(hikes, key=lambda h: h["annual_delta"])
            parts.append(
                f"{len(hikes)} price change(s) detected; the largest is {worst['merchant']} "
                f"{_fmt(worst['from_amount'])} -> {_fmt(worst['to_amount'])} on "
                f"{worst['changed_on']} (+{_fmt(worst['annual_delta'])}/year)."
            )
        if dups:
            total = sum(d["recoverable"] for d in dups)
            parts.append(
                f"{len(dups)} possible duplicate charge(s) worth {_fmt(total)} "
                f"recoverable right now."
            )
        if trials:
            parts.append(
                f"{len(trials)} free-trial conversion(s) detected — verify you still use them."
            )
        avoidable = [f for f in fees if f.get("avoidable")]
        if avoidable:
            total = sum(f["total"] for f in avoidable)
            worst = max(avoidable, key=lambda f: f["total"])
            parts.append(
                f"{len(avoidable)} avoidable fee categor{'y' if len(avoidable) == 1 else 'ies'} "
                f"cost {_fmt(total)} in this window (worst: {worst['category'].replace('_', ' ')}, "
                f"{worst['count']}x) — money out with no service on the other side."
            )
        if creep:
            worst = max(creep, key=lambda c: c["annual_delta"])
            parts.append(
                f"{worst['merchant']} crept from {_fmt(worst['from_amount'])} to "
                f"{_fmt(worst['to_amount'])} in {worst['step_count']} sub-5% steps "
                f"(+{worst['total_pct']}%) — no single hike alert would ever fire."
            )
        if not hikes and not dups and not trials and not avoidable and not creep:
            parts.append("No price hikes, duplicates, trial conversions, fees, or creep found. Clean statement.")
        parts.append(
            "Everything above was computed locally; your statement never left this machine."
        )
        return " ".join(parts)


class LLMBrain:
    """Optional OpenAI-compatible narration. Falls back silently."""

    name = "llm-openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def explain(self, summary: dict) -> Optional[str]:
        prompt = (
            "You are AuditPilot's narrator. In at most 3 short sentences, summarize these "
            "audit findings for a non-technical user. Use only the numbers given; never "
            "invent merchants or amounts.\n\n"
            + json.dumps(summary, ensure_ascii=False)[:4000]
        )
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 300,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return None


def get_brain():
    """Priority: LLM (if fully configured) -> offline. Never raises."""
    base = os.environ.get("AUDITPILOT_LLM_BASE_URL", "").strip()
    key = os.environ.get("AUDITPILOT_LLM_API_KEY", "").strip()
    model = os.environ.get("AUDITPILOT_LLM_MODEL", "").strip()
    if base and key and model:
        return LLMBrain(base, key, model)
    return OfflineBrain()


def explain_findings(summary: dict) -> tuple:
    """Returns (text, brain_name). LLM failure falls back to offline."""
    brain = get_brain()
    if isinstance(brain, LLMBrain):
        text = brain.explain(summary)
        if text:
            return text, brain.name
        return OfflineBrain().explain(summary), OfflineBrain().name
    return brain.explain(summary), brain.name

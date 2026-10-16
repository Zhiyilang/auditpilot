---
title: AuditPilot
emoji: 🔍
colorFrom: indigo
colorTo: blue
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
tags:
  - mcp-server-track
  - agent-app-track
  - mobile-ai-track
---

# 🔍 AuditPilot — recurring-charge forensic auditor

**Find the subscriptions you forgot, the price hikes you missed, the duplicate
charges nobody caught, and the fees you never saw itemized — from a bank
statement, 100% offline.**

AuditPilot reads a bank or credit-card statement (CSV or pasted text) and runs
a six-stage agent loop over it:

```
PERCEIVE → PARSE → DETECT → PLAN → EXPLAIN → RECEIPT
```

Press **“Load demo statement & audit”** to see the full report instantly — no
data of yours is ever needed.

## What it finds

| detector | example |
|---|---|
| `detect_recurring` | Planet Fitness bills monthly at $39.99 ($39.99/mo, 9 sightings); a $5.25 weekly coffee shows as $22.75/mo; an annual $99 backup as $8.25/mo |
| `detect_price_changes` | Netflix $15.99 → $17.99 on 2026-04-03 (+12.5%, +$24.00/yr) — with a persistence rule so seasonal bills are never mistaken for hikes |
| `detect_duplicates` | DoorDash $24.50 ×2 on the same day — recover $24.50 |
| `detect_trials` | Spotify $1.00 → $11.99 — a trial that converted |
| `detect_fees` | Overdraft 3× = $105.00 (~$430/yr at this rate, avoidable); ATM and FX fees itemized with an avoidable/informational verdict |
| `detect_creep` | Cloud Sync $12.00 → $13.99 in 4 sub-5% steps (+16.6%) — gradual hikes that never trip a threshold alert |

Every finding cites its source line. The leakage score
(`min(100, 35×duplicates + 12×price_changes + 8×trial_conversions + 6×avoidable_fee_categories)`) is public
and reproducible. Outputs include a monthly/annual recurring total, money
recoverable now, potential annual savings, an ordered savings plan, and a
self-contained HTML report you can email to an accountant.

## Three ways in, one engine

- **This Gradio app** — upload or paste, get the report. Mobile-friendly layout.
- **MCP over stdio** — `python -m auditpilot.mcp_server`, protocol 2025-11-25,
  zero dependencies. Tools: `parse_statement`, `audit_statement`,
  `savings_plan`, `risk_score`. Works in Claude Desktop / Cursor / any host.
- **MCP over Streamable HTTP** — this app's handlers at `/gradio_api/mcp/`.

## Why offline

A statement is the full financial picture of a person. AuditPilot is pure
Python standard library: no network, no telemetry, no account, no API key. It
runs on an air-gapped laptop. An optional OpenAI-compatible model can polish
the explanation *text* only — it never touches the arithmetic, and any failure
falls back to the offline narrator.

## Self-verification

`python eval.py` → **85 assertions passing**, including real-subprocess MCP
protocol tests and negative detector tests (a one-off bill spike is not a price
hike; 2% jitter is not a price hike; a variable utility is not a hike).

## Links

- GitHub: https://github.com/Zhiyilang/auditpilot
- Demo video: <!-- YouTube link, added after recording -->
- Social post: <!-- X/LinkedIn link, added after posting -->
- Author HF handle: <!-- your HF username -->

Built during the Gradio November Hackathon (Nov 14–30, 2026). MIT licensed.

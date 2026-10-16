# AuditPilot 🔍 — recurring-charge forensic auditor

**Find the subscriptions you forgot, the price hikes you missed, and the duplicate
charges nobody caught — from a bank statement, 100% offline.**

AuditPilot reads a bank or credit-card statement (CSV or pasted text) and runs a
6-stage agent loop over it:

```
PERCEIVE ──► PARSE ──► DETECT ──► PLAN ──► EXPLAIN ──► RECEIPT
```

| stage | what happens |
|---|---|
| PERCEIVE | statement text received (file upload or paste) |
| PARSE | header/BOM/column inference → transactions; unparseable rows counted, never guessed |
| DETECT | 6 detectors run on the charge set (see below) |
| PLAN | findings → ordered, grounded savings actions |
| EXPLAIN | deterministic narrator (+ optional LLM polish) |
| RECEIPT | JSON receipt + self-contained HTML report |

**Every finding carries evidence** — the source line number and the raw row that
triggered it. Nothing is inferred that isn't in the statement.

---

## What it finds

| detector | example finding |
|---|---|
| `detect_recurring` | `Planet Fitness` bills monthly at $39.99 ($39.99/mo, 9 sightings) |
| `detect_price_changes` | `Netflix` $15.99 → $17.99 on 2026-04-03 (+12.5%, +$24.00/yr) |
| `detect_duplicates` | `Doordash` $24.50 ×2 on the same day — recover $24.50 |
| `detect_trials` | `Spotify` $1.00 → $11.99 on 2026-01-28 — a trial that converted |
| `detect_fees` | `Overdraft Fee` 3× = $105.00 (~$430/yr at this rate, avoidable) |
| `detect_creep` | `Cloud Sync` $12.00 → $13.99 in 4 sub-5% steps (+16.6%) — no single hike alert would ever fire |

Outputs per audit:

- **monthly / annual recurring total** (weekly bills converted to a monthly equivalent)
- **recoverable now** (duplicate charges)
- **potential annual savings** (price hikes)
- **leakage score** `min(100, 35×duplicates + 12×price_changes + 8×trial_conversions + 6×avoidable_fee_categories)`,
  graded LOW / MODERATE / HIGH / CRITICAL — public formula, reproducible
- an ordered **savings plan** (dispute → review → check → audit)
- a **shareable HTML report** (single file, no external assets, no JS)

## The demo statement

`examples/statement_messy.csv` is a 13-month statement with planted problems —
run it and you get, deterministically:

```
$ python -m auditpilot audit examples/statement_messy.csv

AuditPilot — 85 transactions (1 rows skipped, mode=csv)
  recurring/month : $175.26  ($2,103.12/year)
  recoverable now : $24.50
  potential savings/yr: $505.69
  leakage score   : 79/100 (CRITICAL)

  [SUB]  Planet Fitness  monthly  $39.99  ($39.99/mo, 9x)
  [SUB]  Netflix         monthly  $19.99  ($19.99/mo, 9x)
  [SUB]  Cloud Sync      monthly  $13.99  ($13.99/mo, 5x)
  [HIKE] Netflix  $15.99 -> $17.99 (+12.5%) since 2026-04-03 (+$24.00/yr)
  [HIKE] Netflix  $17.99 -> $19.99 (+11.1%) since 2026-07-03 (+$24.00/yr)
  [DUP]  Doordash Burger $24.50 x2 (HIGH) — recover $24.50
  [TRIA] Spotify  $1.00 -> $11.99 on 2026-01-28
  [FEE]  overdraft  3x $105.00 (~$430.38/yr, avoidable)
  [CREEP] Cloud Sync $12.00 -> $13.99 in 4 sub-5% steps (+16.6%, +$23.81/yr)
```

The two `[CREEP]` / `[FEE]` lines are the point: a subscription that raises its
price 4% four times never trips a "price hike" alert, and overdraft fees are
money out with no service on the other side of the ledger.

`examples/statement_clean.csv` is the negative control: constant rent/phone/internet,
zero findings, score 0.

## Quick start

```bash
# audit a statement (CLI, zero dependencies — Python 3.8+)
python -m auditpilot audit examples/statement_messy.csv
python -m auditpilot audit statement.csv --json --html report.html

# audit both bundled examples
python -m auditpilot demo

# web app (Gradio 6) — reads PORT, binds 0.0.0.0
pip install -r requirements.txt
python app.py
```

The web app has a **“Load demo statement & audit”** button — one click, no data
of yours is ever needed to see the full report.

## Parsing details (the honest part)

- **CSV with a header** (`date,description,amount`), **debit/credit column pairs**,
  and **headerless CSV** (columns inferred by ratios) are all supported.
- **Pasted text** (`01/03/2026 NETFLIX.COM 15.99`) works too — dates and amounts
  are found by pattern, description is whatever is between them.
- **Sign conventions**: most exports list debits negative. AuditPilot infers the
  charge direction from the majority sign; explicit debit/credit columns are
  trusted as-is. In unsigned exports, rows matching a narrow income-keyword list
  (SALARY, PAYROLL, REFUND, …) are excluded from the charge set.
- **UTF-8 BOMs** and unrecognized header rows are handled without counting the
  header as a skipped data row.
- **Unparseable rows are counted and reported** (`rows skipped: 1`), never guessed.
  Evidence line numbers refer to the original file.
- Merchant identity is normalized (`SQ *BLUE BOTTLE COFFEE #45` → `Blue Bottle`),
  so processor noise doesn't split one subscription into five.

## Why “offline” is the feature

Statements contain the full financial picture of a person or a small business.
AuditPilot is pure Python standard library: no network call, no telemetry, no
account, no API key. It runs on an air-gapped laptop. The HTML report is a
single self-contained file you can email to an accountant or attach to a dispute.

*Optional*: set `AUDITPILOT_LLM_BASE_URL` / `AUDITPILOT_LLM_API_KEY` /
`AUDITPILOT_LLM_MODEL` and an OpenAI-compatible model polishes the explanation
text only. It never touches the arithmetic, and any failure falls back to the
offline narrator.

## MCP server

AuditPilot exposes the same engine as MCP tools, so any MCP host can audit a
statement:

```bash
python -m auditpilot.mcp_server        # zero-dependency stdio server
```

Tools: `parse_statement` · `audit_statement` · `savings_plan` · `risk_score`
(protocol version 2025-11-25). The Gradio app additionally exposes its handlers
as MCP tools over Streamable HTTP at `/gradio_api/mcp/` — point Claude Desktop,
Cursor, or any agent at either one. See `DEPLOY.md`.

## Self-verification

```bash
python eval.py        # 100 assertions
```

Covers: parsing (CSV / headerless / debit-credit / pasted / BOM / empty), every
detector (positive **and** negative cases — a one-off bill spike is *not* a price
hike, 2% jitter is *not* a price hike, a variable utility is *not* a hike, a
price that dips mid-sequence is *not* a creep, a single fee is *not* extrapolated
to a yearly rate), the agent loop, receipt structure, HTML report hygiene (no
script tags, no external assets), the stdio MCP server via a **real subprocess**
(initialize → tools/list → tools/call → unknown tool → malformed JSON), and the CLI.

## Project layout

```
auditpilot/
  statement.py     parsing + merchant normalization
  detectors.py     recurring / price-change / duplicate / trial / fees / creep
  brain.py         offline narrator + optional OpenAI-compatible polish
  agent.py         the 6-stage loop, savings plan, risk score
  receipts.py      JSON receipt + self-contained HTML report
  mcp_server.py    zero-dependency stdio MCP server (spec 2025-11-25)
  cli.py           audit / demo / mcp subcommands
app.py             Gradio 6 web app (also an MCP server via launch(mcp_server=True))
eval.py            100-assertion suite
examples/          messy (planted problems) + clean (negative control) statements
```

## License

MIT — see `LICENSE`.

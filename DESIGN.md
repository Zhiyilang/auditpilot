# AuditPilot — design notes

## Why this project exists

Recurring charges are the quiet leak in personal and small-business finance.
Nobody cancels a subscription they forgot they had; nobody notices a $2/month
price hike; and a duplicated charge sits undisputed until someone reads the
statement line by line. Statement viewers show you rows — they don't show you
*patterns*. AuditPilot turns rows into patterns, and patterns into an action
list with evidence.

## Architecture

```
                 ┌──────────────────────────── app.py (Gradio 6) ───────────────────────────┐
                 │  upload/paste ──► audit_statement_text / audit_statement_file             │
                 └───────────────────────────────┬──────────────────────────────────────────┘
                                                 │ (same engine)
   CLI (cli.py) ──► agent.audit_statement ──► statement.parse_statement
   MCP (mcp_server.py) ──►                      │
                                                 ▼
                                      detectors.detect_*  (4 detectors)
                                                 │
                            build_savings_plan + risk_score
                                                 │
                            brain.explain (offline | optional LLM)
                                                 │
                            receipts.build_receipt / render_html
```

One engine, three front doors (CLI, Gradio web app, MCP server). The web app and
the MCP server share the exact same pure functions — there is no second
implementation to drift.

## Parsing decisions

**Charge direction.** Exports disagree on sign conventions. Rather than asking
the user to configure anything, AuditPilot infers direction from the majority
sign of a single signed amount column (most banks list debits negative), and
trusts explicit debit/credit columns as-is. Detectors then work on charge
*magnitudes*, so the same engine handles both conventions.

**Income in unsigned exports.** When every amount is positive (common with
pasted text), signs can't separate income from spending. A deliberately narrow
keyword list (SALARY, PAYROLL, PAYCHECK, DIRECT DEPOSIT, PAYMENT RECEIVED,
REFUND, INTEREST, DIVIDEND, TAX REFUND, TRANSFER IN) excludes obvious income
rows. The list is small on purpose: a false exclusion loses one row, a false
inclusion turns someone's salary into a "subscription".

**Merchant identity.** Processor prefixes and store numbers would split one
subscription into five (`SQ *BLUE BOTTLE COFFEE #45` vs `BLUE BOTTLE COFFEE`).
Normalization strips known processor tokens, store numbers, long digit runs,
embedded dates, and location suffixes (US/CA/COM/LLC/…), then keys on the first
remaining token. Display names keep the first two meaningful words.

**Never guess.** A row that doesn't parse is counted (`rows skipped`) and
reported. The bad-date row in the demo statement (`2026-02-30`) is skipped, not
silently repaired. Evidence line numbers refer to the original input, blank
lines included.

## Detector decisions

**Recurring.** Intervals between a merchant's charges are compared against six
cadence buckets (weekly 7±2, biweekly 14±3, monthly 30.4±5, quarterly 91±10,
semiannual 182±15, annual 365±20). A merchant is recurring when ≥3 sightings
agree on one bucket ≥60% of the time (2 sightings suffice when they're a year
apart — that's an annual subscription). Irregular spending (Uber, groceries)
lands between buckets and is correctly ignored.

**Price changes.** The hard part is separating a real hike from seasonal
variation (a summer electricity bill is not a price increase). A step counts
only when:

1. both amount levels repeat (old ≥2 and new ≥2), or the new level is the most
   recent charge after a stable old level (≥3) — a fresh hike;
2. the change is material: ≥5% **and** ≥$0.50;
3. the step **persists**: every later charge stays at/above the new level
   (for hikes). A one-off spike followed by a return to the old price is
   rejected — that's the negative test in `eval.py`.

Variable subscriptions are still reported as recurring (with `amount varies:
yes`) but never as price changes.

**Duplicates.** Same merchant + same amount within 48 hours. Same-day pairs are
HIGH confidence; a recurring monthly bill is 30 days apart and never matches.

**Trials.** First-ever charge ≤25% of the later price (and ≤$1 floor), followed
by a full charge within 45 days. Reported as CAUTION — it's a pattern match,
not proof.

## Savings plan

Actions are only ever derived from findings, ordered by priority:

1. `dispute_duplicate` — recoverable now
2. `review_price_change` / `check_trial_conversion`
3. `review_subscription` — the audit list

`recoverable_now` only counts duplicates (money demonstrably double-charged).
`potential_annual_savings` only counts price hikes (annualized delta). No
invented "you could save $X by switching" advice.

## Leakage score

```
score = min(100, 35 × duplicates + 12 × price_changes + 8 × trial_conversions)
```

Deliberately simple and published: a duplicate charge is worth more than a hike
(it's recoverable *now*), a hike more than a trial (it compounds). The score
measures leakage, not lifestyle — a $2,000/month rent is not a leakage.

## The narrator

`brain.OfflineBrain` builds the explanation deterministically from the findings
(templated sentences, real numbers, no invention). `brain.LLMBrain` is optional
and only rewords that summary via any OpenAI-compatible endpoint; it never sees
or produces arithmetic. Any LLM failure silently falls back — the demo never
breaks because a key is missing.

## Failure philosophy

- No credentials → offline engine, full functionality.
- Unparseable rows → counted, reported, skipped.
- Unknown MCP tool → protocol-level `-32602`.
- Malformed JSON-RPC → `-32700`.
- Empty input → empty findings, score 0, no crash.
- LLM unreachable → offline narrator.

Every one of those paths is an assertion in `eval.py`.

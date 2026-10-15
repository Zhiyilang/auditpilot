"""AuditPilot web app (Gradio 6).

Tabs
  1. Audit          — upload a statement CSV or paste text, get the full report
  2. Agent trace    — the PERCEIVE→…→RECEIPT loop with timings and evidence
  3. MCP            — how to drive AuditPilot from Claude Desktop / Cursor
  4. How it works   — detectors, the risk formula, and the privacy model

The app also exposes its handlers as MCP tools (launch(mcp_server=True)),
so an MCP client can call audit_statement_text / audit_statement_file
directly. A zero-dependency stdio server lives in auditpilot/mcp_server.py.

Run:  python app.py            (reads PORT, binds 0.0.0.0)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import gradio as gr

from auditpilot.agent import audit_statement, audit_to_html
from auditpilot import receipts

ROOT = Path(__file__).resolve().parent
EXAMPLE_PATH = ROOT / "examples" / "statement_messy.csv"
REPORT_DIR = Path(tempfile.mkdtemp(prefix="auditpilot-reports-"))

CSS = """
.metric-row{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}
.metric{background:#f2f4f8;border-radius:10px;padding:10px 14px;min-width:130px}
.metric .v{font-size:20px;font-weight:700;color:#1c2333}
.metric .k{font-size:11px;color:#5b6478;text-transform:uppercase}
footer{display:none !important}
"""

HEADER_MD = """# 🔍 AuditPilot — recurring-charge forensic auditor

**Find the subscriptions you forgot, the price hikes you missed, and the duplicate
charges nobody caught.** Upload a bank/credit-card statement (CSV or pasted text) —
everything is computed **locally, offline**; your statement never leaves this machine.

No account · no API key · no upload · pure Python standard library.
"""


def _summary_md(receipt: dict) -> str:
    s = receipt["summary"]
    risk = receipt["risk"]
    inp = receipt["input"]
    return (
        f"### Leakage score **{risk['score']}/100** ({risk['grade']}) — "
        f"{inp['transactions']} transactions parsed, {inp['rows_skipped']} rows skipped\n\n"
        f"| recurring / month | recurring / year | recoverable now | potential savings / yr |\n"
        f"|---|---|---|---|\n"
        f"| **${s['monthly_total']:,.2f}** | ${s['annual_total']:,.2f} | "
        f"**${s['recoverable_now']:,.2f}** | ${s['potential_annual_savings']:,.2f} |\n\n"
        f"{receipt['explanation']}"
    )


def _tables(receipt: dict):
    subs = [
        [s["merchant"], s["cadence"], s["latest_amount"], s["monthly_equivalent"],
         s["count"], s["first_seen"], s["last_seen"], "yes" if s["variable_amounts"] else "no"]
        for s in receipt["subscriptions"]
    ]
    hikes = [
        [h["merchant"], h["from_amount"], h["to_amount"], h["delta_pct"],
         h["changed_on"], h["annual_delta"]]
        for h in receipt["price_hikes"]
    ]
    dups = [
        [d["merchant"], d["amount"], d["times"], d["recoverable"], d["confidence"]]
        for d in receipt["duplicates"]
    ]
    plan = [
        [a["action"].replace("_", " "), a["merchant"], a["save_now"], a["save_annual"]]
        for a in receipt["savings_plan"]["actions"]
    ]
    return subs, hikes, dups, plan


def _empty():
    return (
        "_Paste a statement or load the demo to begin._", [], [], [], [],
        {}, [], None,
    )


def _audit(statement: str):
    statement = (statement or "").strip()
    if not statement:
        return _empty()
    audit = audit_statement(statement)
    receipt = audit["receipt"]
    subs, hikes, dups, plan = _tables(receipt)
    report_path = REPORT_DIR / "auditpilot-report.html"
    report_path.write_text(audit_to_html(audit), encoding="utf-8")
    return (
        _summary_md(receipt), subs, hikes, dups, plan,
        receipt, audit["trace"], str(report_path),
    )


def audit_statement_text(statement: str):
    """Audit a pasted bank/credit-card statement (CSV or plain text).

    Returns recurring subscriptions, price changes, duplicate charges,
    trial conversions, a savings plan and a 0-100 leakage score. Every
    finding cites its source line. Runs fully offline.
    """
    return _audit(statement)


def audit_statement_file(file: str = ""):
    """Audit an uploaded statement file (.csv or .txt).

    Same engine as audit_statement_text; reads the file from disk.
    """
    if not file:
        return _empty()
    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (f"Could not read file: {exc}", [], [], [], [], {}, [], None)
    return _audit(text)


def load_demo_statement() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


with gr.Blocks(title="AuditPilot — recurring-charge forensic auditor") as demo:
    gr.Markdown(HEADER_MD)

    with gr.Tab("Audit"):
        with gr.Row():
            file_in = gr.File(label="Statement file (.csv / .txt)", file_types=[".csv", ".txt"])
            text_in = gr.Textbox(
                label="…or paste statement rows",
                placeholder="date,description,amount\n2026-01-03,NETFLIX.COM,-15.99\n…",
                lines=6,
            )
        with gr.Row():
            audit_btn = gr.Button("🔍 Audit statement", variant="primary")
            demo_btn = gr.Button("Load demo statement & audit", variant="secondary")
        summary_out = gr.Markdown()
        with gr.Tabs():
            with gr.Tab("Subscriptions"):
                subs_out = gr.Dataframe(
                    headers=["merchant", "cadence", "latest", "/month", "seen",
                             "first", "last", "amount varies"],
                    datatype=["str", "str", "number", "number", "number", "str", "str", "str"],
                    row_count=(0, "dynamic"),
                )
            with gr.Tab("Price changes"):
                hikes_out = gr.Dataframe(
                    headers=["merchant", "from", "to", "change %", "since", "+$/year"],
                    datatype=["str", "number", "number", "number", "str", "number"],
                    row_count=(0, "dynamic"),
                )
            with gr.Tab("Duplicate charges"):
                dups_out = gr.Dataframe(
                    headers=["merchant", "amount", "times", "recoverable", "confidence"],
                    datatype=["str", "number", "number", "number", "str"],
                    row_count=(0, "dynamic"),
                )
            with gr.Tab("Savings plan"):
                plan_out = gr.Dataframe(
                    headers=["action", "merchant", "save now", "save / year"],
                    datatype=["str", "str", "number", "number"],
                    row_count=(0, "dynamic"),
                )
        report_out = gr.DownloadButton("⬇️ Download HTML report", variant="secondary")

    with gr.Tab("Agent trace"):
        gr.Markdown(
            "The audit is a 6-stage loop. Every stage's output is derived from the "
            "one before it — no hidden steps, and every finding cites its source line."
        )
        trace_out = gr.JSON(label="trace")

    with gr.Tab("Receipt (JSON)"):
        receipt_out = gr.JSON(label="structured receipt")

    with gr.Tab("MCP"):
        gr.Markdown(
            """## Drive AuditPilot from any MCP client

**This app itself is an MCP server** — Gradio exposes the handlers above at
`/gradio_api/mcp/` (Streamable HTTP, spec 2025-11-25).

**Zero-dependency stdio server** (no Gradio needed):

```bash
python -m auditpilot.mcp_server
```

Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "auditpilot": {
      "command": "python",
      "args": ["-m", "auditpilot.mcp_server"],
      "cwd": "/path/to/auditpilot"
    }
  }
}
```

Tools: `parse_statement` · `audit_statement` · `savings_plan` · `risk_score`
"""
        )

    with gr.Tab("How it works"):
        gr.Markdown(
            """## Detectors

| detector | what it finds | evidence |
|---|---|---|
| `detect_recurring` | charges that repeat on a weekly / biweekly / monthly / quarterly / semiannual / annual cadence | every matching charge row |
| `detect_price_changes` | step changes in amount that **persist** (separates real hikes from seasonal bill variation) | the two charges around the change |
| `detect_duplicates` | identical merchant + amount within 48h (the double-charge trap) | the duplicate rows |
| `detect_trials` | tiny first charge (≤25% of the later price) followed by a full charge within 45 days | trial + first full charge |

## Leakage score

```
score = min(100, 35 × duplicates + 12 × price_changes + 8 × trial_conversions)
```
`≤20 LOW · ≤50 MODERATE · ≤75 HIGH · >75 CRITICAL` — public and reproducible.

## Privacy model

AuditPilot is deterministic Python (standard library only). There is no
network call, no telemetry, no account. A statement can be audited on an
air-gapped laptop; the HTML report is a single self-contained file you can
email to an accountant.

*Optional:* set `AUDITPILOT_LLM_BASE_URL` / `AUDITPILOT_LLM_API_KEY` /
`AUDITPILOT_LLM_MODEL` to let an OpenAI-compatible model polish the
explanation text. It never sees the arithmetic, and any failure silently
falls back to the offline narrator.
"""
        )

    outputs = [summary_out, subs_out, hikes_out, dups_out, plan_out,
               receipt_out, trace_out, report_out]

    audit_btn.click(audit_statement_text, inputs=[text_in], outputs=outputs)
    audit_btn.click(audit_statement_file, inputs=[file_in], outputs=outputs)
    demo_btn.click(load_demo_statement, inputs=[], outputs=[text_in]).then(
        audit_statement_text, inputs=[text_in], outputs=outputs
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        mcp_server=True,
        css=CSS,
    )

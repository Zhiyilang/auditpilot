# Demo video script (target: under 3 minutes, English)

> 🎬 **Finished video already produced**: `auditpilot/auditpilot_demo.mp4`
> (29.0s, English terminal recording + captions, all output really executed) —
> upload as-is, or use the storyboard below for a voiced re-record.

Record at 1920×1080. Browser zoom 110%. Have two windows ready: the AuditPilot
web app and a terminal. The bundled demo statement means you never need to show
real financial data.

---

## 0:00–0:20 — Hook

> "This statement has 75 transactions. Somewhere in it are six subscriptions,
> two price hikes, a duplicated charge, and a free trial that converted.
> Finding them by eye takes an hour. AuditPilot does it in under a second —
> offline, because your statement is nobody else's business."

Screen: the messy example statement scrolling slowly in a text editor.

## 0:20–0:55 — One-click audit (the product)

Screen: the web app.

1. Click **“Load demo statement & audit”**.
2. Walk the summary metrics: `$161.27/month recurring`, `$24.50 recoverable
   now`, `$48/yr potential savings`, `leakage score 67/100 HIGH`.
3. Subscriptions tab: point at Planet Fitness, the weekly coffee converted to
   $22.75/month, the annual cloud backup at $8.25/month equivalent.
4. Price changes tab: Netflix $15.99 → $17.99 → $19.99, dates and annual impact.
5. Duplicate charges tab: DoorDash $24.50 twice on August 14 — "that's a
   chargeback".
6. Trial tab: Spotify $1 → $11.99.

> "Every number here cites the line it came from. Nothing is inferred."

## 0:55–1:35 — The agent loop (the engineering)

Screen: Agent trace tab.

> "It's not a chatbot. It's a six-stage loop: perceive, parse, detect, plan,
> explain, receipt. Each stage's output is derived from the one before it, and
> the whole trace is in the receipt."

Screen: Receipt JSON tab — scroll the structured receipt, highlight `evidence`
arrays with line numbers and raw rows.

## 1:35–2:10 — MCP server (Track 1 requirement)

Screen: terminal.

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m auditpilot.mcp_server
```

> "AuditPilot is an MCP server — protocol 2025-11-25, zero dependencies, stdio.
> Any MCP host can call audit_statement. The Gradio app exposes the same
> handlers over Streamable HTTP."

Screen: briefly show `DEPLOY.md`'s Claude Desktop config snippet.

## 2:10–2:40 — Offline & self-verifying

Screen: terminal.

```bash
python eval.py
```

> "Eighty-two assertions, all passing: every detector has negative tests — a
one-off bill spike is not a price hike, two percent jitter is not a price hike.
And it's pure standard library: no pip install, no network, no key."

## 2:40–3:00 — Close

> "AuditPilot. Your statement, audited locally, with the receipts to prove it."

Screen: the HTML report open in a browser tab (download it during the demo).

---

## Recording checklist

- [ ] One click produces the full report (demo button — don't upload anything)
- [ ] Zoom in on the two Netflix price hikes and the DoorDash duplicate
- [ ] Show the trace tab and one `evidence` array in the JSON receipt
- [ ] `python eval.py` tail visible: `RESULT: 85 passed, 0 failed`
- [ ] Audio: plain English, no filler; total runtime under 3 minutes
- [ ] Export 1080p, upload to YouTube, set visibility Public

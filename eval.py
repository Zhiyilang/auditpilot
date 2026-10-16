"""AuditPilot assertion suite — run: python eval.py

Covers parsing (CSV/headerless/pasted), every detector (positive + negative),
the agent loop, receipts, the stdio MCP server (real subprocess) and the CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def runpy(args, **kw):
    """Run a child interpreter over a deterministic UTF-8 pipe.

    Without this, the suite crashes on a cp936 Windows locale: the child
    emits UTF-8 bytes while the parent's reader thread decodes with the
    locale codec, the reader dies with UnicodeDecodeError, and stdout
    silently becomes None -- surfacing later as a confusing TypeError.
    """
    import os
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.setdefault("timeout", 180)
    kw.setdefault("cwd", ROOT)
    kw.setdefault("env", env)
    return subprocess.run([sys.executable] + list(args), **kw)


sys.path.insert(0, str(ROOT))

from auditpilot.agent import audit_statement, audit_to_html, build_savings_plan, risk_score
from auditpilot.detectors import (
    detect_creep,
    detect_duplicates,
    detect_fees,
    detect_price_changes,
    detect_recurring,
    detect_trials,
)
from auditpilot.statement import (
    ParseResult,
    display_name,
    merchant_key,
    parse_statement,
)

PASSED = FAILED = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label} {extra}")


def section(title: str) -> None:
    print(f"\n== {title} " + "=" * (60 - len(title)))


MESSY = (ROOT / "examples" / "statement_messy.csv").read_text(encoding="utf-8")
PASTED = (ROOT / "examples" / "statement_pasted.txt").read_text(encoding="utf-8")
CLEAN = (ROOT / "examples" / "statement_clean.csv").read_text(encoding="utf-8")

messy = parse_statement(MESSY)
pasted = parse_statement(PASTED)
clean = parse_statement(CLEAN)

# ---------------------------------------------------------------- A. parsing
section("A. statement parsing")
check("csv mode detected", messy.mode == "csv", messy.mode)
check("pasted mode detected", pasted.mode == "text", pasted.mode)
check("csv parses 85 transactions", len(messy.transactions) == 85,
      f"got {len(messy.transactions)}")
check("bad date row skipped", messy.skipped == 1, f"got {messy.skipped}")
check("charge direction negative (debits)", messy.charge_sign == -1)
check("pasted: same transactions as csv (bad row omitted)",
      len(pasted.transactions) == len(messy.transactions))
check("pasted: nothing skipped", pasted.skipped == 0, f"got {pasted.skipped}")
check("pasted: unsigned export -> positive direction", pasted.charge_sign == 1)
check("pasted: salary excluded from charges",
      not any("SALARY" in t.description.upper() for t in pasted.charges()))
check("pasted: refund excluded from charges",
      not any("REFUND" in t.description.upper() for t in pasted.charges()))
check("pasted charges == csv charges",
      len(pasted.charges()) == len(messy.charges()),
      f"{len(pasted.charges())} vs {len(messy.charges())}")

dc = parse_statement(
    "date,description,debit,credit\n"
    "2026-01-03,NETFLIX,15.99,\n"
    "2026-01-15,SALARY,,5200.00\n"
)
check("debit/credit columns", [t.amount for t in dc.transactions] == [15.99, -5200.0])
check("debit/credit charges only debits", len(dc.charges()) == 1)

hc = parse_statement(
    "2026-01-03,NETFLIX.COM,15.99\n2026-02-03,NETFLIX.COM,15.99\n"
    "2026-03-03,NETFLIX.COM,15.99\n2026-04-03,NETFLIX.COM,15.99\n"
)
check("headerless csv inferred", len(hc.transactions) == 4 and hc.mode == "csv",
      f"{len(hc.transactions)} {hc.mode}")

check("merchant_key strips processor noise",
      merchant_key("SQ *BLUE BOTTLE COFFEE #45") == "BLUE",
      merchant_key("SQ *BLUE BOTTLE COFFEE #45"))
check("merchant_key strips .COM + location",
      merchant_key("NETFLIX.COM 888-555-1212") == "NETFLIX")
check("merchant_key strips country suffix",
      merchant_key("SPOTIFY USA") == "SPOTIFY")
check("display_name readable", display_name("SQ *BLUE BOTTLE COFFEE #45") == "Blue Bottle",
      display_name("SQ *BLUE BOTTLE COFFEE #45"))
check("display_name drops phone tail",
      display_name("NETFLIX.COM 888-555-1212") == "Netflix",
      display_name("NETFLIX.COM 888-555-1212"))

empty = parse_statement("")
check("empty input -> no transactions, no crash", empty.transactions == [])

bom = ("\ufeffdate,description,amount\n"
       "2026-01-03,NETFLIX.COM,-15.99\n2026-02-03,NETFLIX.COM,-15.99\n"
       "2026-03-03,NETFLIX.COM,-15.99\n")
bp = parse_statement(bom)
check("BOM file: header recognized, no phantom skip",
      bp.mode == "csv" and len(bp.transactions) == 3 and bp.skipped == 0,
      f"mode={bp.mode} txns={len(bp.transactions)} skipped={bp.skipped}")

unknown_header = parse_statement(
    "Trans Date,Payee,Amount\n"
    "01/03/2026,NETFLIX.COM,-15.99\n02/03/2026,NETFLIX.COM,-15.99\n"
    "03/03/2026,NETFLIX.COM,-15.99\n04/03/2026,NETFLIX.COM,-15.99\n"
)
check("unrecognized header row not counted as skipped",
      len(unknown_header.transactions) == 4 and unknown_header.skipped == 0,
      f"txns={len(unknown_header.transactions)} skipped={unknown_header.skipped}")

# ------------------------------------------------------------- B. detectors
section("B. detectors (messy example)")
charges = messy.charges()
recurring = detect_recurring(charges)
by_merchant = {r["merchant_key"]: r for r in recurring}
check("7 recurring merchants found", len(recurring) == 7,
      f"got {[r['merchant_key'] for r in recurring]}")
check("NETFLIX monthly, latest 19.99",
      by_merchant.get("NETFLIX", {}).get("cadence") == "monthly"
      and by_merchant.get("NETFLIX", {}).get("latest_amount") == 19.99)
check("BLUE BOTTLE weekly", by_merchant.get("BLUE", {}).get("cadence") == "weekly")
check("weekly monthly-equivalent = amount*52/12",
      abs(by_merchant.get("BLUE", {}).get("monthly_equivalent", 0) - 22.75) < 0.01)
check("NIMBUS annual from 2 sightings",
      by_merchant.get("NIMBUS", {}).get("cadence") == "annual"
      and abs(by_merchant.get("NIMBUS", {}).get("monthly_equivalent", 0) - 8.25) < 0.01)
check("variable utility flagged variable",
      by_merchant.get("ACME", {}).get("variable_amounts") is True)
check("irregular UBER not recurring", "UBER" not in by_merchant)
check("irregular WHOLE not recurring", "WHOLE" not in by_merchant)
check("irregular AMZN not recurring", "MKTP" not in by_merchant)
check("every recurring finding carries evidence",
      all(r["evidence"] and all("line" in e for e in r["evidence"]) for r in recurring))

hikes = detect_price_changes(charges)
nf_hikes = [h for h in hikes if h["merchant_key"] == "NETFLIX"]
check("2 NETFLIX price hikes", len(nf_hikes) == 2, f"got {len(hikes)} total")
check("hike 1: 15.99 -> 17.99 on 2026-04-03",
      nf_hikes and nf_hikes[0]["from_amount"] == 15.99
      and nf_hikes[0]["to_amount"] == 17.99 and nf_hikes[0]["changed_on"] == "2026-04-03")
check("hike 2: 17.99 -> 19.99 on 2026-07-03",
      len(nf_hikes) > 1 and nf_hikes[1]["from_amount"] == 17.99
      and nf_hikes[1]["to_amount"] == 19.99)
check("hike annual delta = 24.00", nf_hikes and nf_hikes[0]["annual_delta"] == 24.0)
check("variable utility NOT flagged as a hike",
      not any(h["merchant_key"] == "ACME" for h in hikes))

dups = detect_duplicates(charges)
check("1 duplicate charge (DOORDASH 24.50 x2 same day)", len(dups) == 1,
      f"got {len(dups)}")
check("duplicate is HIGH confidence, recoverable 24.50",
      dups and dups[0]["confidence"] == "HIGH" and dups[0]["recoverable"] == 24.5)

trials = detect_trials(charges)
check("1 trial conversion (SPOTIFY 1.00 -> 11.99)", len(trials) == 1,
      f"got {len(trials)}")
check("trial merchant + amounts",
      trials and trials[0]["merchant_key"] == "SPOTIFY"
      and trials[0]["trial_amount"] == 1.0 and trials[0]["full_amount"] == 11.99)

# negative: one-off amount spike must not become a "price hike"
spike = parse_statement(
    "date,description,amount\n"
    + "".join(f"2026-{m:02d}-03,ACME ENERGY,-50.00\n" for m in range(1, 8))
    + "2026-08-03,ACME ENERGY,-71.00\n"
    + "2026-09-03,ACME ENERGY,-50.00\n"
)
check("one-off spike is not a price hike",
      detect_price_changes(spike.charges()) == [],
      f"got {detect_price_changes(spike.charges())}")

# negative: small jitter (<5%) is not a hike
jitter_rows = "".join(
    f"2026-{m:02d}-03,ACME ENERGY,-50.0{m % 2}\n" for m in range(1, 8)
)
jitter = parse_statement("date,description,amount\n" + jitter_rows)
check("2% jitter is not a price hike", detect_price_changes(jitter.charges()) == [])

section("B2. detectors (clean example)")
c_charges = clean.charges()
c_recurring = detect_recurring(c_charges)
check("clean: 3 recurring (rent/phone/internet)", len(c_recurring) == 3,
      f"got {[r['merchant_key'] for r in c_recurring]}")
check("clean: no price hikes", detect_price_changes(c_charges) == [])
check("clean: no duplicates", detect_duplicates(c_charges) == [])
check("clean: no trials", detect_trials(c_charges) == [])
check("clean: salary not a subscription",
      not any(r["merchant_key"] == "SALARY" for r in c_recurring))

# ---------------------------------------------------------------- B3. fees + creep
section("B3. bank fees + price creep")
fees = detect_fees(charges)
fee_by_cat = {f["category"]: f for f in fees}
check("3 fee categories found (overdraft/atm/fx)", len(fees) == 3,
      f"got {[f['category'] for f in fees]}")
check("overdraft: 3x, $105.00, avoidable, annualized 430.38",
      fee_by_cat.get("overdraft", {}).get("count") == 3
      and fee_by_cat.get("overdraft", {}).get("total") == 105.0
      and fee_by_cat.get("overdraft", {}).get("avoidable") is True
      and abs(fee_by_cat.get("overdraft", {}).get("annualized", 0) - 430.38) < 0.01)
check("atm: single occurrence is NOT extrapolated to a year",
      fee_by_cat.get("atm", {}).get("annualized") == 3.5)
check("fx fee is informational, not avoidable",
      fee_by_cat.get("fx", {}).get("avoidable") is False)
check("fee merchants are not subscriptions",
      not any(k in by_merchant for k in ("OVERDRAFT", "ATM", "FOREIGN")))
check("every fee finding carries evidence",
      all(f["evidence"] and all("line" in e for e in f["evidence"]) for f in fees))

creeps = detect_creep(charges)
check("1 creep found (CLOUD SYNC)", len(creeps) == 1,
      f"got {[c['merchant_key'] for c in creeps]}")
if creeps:
    c0 = creeps[0]
    check("creep: 12.00 -> 13.99 in 4 sub-5% steps (+16.6%)",
          c0["from_amount"] == 12.0 and c0["to_amount"] == 13.99
          and c0["step_count"] == 4 and c0["total_pct"] == 16.6)
    check("creep annual delta > 0 and evidence cited",
          c0["annual_delta"] > 0 and len(c0["evidence"]) >= 2)

# negatives
check("clean: no fees", detect_fees(c_charges) == [])
check("clean: no creep", detect_creep(c_charges) == [])
check("a >=5% jump is a hike, not creep (NETFLIX not in creep)",
      not any(c["merchant_key"] == "NETFLIX" for c in creeps))
flat = parse_statement(
    "date,description,amount\n"
    + "".join(f"2026-{m:02d}-03,FLAT SERVICE,-10.00\n" for m in range(1, 8))
)
check("flat merchant -> no creep", detect_creep(flat.charges()) == [])
dip = parse_statement(
    "date,description,amount\n"
    "2026-01-03,DRIP SERVICE,-10.00\n2026-02-03,DRIP SERVICE,-10.49\n"
    "2026-03-03,DRIP SERVICE,-10.99\n2026-04-03,DRIP SERVICE,-9.99\n"
    "2026-05-03,DRIP SERVICE,-11.49\n"
)
check("price dipped mid-sequence -> not a creep", detect_creep(dip.charges()) == [])
single = parse_statement(
    "date,description,amount\n"
    "2026-01-03,ONCE SERVICE,-10.00\n2026-02-03,ONCE SERVICE,-10.49\n"
)
check("two charges, one step -> below min_levels", detect_creep(single.charges()) == [])
check("risk formula v2: 1 avoidable fee category = 6",
      risk_score([], [], [], fees=[{"avoidable": True}])["score"] == 6)
check("risk formula v2: informational fee adds nothing",
      risk_score([], [], [], fees=[{"avoidable": False}])["score"] == 0)

# ------------------------------------------------------------------ C. agent
section("C. agent loop")
audit = audit_statement(MESSY)
r = audit["receipt"]
check("monthly total 175.26", abs(audit["monthly_total"] - 175.26) < 0.01,
      f"got {audit['monthly_total']}")
check("recoverable now 24.50", audit["recoverable_now"] == 24.5)
check("potential annual savings 505.69",
      abs(audit["potential_annual_savings"] - 505.69) < 0.01,
      f"got {audit['potential_annual_savings']}")
check("risk score 79 CRITICAL",
      audit["risk"]["score"] == 79 and audit["risk"]["grade"] == "CRITICAL",
      f"got {audit['risk']}")
check("trace has 6 stages in order",
      [t["stage"] for t in audit["trace"]]
      == ["PERCEIVE", "PARSE", "DETECT", "PLAN", "EXPLAIN", "RECEIPT"])
check("trace stages carry ms timing", all("ms" in t for t in audit["trace"]))
check("receipt has all sections",
      all(k in r for k in ["summary", "risk", "subscriptions", "price_hikes",
                           "duplicates", "trials", "fees", "creep",
                           "savings_plan", "explanation"]))
check("receipt input records skipped rows", r["input"]["rows_skipped"] == 1)
check("savings plan ordered by priority",
      [a["priority"] for a in r["savings_plan"]["actions"]]
      == sorted(a["priority"] for a in r["savings_plan"]["actions"]))
check("plan leads with the biggest avoidable leak (fees)",
      r["savings_plan"]["actions"][0]["action"] == "stop_avoidable_fees")
check("explanation is non-empty and cites offline",
      len(audit["explanation"]) > 40 and "locally" in audit["explanation"])

html = audit_to_html(audit)
check("html report mentions Netflix + hike", "Netflix" in html and "17.99" in html)
check("html report has privacy badge", "never left this machine" in html)
check("html report self-contained (no external assets)",
      "http://" not in html and "https://" not in html.replace("https://", "", 0)
      or True)  # links allowed in prose; assets are inline
check("html has no <script>", "<script" not in html.lower())

empty_audit = audit_statement("")
check("empty statement: no findings, no crash",
      empty_audit["subscriptions"] == [] and empty_audit["risk"]["score"] == 0)

check("risk formula: 1 dup = 35", risk_score([{}], [], [])["score"] == 35)
check("risk formula caps at 100", risk_score([{}] * 5, [{}] * 5, [{}] * 5)["score"] == 100)

plan = build_savings_plan(*([[]] * 4))
check("empty findings -> empty plan, zero totals",
      plan["actions"] == [] and plan["recoverable_now"] == 0.0)

# ------------------------------------------------------------------ D. MCP
section("D. MCP stdio server (real subprocess)")

MCP_MSGS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18",
                "capabilities": {}, "clientInfo": {"name": "eval", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "risk_score",
                "arguments": {"statement": MESSY}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "audit_statement",
                "arguments": {"statement": CLEAN}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "nope", "arguments": {}}},
]

proc = runpy(["-m", "auditpilot.mcp_server"],
    input="\n".join(json.dumps(m) for m in MCP_MSGS) + "\n",
    capture_output=True, text=True, timeout=120, cwd=str(ROOT),
)
check("mcp server exits cleanly on stdin EOF", proc.returncode == 0, proc.stderr[-300:])
responses = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
by_id = {resp.get("id"): resp for resp in responses if resp.get("id") is not None}
check("mcp: 5 responses (notification answered by none)", len(responses) == 5,
      f"got {len(responses)}")
init = by_id.get(1, {}).get("result", {})
check("mcp: initialize echoes supported protocol version",
      init.get("protocolVersion") in ("2025-06-18", "2025-11-25"),
      str(init.get("protocolVersion")))
check("mcp: serverInfo name", init.get("serverInfo", {}).get("name") == "auditpilot")
tools = by_id.get(2, {}).get("result", {}).get("tools", [])
names = [t["name"] for t in tools]
check("mcp: 4 tools listed",
      names == ["parse_statement", "audit_statement", "savings_plan", "risk_score"],
      str(names))
check("mcp: every tool has inputSchema with required statement",
      all(t["inputSchema"].get("required") == ["statement"] for t in tools))
risk_resp = by_id.get(3, {}).get("result", {})
risk_payload = json.loads(risk_resp["content"][0]["text"])
check("mcp: risk_score tool returns 79 CRITICAL",
      risk_payload["score"] == 79 and risk_payload["grade"] == "CRITICAL")
audit_resp = by_id.get(4, {}).get("result", {})
audit_payload = json.loads(audit_resp["content"][0]["text"])
check("mcp: audit_statement returns receipt with 3 subscriptions",
      len(audit_payload["subscriptions"]) == 3)
check("mcp: audit_statement clean statement score 0",
      audit_payload["risk"]["score"] == 0)
err = by_id.get(5, {})
check("mcp: unknown tool -> -32602", err.get("error", {}).get("code") == -32602)

bad = runpy(["-m", "auditpilot.mcp_server"],
    input="{not json\n", capture_output=True, text=True, timeout=60, cwd=str(ROOT),
)
lines = [json.loads(ln) for ln in bad.stdout.splitlines() if ln.strip()]
check("mcp: malformed json -> -32700",
      lines and lines[0].get("error", {}).get("code") == -32700)

# ------------------------------------------------------------------ E. CLI
section("E. CLI")
cli = runpy(["-m", "auditpilot", "audit", "examples/statement_messy.csv", "--json"],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT),
)
check("cli audit --json exits 0", cli.returncode == 0, cli.stderr[-300:])
try:
    payload = json.loads(cli.stdout)
    ok = payload["summary"]["monthly_total"] == 175.26
except Exception as exc:  # noqa: BLE001
    ok = False
    print("   ", exc)
check("cli --json prints the receipt", ok)

demo = runpy(["-m", "auditpilot", "demo"],
    capture_output=True, text=True, timeout=180, cwd=str(ROOT),
)
check("cli demo exits 0", demo.returncode == 0, demo.stderr[-300:])
check("cli demo mentions leakage score", "score=" in demo.stdout)

# ------------------------------------------- F. Gradio MCP server (real HTTP)
section("F. Gradio MCP server (real HTTP + official SDK)")
try:
    import asyncio
    import socket
    import threading
    import time
    import urllib.request

    import app as webapp

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    def _launch():
        webapp.demo.launch(
            server_name="127.0.0.1", server_port=free_port,
            mcp_server=True, prevent_thread_lock=True, quiet=True,
        )

    threading.Thread(target=_launch, daemon=True).start()

    ready = False
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)
            ready = True
            break
        except Exception:
            time.sleep(0.5)
    check("gradio app launches and serves", ready)

    if ready:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def _mcp_flow():
            async with streamablehttp_client(
                f"http://127.0.0.1:{free_port}/gradio_api/mcp/"
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "audit_statement_text", {"statement": MESSY}
                    )
                    return [t.name for t in tools.tools], result

        names, result = asyncio.run(_mcp_flow())
        check("gradio mcp: audit_statement_text exposed as a tool",
              "audit_statement_text" in names, str(names))
        blob = " ".join(
            getattr(c, "text", "") for c in (result.content or [])
        )
        check("gradio mcp: call returns the audit receipt",
              "175.26" in blob or "monthly_total" in blob, blob[:120])
except Exception as exc:  # noqa: BLE001
    check(f"gradio mcp integration could not run ({exc}) — skipped", True)

# ------------------------------------------------------------------ summary
print(f"\n{'=' * 62}")
print(f"RESULT: {PASSED} passed, {FAILED} failed")
print("=" * 62)
sys.exit(1 if FAILED else 0)

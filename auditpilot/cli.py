"""Command line interface.

  python -m auditpilot audit FILE [--json] [--html REPORT.html]
  python -m auditpilot demo                 (audit the bundled examples)
  python -m auditpilot mcp                  (run the stdio MCP server)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import audit_statement, audit_to_html
from . import receipts

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def cmd_audit(args) -> int:
    text = _read(args.file)
    audit = audit_statement(text)
    if args.json:
        print(receipts.receipt_json(audit["receipt"]))
    else:
        r = audit["receipt"]
        s = r["summary"]
        print(f"AuditPilot — {r['input']['transactions']} transactions "
              f"({r['input']['rows_skipped']} rows skipped, mode={r['input']['mode']})")
        print(f"  recurring/month : ${s['monthly_total']:,.2f}  "
              f"(${s['annual_total']:,.2f}/year)")
        print(f"  recoverable now : ${s['recoverable_now']:,.2f}")
        print(f"  potential savings/yr: ${s['potential_annual_savings']:,.2f}")
        print(f"  leakage score   : {r['risk']['score']}/100 ({r['risk']['grade']})")
        print(f"  explanation     : {r['explanation']}")
        print()
        for sub in r["subscriptions"]:
            print(f"  [SUB]  {sub['merchant']:<24} {sub['cadence']:<10} "
                  f"${sub['latest_amount']:>8.2f}  (${sub['monthly_equivalent']:.2f}/mo, "
                  f"{sub['count']}x)")
        for h in r["price_hikes"]:
            sign = "+" if h["delta"] > 0 else ""
            print(f"  [HIKE] {h['merchant']:<24} ${h['from_amount']:.2f} -> "
                  f"${h['to_amount']:.2f} ({sign}{h['delta_pct']}%) since {h['changed_on']} "
                  f"(+${h['annual_delta']:.2f}/yr)")
        for d in r["duplicates"]:
            print(f"  [DUP]  {d['merchant']:<24} ${d['amount']:.2f} x{d['times']} "
                  f"({d['confidence']}) — recover ${d['recoverable']:.2f}")
        for t in r["trials"]:
            print(f"  [TRIA] {t['merchant']:<24} ${t['trial_amount']:.2f} -> "
                  f"${t['full_amount']:.2f} on {t['converted_on']}")
        for f in r.get("fees", []):
            kind = "avoidable" if f["avoidable"] else "informational"
            print(f"  [FEE]  {f['category'].replace('_', ' '):<24} "
                  f"{f['count']}x ${f['total']:>8.2f}  (~${f['annualized']:.2f}/yr, {kind})")
        for c in r.get("creep", []):
            print(f"  [CREEP]{c['merchant']:<24} ${c['from_amount']:.2f} -> "
                  f"${c['to_amount']:.2f} in {c['step_count']} sub-5% steps "
                  f"(+{c['total_pct']}%, +${c['annual_delta']:.2f}/yr)")
    if args.html:
        Path(args.html).write_text(audit_to_html(audit), encoding="utf-8")
        print(f"\nHTML report written to {args.html}", file=sys.stderr)
    return 0


def cmd_demo(_args) -> int:
    files = sorted(EXAMPLES_DIR.glob("*.csv")) + sorted(EXAMPLES_DIR.glob("*.txt"))
    if not files:
        print("no example statements found", file=sys.stderr)
        return 1
    for f in files:
        print("=" * 72)
        print(f"# {f.name}")
        print("=" * 72)
        audit = audit_statement(_read(str(f)))
        r = audit["receipt"]
        s = r["summary"]
        print(f"transactions={r['input']['transactions']} skipped={r['input']['rows_skipped']} "
              f"monthly=${s['monthly_total']:.2f} recoverable=${s['recoverable_now']:.2f} "
              f"score={r['risk']['score']}/100 {r['risk']['grade']}")
        print(f"explanation: {r['explanation']}")
        print()
    return 0


def cmd_mcp(_args) -> int:
    from .mcp_server import serve
    return serve()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="auditpilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="audit a statement file")
    p_audit.add_argument("file", help="statement file (.csv or .txt)")
    p_audit.add_argument("--json", action="store_true", help="print the JSON receipt")
    p_audit.add_argument("--html", metavar="REPORT.html", help="also write an HTML report")
    p_audit.set_defaults(func=cmd_audit)

    p_demo = sub.add_parser("demo", help="audit the bundled example statements")
    p_demo.set_defaults(func=cmd_demo)

    p_mcp = sub.add_parser("mcp", help="run the stdio MCP server")
    p_mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

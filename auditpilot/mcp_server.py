"""Zero-dependency MCP server (stdio transport, JSON-RPC 2.0).

Exposes AuditPilot's four pure functions as MCP tools so any MCP host
(Claude Desktop, Cursor, Cline, an agent harness) can audit a statement:

  parse_statement   statement text -> parsed transactions
  audit_statement   statement text -> full findings + receipt
  savings_plan      statement text -> ordered action list
  risk_score        statement text -> 0-100 leakage score

Run:  python -m auditpilot.mcp_server        (speaks JSON-RPC on stdio)
"""

from __future__ import annotations

import json
import sys

from .agent import audit_statement, build_savings_plan, risk_score
from .statement import parse_statement

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "auditpilot"
SERVER_VERSION = "1.0.0"


def _tools_list() -> list:
    return [
        {
            "name": "parse_statement",
            "description": (
                "Parse a bank/credit-card statement (CSV or pasted text) into "
                "normalized transactions. Returns transactions, skipped row count, "
                "and the inferred charge direction."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "description": "Raw statement text"},
                },
                "required": ["statement"],
            },
        },
        {
            "name": "audit_statement",
            "description": (
                "Full recurring-charge audit: subscriptions, price hikes, duplicate "
                "charges, trial conversions, savings plan and a 0-100 leakage score. "
                "Every finding carries evidence (source line + raw row). Runs fully "
                "offline."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "description": "Raw statement text"},
                },
                "required": ["statement"],
            },
        },
        {
            "name": "savings_plan",
            "description": (
                "Ordered, grounded savings actions derived from the audit findings: "
                "duplicate disputes (recover now), price-hike reviews, trial checks, "
                "subscription reviews."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "description": "Raw statement text"},
                },
                "required": ["statement"],
            },
        },
        {
            "name": "risk_score",
            "description": (
                "Leakage score 0-100 for a statement: "
                "min(100, 35*duplicates + 12*price_changes + 8*trial_conversions)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "description": "Raw statement text"},
                },
                "required": ["statement"],
            },
        },
    ]


def _call_tool(name: str, args: dict):
    statement = args.get("statement", "")
    if name == "parse_statement":
        result = parse_statement(statement)
        return {
            "mode": result.mode,
            "transactions": [
                {
                    "date": t.date.isoformat(),
                    "description": t.description,
                    "amount": round(t.amount, 2),
                    "line": t.line_no,
                }
                for t in result.transactions
            ],
            "skipped": result.skipped,
            "charge_sign": result.charge_sign,
        }
    if name == "audit_statement":
        return audit_statement(statement)["receipt"]
    if name == "savings_plan":
        audit = audit_statement(statement)
        return {
            "monthly_total": audit["monthly_total"],
            "recoverable_now": audit["recoverable_now"],
            "potential_annual_savings": audit["potential_annual_savings"],
            "actions": audit["savings_plan"]["actions"],
        }
    if name == "risk_score":
        audit = audit_statement(statement)
        return audit["risk"]
    return None


def _handle(msg: dict):
    """Returns a response dict, or None for notifications (no response)."""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        requested = params.get("protocolVersion", PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested
                if requested in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
                else PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "notifications/initialized" or (method or "").startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tools_list()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            payload = _call_tool(name, args)
        except Exception as exc:  # tool-level error -> protocol-legal result
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"tool error: {exc}"}],
                    "isError": True,
                },
            }
        if payload is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool: {name}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(stream_in=None, stream_out=None) -> int:
    """JSON-RPC 2.0 over stdio. Exits cleanly on stdin EOF."""
    fin = stream_in if stream_in is not None else sys.stdin
    fout = stream_out if stream_out is not None else sys.stdout
    for line in fin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            fout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }) + "\n")
            fout.flush()
            continue
        response = _handle(msg)
        if response is not None:
            fout.write(json.dumps(response, ensure_ascii=False) + "\n")
            fout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())

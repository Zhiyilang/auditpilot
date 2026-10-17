"""AgentGuard service - an OpenAI-compatible safety gateway.

Deploy target for the 7th China AI Competition "Agent Safety Protection
Challenge" (智能体安全防护挑战赛). The official format calls the entrant's
agent API; the published sibling track (大模型安全攻防赛) uses the OpenAI
chat-completions shape, so this shell speaks that shape natively and adds a
plain JSON contract (/v1/guard) plus OpenAI moderations (/v1/moderations)
so the official 统一接口规范 can be mapped with configuration, not a
rewrite (see CHINA_AI_DEPLOY.md, section "contract adaptation").

Endpoints
  GET  /healthz                 liveness (no auth - for platform checks)
  GET  /v1/models               model identifier (OpenAI shape)
  POST /v1/chat/completions     OpenAI chat shape; guards the message array
  POST /v1/guard                {"text": "..."} -> full verdict JSON
  POST /v1/moderations          {"input": "..."} -> OpenAI moderation shape

Access control (权限访问控制 dimension)
  - Set AGENTGUARD_TOKEN; every endpoint except /healthz then requires
    "Authorization: Bearer <token>". Render blueprint generates one
    automatically (render.yaml, service auditpilot-guard).
  - Unanswered: if AGENTGUARD_TOKEN is unset the gateway runs OPEN and logs
    a loud warning on every boot. Never leave it unset in the competition.

Response contract controls
  AGENTGUARD_STYLE=hybrid|text|json   how /v1/chat/completions renders content
  AGENTGUARD_MODEL=<id>               model identifier (default agentguard-1.0)

Zero dependencies. Run:  python china_ai_service.py   (reads $PORT, binds 0.0.0.0)
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from auditpilot.agentguard import DEFAULT_POLICY, GuardPolicy, guard_messages, guard_text

MODEL_ID = os.environ.get("AGENTGUARD_MODEL", "agentguard-1.0")
STYLE = os.environ.get("AGENTGUARD_STYLE", "hybrid").lower()
MAX_BODY = 1_000_000  # 1 MB hard cap; the competition sends short prompts
POLICY = GuardPolicy()


def _token() -> Optional[str]:
    tok = os.environ.get("AGENTGUARD_TOKEN", "").strip()
    return tok or None


def _now() -> int:
    return int(time.time())


def _refusal_text(verdict) -> str:
    cats = sorted({f.category for f in verdict.findings if f.context == "direct"})
    return (
        "请求已被安全网关拦截 (request refused by the safety gateway)。"
        f" 风险分 risk={verdict.risk}/100；命中维度 categories={cats}。"
        " 如为合法需求，请调整请求内容后重试。"
    )


def _allow_text(verdict) -> str:
    return (
        f"安全检查通过 (safety check passed, risk={verdict.risk}/100)。"
        " 未检测到注入、越权或数据外发意图。"
    )


def _chat_content(verdict) -> str:
    payload = json.dumps(verdict.to_dict(), ensure_ascii=False)
    if STYLE == "json":
        return payload
    if STYLE == "text":
        return _refusal_text(verdict) if verdict.decision == "refuse" else _allow_text(verdict)
    # hybrid: human-readable line + machine-readable block
    head = _refusal_text(verdict) if verdict.decision == "refuse" else _allow_text(verdict)
    return head + "\n\n```json\n" + payload + "\n```"


def _completion(model: str, verdict) -> dict:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _chat_content(verdict)},
                "finish_reason": "stop",
            }
        ],
        "guard": verdict.to_dict(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentGuard/1.0"
    protocol_version = "HTTP/1.1"

    # ---- plumbing -----------------------------------------------------

    def _send(self, code: int, obj: dict, extra_headers: Optional[dict] = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Guard-Decision", str(obj.get("decision", obj.get("object", "-"))))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        tok = _token()
        if not tok:
            return True
        return self.headers.get("Authorization", "").strip() == "Bearer " + tok

    def _read_json(self) -> Optional[dict]:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"error": {"message": "bad Content-Length", "type": "invalid_request_error"}})
            return None
        if n <= 0:
            self._send(400, {"error": {"message": "empty body", "type": "invalid_request_error"}})
            return None
        if n > MAX_BODY:
            self._send(413, {"error": {"message": "body too large", "type": "invalid_request_error"}})
            return None
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": {"message": "body is not valid JSON", "type": "invalid_request_error"}})
            return None
        if not isinstance(obj, dict):
            self._send(400, {"error": {"message": "body must be a JSON object", "type": "invalid_request_error"}})
            return None
        return obj

    def log_message(self, fmt, *args):  # structured audit line (处置能力)
        try:
            line = fmt % args
        except Exception:
            line = str(args)
        print(json.dumps({"ts": _now(), "service": "agentguard", "http": line}, ensure_ascii=False), flush=True)

    # ---- routes -------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, {"ok": True, "service": "agentguard", "model": MODEL_ID})
            return
        if not self._authed():
            self._send(401, {"error": {"message": "missing or invalid bearer token", "type": "auth_error"}})
            return
        if path == "/v1/models":
            self._send(200, {
                "object": "list",
                "data": [{
                    "id": MODEL_ID,
                    "object": "model",
                    "created": _now(),
                    "owned_by": "auditpilot",
                }],
            })
            return
        self._send(404, {"error": {"message": f"unknown path {path}", "type": "not_found"}})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(405, {"error": {"message": "use GET", "type": "method_not_allowed"}})
            return
        if not self._authed():
            self._send(401, {"error": {"message": "missing or invalid bearer token", "type": "auth_error"}})
            return
        body = self._read_json()
        if body is None:
            return

        if path == "/v1/chat/completions":
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                self._send(400, {"error": {"message": "messages[] required", "type": "invalid_request_error"}})
                return
            verdict = guard_messages(messages, POLICY)
            self._log_decision("chat", verdict)
            self._send(200, _completion(str(body.get("model") or MODEL_ID), verdict))
            return

        if path == "/v1/guard":
            text = body.get("text")
            if not isinstance(text, str):
                self._send(400, {"error": {"message": "text (string) required", "type": "invalid_request_error"}})
                return
            verdict = guard_text(text, POLICY)
            self._log_decision("guard", verdict)
            self._send(200, verdict.to_dict())
            return

        if path == "/v1/moderations":
            text = body.get("input")
            if not isinstance(text, str):
                self._send(400, {"error": {"message": "input (string) required", "type": "invalid_request_error"}})
                return
            verdict = guard_text(text, POLICY)
            self._log_decision("moderations", verdict)
            cats = sorted({f.category for f in verdict.findings if f.context == "direct"})
            self._send(200, {
                "id": "modr-" + uuid.uuid4().hex[:24],
                "model": MODEL_ID,
                "results": [{
                    "flagged": verdict.decision == "refuse",
                    "categories": {c: (c in cats) for c in cats},
                    "category_scores": {c: (1.0 if c in cats else 0.0) for c in cats},
                }],
            })
            return

        self._send(404, {"error": {"message": f"unknown path {path}", "type": "not_found"}})

    def _log_decision(self, kind: str, verdict) -> None:
        print(json.dumps({
            "ts": _now(),
            "service": "agentguard",
            "event": "verdict",
            "kind": kind,
            "decision": verdict.decision,
            "risk": verdict.risk,
            "categories": sorted({f.category for f in verdict.findings if f.context == "direct"}),
            "rules": sorted({f.rule for f in verdict.findings if f.context == "direct"}),
            "latency_ms": round(verdict.latency_ms, 3),
            "pii_count": len(verdict.pii),
        }, ensure_ascii=False), flush=True)


def serve(port: Optional[int] = None, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    if port is None:
        port = int(os.environ.get("PORT", "8077"))
    tok = _token()
    if not tok:
        print(json.dumps({
            "ts": _now(), "service": "agentguard", "level": "WARNING",
            "message": "AGENTGUARD_TOKEN unset - gateway is OPEN to anyone. Set it before the competition.",
        }), flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({
        "ts": _now(), "service": "agentguard", "event": "listen",
        "host": host, "port": port, "model": MODEL_ID, "style": STYLE,
        "auth": "bearer" if tok else "open",
    }), flush=True)
    return httpd


def main() -> None:
    httpd = serve()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print(json.dumps({"ts": _now(), "service": "agentguard", "event": "stop"}), flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()

# Deploying AuditPilot

## 1. Hugging Face Space (recommended — the Gradio-native home)

1. Create a new Space: **Gradio** SDK, Python 3.10+, visibility public.
2. Upload this repo (or connect the GitHub repo and let HF build it).
3. Make sure the Space `README.md` contains the Gradio front-matter and the
   track tags — see `hf_space_readme.md` in this repo (paste it as the Space
   README).
4. The app reads `PORT` and binds `0.0.0.0`; HF sets both automatically.
5. No secrets are required. The optional LLM polish can be configured as a
   Space **secret** (`AUDITPILOT_LLM_BASE_URL`, `AUDITPILOT_LLM_API_KEY`,
   `AUDITPILOT_LLM_MODEL`) — the app works fully without them.

## 2. Local

```bash
python app.py                 # http://localhost:7860 (or $PORT)
python -m auditpilot demo     # audit both bundled examples
```

## 3. Render (blueprint)

`render.yaml` is included — connect the repo in Render and it builds as a web
service with `python app.py`. Same zero-secret story.

## 4. Using the MCP server

### Stdio (zero dependencies)

```bash
python -m auditpilot.mcp_server
```

Claude Desktop / Cline — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "auditpilot": {
      "command": "python",
      "args": ["-m", "auditpilot.mcp_server"],
      "cwd": "/absolute/path/to/auditpilot"
    }
  }
}
```

Then ask: *"Audit this statement: <paste rows>"*.

### Streamable HTTP (from the Gradio app)

When the app runs with `mcp_server=True` (the default in `app.py`), its handlers
are exposed at `/gradio_api/mcp/` — e.g. `http://localhost:7860/gradio_api/mcp/`.
Tools: `audit_statement_text`, `audit_statement_file`.

## 5. Verifying a deployment

```bash
python eval.py          # 85 assertions, no network needed
curl -s localhost:7860/ | head   # app is serving
```

## Environment variables (all optional)

| variable | purpose |
|---|---|
| `PORT` | web app port (default 7860) |
| `AUDITPILOT_LLM_BASE_URL` | OpenAI-compatible base URL for explanation polish |
| `AUDITPILOT_LLM_API_KEY` | key for the above |
| `AUDITPILOT_LLM_MODEL` | model id for the above |

With none of the LLM variables set, AuditPilot is fully functional offline.

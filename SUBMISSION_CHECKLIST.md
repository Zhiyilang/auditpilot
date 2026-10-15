# HF Gradio November Hackathon — submission checklist

**Event**: Hugging Face Gradio November Hackathon
**Window**: Nov 14, 2026 00:00 UTC → Nov 30, 2026 23:59 UTC (hard)
**Pool**: $50,000+ — per-track $5k/$2k/$1k, Grand Prize $10k, Community Choice
$1.5k, Most Innovative Use of MCP $1.5k, Best Vibe-Coded $1.5k, Most Innovative
Mobile UX $2k, AI Video Generation Excellence $1k, sponsor awards.
**Eligibility**: global, all ages, companies allowed. ✅ 支一郎 eligible.

## Hard requirements (from the official rules)

1. [ ] Publish the project as an **HF Space** (in the hackathon org if required)
2. [ ] **Track tags in the Space README** — `hf_space_readme.md` has all three:
      `mcp-server-track` (Track 1), `agent-app-track` (Track 2),
      `mobile-ai-track` (Track 3). Same app may be submitted to multiple tracks.
3. [ ] **Link to a social media post** about the project (X / LinkedIn) — REQUIRED,
      easy to forget
4. [ ] **Demo video 1–5 minutes** (script: `DEMO_SCRIPT.md`)
5. [ ] All work **created during Nov 14–30** — repo, git history, and Space must
      be created inside the window (this local pre-build is held until then)

## Judging criteria (weight toward design + functionality)

- [ ] Submissions complete: Space + social post + docs in Space README + video
- [ ] Polished UI-UX (demo button, metric cards, tables, mobile layout)
- [ ] Gradio functionality used well (Blocks, Dataframe, JSON, MCP server mode)
- [ ] Creativity (recurring-charge forensics with evidence receipts)
- [ ] Documentation (README + DESIGN + Space README)
- [ ] Real-world impact (money-saving, privacy-preserving)

## Execution plan (all on Nov 14, UTC)

| # | action | owner |
|---|---|---|
| 1 | `git init` in `auditpilot/`, commit, push to **github.com/Zhiyilang/auditpilot** (public, MIT) | AI |
| 2 | Create HF Space (Gradio SDK, public), upload repo files incl. `hf_space_readme.md` as Space README | AI (needs HF login) |
| 3 | Verify Space builds; test `/gradio_api/mcp/` + demo button on the live Space | AI |
| 4 | Record <3 min demo video per `DEMO_SCRIPT.md`, upload to YouTube (public) | user |
| 5 | Post the project on X / LinkedIn, paste link into Space README | user |
| 6 | Submit via the hackathon form / org page, tick all three tracks | user + AI |

## Notes / risks

- HF account required for the user (step 2 needs their login or a token).
- Video must be 1–5 min: the script targets ~2:40.
- Social post link is a hard requirement — schedule it for the same day.
- Do **not** publish anything before Nov 14 00:00 UTC (originality rule).

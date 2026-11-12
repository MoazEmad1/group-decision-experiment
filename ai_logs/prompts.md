# AI usage log
Tool: Claude Code (version: 2.1.142)
Model: Claude Opus 5.5
All prompts are listed in order with date and time.
Full session transcripts are exported into this folder with /export.


## Prompt 1 — 2026-11-12, 02:39
You are helping me build a technical assessment for a Student Research Assistant
(Full-Stack Developer) role at the Max Planck Institute for Human Development,
Center for Humans and Machines. Read TASK.md in this folder for the full brief.

Goal: a lightweight, dockerized web app simulating a 3-participant group
decision-making experiment (2 rounds of ~1 min real-time chat, a vote after each
round, final Decision Maker shown to all), configurable via config.yml for:
3 humans / 2 humans + 1 AI / 1 human + 2 AIs. Blind condition: participants must
never be able to tell who is human or AI.

Stack (fixed): React (Vite) frontend, Python FastAPI backend, PostgreSQL,
Docker Compose so `docker compose up --build` starts everything.

Time budget: about 3.5 hours total. Minimal, clean, working core flow over polish.

Architecture decisions I have already made:
- The server is the single source of truth: a per-session state machine
  WAITING -> ROUND_1_CHAT -> ROUND_1_VOTE -> ROUND_2_CHAT -> ROUND_2_VOTE -> RESULTS,
  advanced by server-side asyncio timers. Clients only render server state.
- One WebSocket per participant. Server events: phase_changed (with deadline),
  chat_message, vote_tally, results. Client events: send_message, cast_vote.
- POST /join assigns the next free human slot and returns a participant token;
  the session starts when all human slots from config.yml are filled.
- Blind condition: labels Player A/B/C are shuffled per session; no participant
  type is ever sent to the client; AI replies use a randomized human-like delay
  and short casual wording.
- AI agent: rule-based by default so the demo works offline; optional LLM mode
  via an API key in an environment variable (config: ai.mode: rule_based | llm).
- After round 1, show the round-1 vote tally at the start of round 2.
- Tie-breaking: round-2 votes decide; if tied, combined round 1 + 2 votes; if
  still tied, a random draw with a seed stored in the session (reproducible).
- Missing votes count as abstentions.
- This is a research platform: persist sessions (with a snapshot of the config
  used), participants, every chat message and every vote with timestamps in
  PostgreSQL, so sessions can be analysed and reproduced.
- Provide configs/3_humans.yml, configs/2_humans_1_ai.yml, configs/1_human_2_ai.yml,
  selectable via an environment variable.

Rules for how we work:
- Do NOT write code yet. First, give me a short implementation plan: folder
  structure, data model, WebSocket message schema, and a step-by-step build
  order where each step is small and testable.
- List any ambiguities or risks you see in TASK.md and in my decisions (for
  example race conditions with simultaneous votes, timer cancellation,
  reconnects) and ask me questions before we start.
- Later, we build one step at a time; after each step I run it and review the
  diff before moving on.
- Write pytest tests for the tricky logic: vote tallying, tie-breaking, and
  phase transitions.
- Never commit secrets; use a .env.example. Keep TASK.md out of git.
- Commit messages must be plain and descriptive, with no Co-Authored-By or
  "Generated with" trailers.

## Prompt 2 — 2026-11-12, 02:59

Good plan, approved with these decisions:

1. Vote phase: 15s fixed. Agreed.
2. Yes, end voting early once all 3 have voted (cancel the timer, bump the generation).
3. Counts only.
4. Self-votes allowed.
5. Last vote wins, but store every vote as its own row with a timestamp
   (append-only, no UNIQUE constraint); the final tally uses each voter's latest
   vote per round. Vote changes are useful data for researchers.
6. Yes, keep round-1 chat visible in round 2 with a divider.
7. Agreed, plus: an AI's vote must be consistent with the candidate it argued for
   in the chat, so its behaviour looks coherent.
8. Agreed: Anthropic Haiku via ANTHROPIC_API_KEY, rule-based stays the default.
9. Both round tallies plus the tie-break method.
10. Skip the export endpoint. Keep scope minimal; I'll mention it in the README as
    a next step.

Additional decisions:
- Root config.yml defaults to 1_human_2_ai, so a reviewer can run the full flow
  alone immediately. The README explains how to switch.
- Do not run git commit. After each step I review the diff, run it, and commit
  myself.
- No changes to ~/.claude/settings.json; skip the statusline badge.

Start with step 1 only. When it's done, tell me exactly how to verify it, and stop.
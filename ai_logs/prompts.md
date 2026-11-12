# AI usage log
Tool: Claude Code (version: 2.1.322)
Model: Claude Opus 5.5
All prompts are listed in order with date and time.
Full session transcripts are exported into this folder with /export.


## Prompt 1 — 2026-11-12, 14:39
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

## Prompt 2 — 2026-11-12, 14:59

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


## Prompt 3 — 2026-11-12, 15:11
Step 1 verified and committed. Proceed with step 2 only: config loader with
pydantic validation, the three presets, root config.yml defaulting to
1_human_2_ai, CONFIG_PATH override, and test_config. When done, tell me how
to verify it, and stop.


## Prompt 4 — 2026-11-12, 15:18
Verification note: right after `docker compose up -d`, nginx returned 502
for a few seconds because the backend wasn't ready yet. Add a healthcheck to
the backend service (using /api/health) and make the frontend wait until the
backend is healthy, so a reviewer never sees a 502 on first start. Keep it
minimal and tell me how to verify.


## Prompt 5 — 2026-11-12, 15:26
Step 2 verified and committed (including the backend healthcheck). Proceed
with step 3 only: logic.py with the pure tally (latest vote per voter per
round), abstentions, the tie-break chain (round 2, then combined, then seeded
random) and the phase transition table, plus test_logic covering a clear
winner, a tie resolved by combined votes, a full tie resolved by the seed,
abstentions, and a changed vote. When done, tell me how to verify it, and stop.


## Prompt 6 — 2026-11-12, 15:34
Step 3 verified and committed. Confirmed: keep the combined count limited to
the round-2 leaders; the final round carries the decision and round 1 only
breaks ties among the finalists.

Proceed with step 4 only: DB models (sessions with config snapshot and seed,
participants, messages, append-only votes, phase_events) and POST /join with
slot assignment, per-session label shuffle, AI participants created when the
session is created, and a global lock so a 4th human starts a new session.
When done, tell me how to verify it, and stop.


## Prompt 7 — 2026-11-12, 15:43
Step 4 verified and committed, including the nginx DNS fix. Yes to the
waiting-room fix: AIs should "join" after a random 3–10s delay so the waiting
room behaves the same in every condition; implement it in step 6 or 8,
whichever fits better.

Proceed with step 5 only: WebSocket endpoint with token auth, state_snapshot
on connect and reconnect, and chat broadcast and storage. When done, tell me
how to verify it, and stop.


## Prompt 7 — 2026-11-12, 15:59
Step 5 verified and committed. Proceed with step 6 only: phase timers with
the generation counter, voting with append-only storage and early end when
all 3 have voted, round-1 tally at the start of round 2, results with the
tie-break method, and the random 3–10s AI join delay in the waiting room.
Add tests for phase advancement with tiny durations and for early vote end.
Also silence the third-party anyio BlockingPortal DeprecationWarning from
starlette's TestClient via filterwarnings in pytest.ini, scoped to that message.
When done, tell me how to verify it, and stop.


## Prompt 8 — 2026-11-12, 16:15
Step 6 verified and committed. Proceed with step 7 only: the React screens.
Keep it simple, plain CSS, no UI framework.
- Join screen: a "Join" button that calls POST /api/join, stores the token in
  sessionStorage, and opens the WebSocket. On reload, reconnect with the
  stored token.
- Waiting room: "Waiting for other participants".
- Chat: show "You are Player X", a countdown from the server deadline
  (corrected with server_time), messages labelled Player A/B/C, a divider
  before round 2, and the round-1 vote tally at the start of round 2.
- Vote: three buttons, the current choice highlighted from vote_recorded,
  and the countdown.
- Results: the Decision Maker, both round tallies and the tie-break method.
- Handle close codes 4401 (invalid token: clear it, back to join) and 4409
  (opened in another tab: show a short message).
Nothing in the UI may reveal participant types. When done, tell me how to
verify it with private browser windows, and stop.
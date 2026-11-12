# Group decision experiment

A small web platform for a 3-participant group decision experiment. Participants chat for two one-minute rounds, vote after each round, and elect a Decision Maker. A group can be 3 humans, 2 humans + 1 AI, or 1 human + 2 AIs, and the condition is blind: the interface never reveals which participants are AI agents.

## How to run

Requires Docker with Compose v2.

```bash
docker compose up --build
```

The first build takes a few minutes. Ports 8080 (app) and 8000 (backend, for debugging) must be free.

Open http://localhost:8080 and click Join. The default condition is 1 human + 2 AIs, so a single browser tab runs a full session. A session takes about 3 minutes: two 60 s chats and two 15 s votes.

To test with several human participants, open one tab or private window per human. Each tab is a separate participant, because the token is kept in sessionStorage. The first three joins fill a session and a fourth starts a new one. Reloading a tab rejoins as the same player.

To start from an empty database, run `docker compose down -v`.

A `.env` file is optional; the defaults work without it. Run `cp .env.example .env` to change the Postgres credentials, set the condition or add an API key.

## Switching conditions

The backend loads the config file named by `CONFIG_PATH`. The default is `config.yml`, which is identical to the 1 human + 2 AIs preset.

| Config | Group |
|---|---|
| `configs/3_humans.yml` | 3 humans |
| `configs/2_humans_1_ai.yml` | 2 humans + 1 AI |
| `configs/1_human_2_ai.yml` | 1 human + 2 AIs (default) |
| `configs/1_human_2_ai_llm.yml` | 1 human + 2 AIs, with AI messages written by Claude Haiku 5.5 |

```bash
CONFIG_PATH=configs/3_humans.yml docker compose up --build
```

`CONFIG_PATH` can also go in `.env`. Each file also sets the phase durations and the AI pacing: reply delays, join delay, per-round message cap and typing speed. The config is validated at startup: it needs exactly 3 participants and at least 1 human, and unknown keys are rejected. The loaded condition is printed in the backend log.

**LLM mode (optional).** Put the key in `.env`, which is git-ignored:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Then start with `CONFIG_PATH=configs/1_human_2_ai_llm.yml`. The key is read only from the environment and never logged.

- **Without a key**, the AIs use the rule-based agent and the backend logs a warning.
- **When a call fails**, times out (8 s) or returns something unusable, that one message falls back to the rule-based agent and the reason is logged.

To see the LLM log lines, run `docker compose logs backend | grep LLM`.

## Running the tests

```bash
docker compose build backend
docker compose run --rm --no-deps backend pytest
```

There are 181 tests. They run against a temporary SQLite database and a fake LLM client, so they need neither the Postgres container nor an API key. They cover:

- config validation, phase order and the tie-break
- slot assignment
- the WebSocket protocol, including checks that no participant type reaches the client
- phase timers and early vote end
- the AI reply cap, vote timing and vote consistency
- LLM mode and its fallbacks

The tests are copied into the image, so rebuild after changing them.

## Architecture

- **Frontend:** React (Vite), built into an nginx image. nginx serves the app on port 8080 and proxies `/api` and `/ws` to the backend.
- **Backend:** FastAPI with a single worker.
  - `POST /api/join` assigns a slot and returns a token and a label (A, B or C).
  - Everything else runs over a WebSocket at `/ws?token=...`: the state snapshot on connect or reconnect, chat, votes, phase changes, tallies and results.
- **Live state:** each session's live state is kept in memory as the single source of truth, guarded by one lock per session.
  - Every event is written to PostgreSQL before it is broadcast.
  - Phase timers run on the server; the browser only counts down to the server's deadline.
- **AI agents** have two parts:
  - a driver that decides when to speak and when to vote (delays, reply cap, vote time)
  - a brain that decides what to say (rule-based or LLM)

  AIs post and vote through the same code paths as humans.
- **PostgreSQL tables:**
  - `sessions`: config snapshot, random seed, Decision Maker and tie-break method
  - `participants`: type, label and join time
  - `messages`
  - `votes` (append-only)
  - `phase_events`

  Together they are enough to reconstruct and analyse a session.
- **Phase and vote rules** live in `backend/app/logic.py` as pure functions with unit tests.

## Design decisions and trade-offs

The source for this section is [docs/ISSUES.md](docs/ISSUES.md), which also lists the issues found and fixed during testing.

- **Blind condition.**
  - Labels A, B and C are shuffled per session, and participant type is never sent to the browser.
  - AIs "join" the waiting room after a random 3–10 s, so it behaves the same in every condition.
  - Before each message an AI waits a thinking delay plus typing time proportional to the message length.
  - AIs don't answer every message and stop after 3 messages per round, so two AIs can't loop on each other.
- **Voting.**
  - The vote phase lasts 15 s and ends early once all three have voted.
  - Votes are append-only; a participant's latest vote in a round counts.
  - Self-votes are allowed, and tallies show counts only.
  - Round-1 chat stays visible in round 2 below a divider, together with the round-1 tally.
- **AI votes match their chat.** Each AI votes for the player its last message argued for.
- **Tie-break.** Round-2 votes decide.
  - If round 2 is tied, the combined votes of both rounds break the tie among the round-2 leaders only. A participant with no votes in the final round can't win through round-1 votes.
  - If it is still tied, a seeded random draw decides. The seed is stored per session, so the result can be reproduced.
- **Stale timers.** Each phase change increments a generation counter and cancels the previous timer. A timer that fires late for an old generation does nothing, so a session can never advance twice.
- **One participant per tab.** The token is kept in sessionStorage. This makes it easy to test 3 humans on one machine, and a reload rejoins as the same player.
- **Restarts.** Live state is in memory, so a session interrupted by a backend restart can't resume. Such sessions are marked ABORTED at startup; their data is kept and they accept no new joins.
- **LLM mode changes only what the AIs say.** Timing, the reply cap and votes stay with the driver.
  - The model returns JSON: the message plus the player it supports.
  - Calls are not retried, and the model's response time counts as typing time.
  - A filter rejects any message that mentions AI, bots, models or humans, and that message falls back to the rule-based agent.
- **Ethics note on AI identity questions.** The LLM prompt tells the agent never to say or hint that it is an AI, and also never to claim to be human. When asked "are you a bot?", it steers back to the task instead of lying. Whether and how AI participants may conceal what they are is for the study's ethics plan and debriefing to decide.
- **Observed limitation.** In a live test, both AIs deflected "are you a bot?" the same way at the same moment, which can itself be a cue. Possible fixes:
  - let at most one AI respond to identity questions
  - vary the deflection style
  - settle the policy in the study's ethics plan

## Known limitations and next steps

- **No export endpoint.** The data is in PostgreSQL (`docker compose exec db psql -U experiment experiment`). Next step: a CSV/JSON export per session.
- **Self-nomination is fixed in code.** AIs argue for themselves 25% of the time (`SELF_CANDIDATE_PROBABILITY` in `backend/app/ai/rule_based.py`, also used for the LLM agent's starting candidate). It should become a config field so it can vary by condition.
- **Token in the WebSocket query string.** Browsers can't set headers on WebSocket connections, so the token is sent in the URL and can end up in proxy logs. Tokens are also stored in plain text. Next step: a short-lived connection ticket and hashed tokens.
- **No migrations.** Tables are created with `create_all`, so a schema change needs `docker compose down -v`, or Alembic as a next step.
- **In-memory session state.** The backend runs a single worker and a restart aborts running sessions. Running more workers would need shared state (for example Redis) or routing each session to one worker.

## Time spent

Time spent: about 3 hours of development (14:39–17:32 on 12 November 2026), including reviewing and verifying each step before committing.

## AI usage log

All prompts, together with the exact tools and models used, are in [ai_logs/](ai_logs/).

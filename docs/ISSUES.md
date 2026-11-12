# Issues found and fixed

1. **502 on first start.** Right after `docker compose up -d`, nginx returned
   502 for a few seconds because the backend wasn't ready yet. Found by testing
   the way a reviewer would. Fix: backend healthcheck on /api/health, and the
   frontend waits until the backend is healthy. 
2. **502 after switching condition.** nginx cached the backend’s IP at startup; 
   recreating the backend gave it a new IP. Fix: nginx re-resolves the backend
   through Docker’s DNS every 5 seconds.
3. **Stale sessions confused manual tests.** Waiting sessions left over from
   earlier runs made two test clients land in different sessions. Lesson: start
   every manual check with `docker compose down -v`.

# Design decisions

1. **Tie-break scope.** Round-2 votes decide. If round 2 is tied, combined
   round 1 + 2 votes break the tie among the round-2 leaders only, so a
   participant with no votes in the final round can't win through round-1
   votes. If still tied, a seeded random draw (seed stored per session, so the
   result is reproducible).
2. **AI join delay.** AIs ‘join’ the waiting room after a random 3–10 seconds, 
   so the waiting room behaves the same in every condition and doesn’t reveal 
   AI participants.
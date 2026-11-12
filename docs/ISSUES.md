# Issues found and fixed

1. **502 on first start.** Right after `docker compose up -d`, nginx returned
   502 for a few seconds because the backend wasn't ready yet. Found by testing
   the way a reviewer would. Fix: backend healthcheck on /api/health, and the
   frontend waits until the backend is healthy.

# Design decisions

1. **Tie-break scope.** Round-2 votes decide. If round 2 is tied, combined
   round 1 + 2 votes break the tie among the round-2 leaders only, so a
   participant with no votes in the final round can't win through round-1
   votes. If still tied, a seeded random draw (seed stored per session, so the
   result is reproducible).
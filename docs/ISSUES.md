# Issues found and fixed

1. **502 on first start.** Right after `docker compose up -d`, nginx returned
   502 for a few seconds because the backend wasn't ready yet. Found by testing
   the way a reviewer would. Fix: backend healthcheck on /api/health, and the
   frontend waits until the backend is healthy.
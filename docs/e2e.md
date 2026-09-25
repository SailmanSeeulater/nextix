# End-to-end checklist

Manual verification against a throwaway GitHub repo. Filled in per phase.

## Phase 0

- [ ] `cp .env.example .env`
- [ ] `docker compose up --build`
- [ ] `curl http://localhost:8000/api/health` returns `{"status":"ok",...}`
- [ ] http://localhost:3000 shows the board skeleton with a green "API ok" indicator
- [ ] `docker compose logs worker` shows the Celery worker ready with `nextix.ping` registered
- [ ] `docker compose logs beat` shows beat started without errors

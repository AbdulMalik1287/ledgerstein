# Kosh dashboard

React + Vite + Tailwind front end for the reconciliation engine.

```bash
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/api` to `http://127.0.0.1:8000`, so start the backend
first:

```bash
cd ../backend && python -m uvicorn app.main:app --reload
```

Four screens, one idea each:

- **Scorecard** — precision before recall, and the rupee cost of false matches.
- **Exceptions** — the queue, ordered by exposure, with the engine's diagnosis
  and the candidates it refused to choose between.
- **Matches** — every link, with the rule that made it and a readable reason.
- **Audit trail** — every decision in order, machine and human alike.

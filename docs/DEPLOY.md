# Deploying LedgerStein

The whole thing ships as **one container**. FastAPI serves the API *and* the
built dashboard, so a deployment is a single service on a single origin — no
CORS to configure, no second URL to keep in sync, and a judge gets one link.

The container is also **self-seeding**. On first start it regenerates both
batches from their seeds and reconciles one, so the dashboard opens showing a
real run rather than an empty shell. Nothing needs to be uploaded, and no data
is committed to the repo.

> Verified: a cold start with no `data/generated/` and no database produces
> 620 rows, 476 matches, 100% precision, 98.6% recall — byte-identical to the
> figures in the README.

---

## Option A — Render (recommended)

Free, no card required, and `render.yaml` in the repo root means there is
nothing to configure by hand.

1. Go to **[dashboard.render.com/blueprints](https://dashboard.render.com/blueprints)**
   → **New Blueprint Instance**.
2. Connect GitHub, pick **`AbdulMalik1287/ledgerstein`**.
3. Render reads `render.yaml`, sees one Docker web service on the free plan, and
   shows it for approval. Click **Apply**.
4. Wait ~4–6 minutes for the first build (the npm install is the slow part).
5. Your link is **`https://ledgerstein.onrender.com`** — or whatever suffix
   Render assigns if that name is taken.

**Health check** is already pointed at `/api/health`, so Render will not mark the
service live until the engine has actually started.

### The one thing to know about the free plan

Free services **spin down after 15 minutes of inactivity**, and the next request
takes **about 50 seconds** to wake them.

For judging, that matters. Two ways to handle it:

- **Warm it before you submit.** Open the link ~2 minutes before anyone will
  click it, and again right before the deadline.
- **Keep it awake.** Point a free uptime pinger
  ([UptimeRobot](https://uptimerobot.com), [cron-job.org](https://cron-job.org))
  at `https://<your-app>.onrender.com/api/health` every 10 minutes. This is the
  reliable option and takes two minutes to set up.

Put a line in your submission either way:

> First load may take ~50s if the free instance has spun down.

---

## Option B — Hugging Face Spaces

Also free, no card, and it does **not** spin down the same way. Slightly more
manual because Spaces wants the Dockerfile at the repo root of *its* git remote
and expects port 7860.

1. Create a Space at **[huggingface.co/new-space](https://huggingface.co/new-space)**
   → SDK **Docker** → **Blank** template.
2. Add a short header to the top of a `README.md` in that Space repo:

   ```yaml
   ---
   title: LedgerStein
   emoji: 📗
   colorFrom: green
   colorTo: gray
   sdk: docker
   app_port: 7860
   ---
   ```

3. Push this repo to the Space remote:

   ```bash
   git remote add space https://huggingface.co/spaces/<you>/ledgerstein
   git push space main
   ```

4. Spaces sets `PORT`, and the Dockerfile already honours it.

---

## Option C — Fly.io

Best latency from India (`bom` region) and it can stay always-on, but it wants a
card on file even within the free allowance.

```bash
fly launch --no-deploy --name ledgerstein --region bom
fly deploy
```

`fly launch` will detect the Dockerfile. Set `internal_port = 8000` in the
generated `fly.toml` if it guesses wrong.

---

## Running the container locally

Worth doing once before you rely on any host:

```bash
docker build -t ledgerstein .
docker run --rm -p 8000:8000 ledgerstein
# then open http://localhost:8000
```

The API docs are at `http://localhost:8000/docs`.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8000` | Injected by every host above. The container binds it. |
| `LEDGERSTEIN_DB_URL` | `sqlite:///./ledgerstein.sqlite3` | Ephemeral on free tiers — see below. |
| `ANTHROPIC_API_KEY` | unset | Tier 4 backend, paid. |
| `GEMINI_API_KEY` | unset | Tier 4 backend, **free, no card**. |
| `GROQ_API_KEY` | unset | Tier 4 backend, **free, no card**. |

Set **one** model key in the host's dashboard — **never** in `render.yaml` or
any committed file — and the adjudicator runs on the deployed instance. Set
none and it skips the ambiguous rows and records why, which is a legitimate
state, not a broken one. `GET /api/health` reports which backends it can see.

| Backend | Credential | Cost | Get one |
|---|---|---|---|
| `groq` | `GROQ_API_KEY` | **free tier, no card** | [console.groq.com/keys](https://console.groq.com/keys) |
| `gemini` | `GEMINI_API_KEY` | **free tier, no card** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — must start `AIza`; the short-lived `AQ.…` tokens expire within the hour |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | [console.anthropic.com](https://console.anthropic.com) |
| `ollama` | **none** | free, local | [ollama.com](https://ollama.com) then `ollama pull qwen3:4b` |

`auto` prefers a hosted backend when its key is set, and falls back to a local
Ollama server if one is answering. With nothing available the tier skips the
ambiguous rows and records why — the safe default, not a failure.

Ollama is there because every hosted free tier eventually says no: keys expire
mid-session, quotas exhaust after a couple of dozen calls, and a demo that
depends on someone else's rate limiter can fail while it is being watched. A
local model has no key, no quota and no network. It is preferred last because it
is usually the weakest model on offer.

### About the ephemeral disk

SQLite lives on the container's own filesystem, which free tiers wipe on
restart. That is fine here by design: the service regenerates its batches and
reconciles one on every cold start, so a restart costs nothing except manual
exception resolutions made since. Nothing a judge sees depends on persistence.

If you want resolutions to survive, attach a Render disk (paid) mounted at
`/srv/backend`, or point `LEDGERSTEIN_DB_URL` at a managed Postgres — the
SQLAlchemy models need no changes for that.

---

## After it is live

1. Open the link and click **Reconcile** once to confirm the button path works
   on the deployed instance, not just the seeded run.
2. Update `docs/SUBMISSION.md` with the live URL.
3. Add it to the README badge line.
4. Set up the uptime pinger if you went with Render.

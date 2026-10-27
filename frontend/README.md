# AquaRevive website (React + Vite)

Shows real Sentinel-2 results for every Bengaluru lake: map and priority ranking,
one satellite image per year (re-measured live), trends, explainability
(measurement rule + U-Net occlusion / Grad-CAM / SHAP) and accuracy.
There is no mock data: if a service is down the page says so.

## Run (three terminals, from the project root)

    # 1. AI service (port 8001) - needs the project .venv
    source .venv/bin/activate
    uvicorn ai_agent.app.main:app --port 8001

    # 2. Backend API (port 8000)
    cd backend && source ../.venv/bin/activate
    uvicorn app.main:app --port 8000

    # 3. Website (port 5173)
    cd frontend && npm install && npm run dev      # open http://localhost:5173

The dev server proxies `/api` to the backend and `/files` (satellite pictures)
to the AI service. Other ports: `BACKEND_URL=... AGENT_URL=... npm run dev`.

## Keeping it live

A fourth terminal keeps the data current (internet needed):

    source .venv/bin/activate
    python ai_agent/scripts/live_update.py             # checks every 6 h, runs until Ctrl+C
    python ai_agent/scripts/live_update.py --once      # one check (e.g. from cron)

Each check measures every featured lake on new Sentinel-2 images (a new pass
over Bengaluru every ~5 days), back-fills lakes added to
`ai_agent/featured_lakes.csv`, and pre-renders the newest picture of each lake.
The AI service notices the new rows by itself; the dashboard polls every
5 minutes and shows a "Live" badge with the newest image date. The first run
back-fills new lakes over 2015-2026 and can take an hour or two.

## Chatbot (Ask AquaRevive)

The 💬 button answers questions from the project's own data. It always works
(rule-based, no setup). For natural conversation, install the free local model
once - the bot switches to it automatically when it is running:

    brew install ollama && brew services start ollama
    ollama pull qwen2.5:7b          # ~4.7 GB, once

The model must fetch facts through tools; every number in its reply is checked
against those facts, and accuracy/method questions always use the fixed wording.
If anything fails the bot falls back to the rule-based answer.

## Which lakes

The site shows the well-known named lakes listed in `ai_agent/featured_lakes.csv`
(edit it to add or remove lakes). To serve every outline again, start the AI
service with `LAKE_SET=all`.

## Notes
* The first time a lake's yearly images are opened, each one is downloaded from
  the AWS Sentinel-2 archive (~10-30 s, internet needed) and cached in
  `ai_agent/reports/web/` (git-ignored). Later visits are instant.
* SHAP takes ~10 s per run; it can be switched off on the Explainability tab.
* Tabs are linkable: `/lake/<id>?tab=years|trends|explain|accuracy|causes`.

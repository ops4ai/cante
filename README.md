# Cante · *Ops4 Cante*

An open-source platform to run many AI agents across many messaging channels — from one backoffice. An [Ops4.AI](https://ops4.ai) project.

## What it is (and what it is not)

**It is:**
- A **control plane**: a backoffice to connect numbers, define agents, and watch conversations
- A **runtime**: a scalable engine that reads incoming messages, runs an AI agent, and replies
- **Multi-channel**: WhatsApp first, via [Evolution API](https://github.com/EvolutionAPI/evolution-api). New channels are added by writing one small adapter
- **Multi-model**: each agent can use a different LLM (Anthropic, OpenAI, DeepSeek, OpenRouter, a local model)
- **No-code to customize**: you write an agent's behavior as Markdown and add integrations as configuration

**It is not:**
- A single-purpose chatbot. Nothing about orders, laundry, payments, or any specific business is hard-coded
- A closed SaaS. You self-host it

## Quickstart (~10 min)

```bash
git clone https://github.com/ops4ai/cante.git
cd cante
cp .env.example .env
# Edit .env: set at least ANTHROPIC_API_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
make up
make seed
```

Open **http://localhost:5173** — that's the backoffice UI (a placeholder page for now; the full backoffice lands in M4–M5). The REST API it talks to is live at http://localhost:8000/v1/. To connect a WhatsApp number by QR code today, use the API: `POST /v1/auth/login` → `POST /v1/numbers` → `GET /v1/numbers/{id}/qr` (scan with your phone) → `POST /v1/numbers/{id}/connect`.

## Vocabulary

| Word | Meaning |
|------|---------|
| **Number** | A connected WhatsApp phone number (QR connect) |
| **Bot** | An agent's config: Skill + LLM Provider + language |
| **Skill** | What a bot does — Markdown playbook + scope + tools |
| **Provider** | An LLM endpoint (model + URL + API key) |
| **Route** | Connects a Number to a Bot (one number → many bots) |

## Architecture

```
WhatsApp → Evolution API → ingress → Redis Streams → worker → Redis Streams → sender → Evolution API → WhatsApp
                              │                         │
                              ▼                         ▼
                            api (backoffice)        scheduler (proactive + learning)
```

Each box is a separate, independently scalable service. The message bus is Redis Streams (consumer groups, at-least-once, `XAUTOCLAIM` for crash recovery).

## Project structure

```
cante/
  core/            # shared lib: models, LLM adapters, guards, tools, event bus, channel interface
  services/
    ingress/       # receives channel webhooks, queues messages
    api/           # backoffice REST API (CRUD, conversations, metrics)
    worker/        # runs the AI agent loop + guard pipeline
    sender/        # paces and sends replies (anti-ban)
    scheduler/     # daily learning job + proactive triggers
  frontend/        # React + Vite + Tailwind backoffice
  examples/
    mock-backend/  # tiny service the Barber/Trainer presets call
  deploy/          # docker-compose, Helm/Kustomize for Kubernetes
  docs/            # guides (skills, integrations, gotchas)
  GOTCHAS.md       # reliability rules — read this before deploying
  .env.example
  Makefile
```

## Features

- **Multi-number, multi-bot, multi-model** — one backoffice runs everything
- **Behavior in Markdown** — edit a bot without a redeploy
- **Human hand-off** — conversations escalate when the bot gets stuck
- **Self-improvement loop** — daily analysis of escalations → actionable suggestions
- **No-code integrations** — declare HTTP tools in a Skill; the bot calls your API mid-conversation
- **Proactive messages** — external apps push triggers; the bot reaches out first
- **Guard pipeline** — scope enforcement, language consistency, dedup, send-delay
- **Filtered conversations** — by bot, number, group, state, language, date, full-text search
- **Multi-language UI** — English + European Portuguese + Spanish + French

## Configuration

See `.env.example` for all variables. The important ones:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Default LLM key |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First backoffice login |
| `DATABASE_URL` | PostgreSQL connection |
| `REDIS_URL` | Redis connection |
| `EVOLUTION_BASE_URL` / `EVOLUTION_API_KEY` | Evolution API (WhatsApp) |
| `SECRET_ENCRYPTION_KEY` | Encrypts stored API keys |

## Deploy at scale

Kubernetes → `deploy/helm/`. Each service is a Deployment; workers and senders autoscale (HPA) on stream lag (KEDA `redis-streams` scaler).

## Languages

- **Backoffice UI**: EN (default), pt-PT, es, fr
- **Bots**: any language the model supports; a language guard keeps replies consistent

## Roadmap

- ✅ WhatsApp (Evolution API)
- ⏳ Telegram adapter
- ⏳ Instagram DM adapter
- ⏳ Web chat widget
- ⏳ Pluggable message bus (NATS / Kafka)

## Contributing

Contributions welcome — channel adapters, LLM adapters, docs, and skill presets especially. Read `CONTRIBUTING.md` and open an issue before large changes.

## License

MIT — see `LICENSE`.

## About

Built by **[Ops4.AI](https://ops4.ai)** — consulting for production agentic-AI and LLM orchestration.

# Algo Trading Platform

A full-stack algorithmic trading platform that connects to a live broker (Kotak Neo) to run automated strategies, manage risk, and execute real orders — built with a Node.js/TypeScript backend, a Python async execution engine, and a React frontend.

> ⚠️ **Disclaimer:** This project places real orders in a live trading account when configured with real credentials. Trading involves financial risk. Use at your own risk, and test thoroughly with small quantities before relying on it for real capital.

## Features

- **Live broker integration** — Kotak Neo Trade API (TOTP + MPIN login flow, session caching, order placement, order status polling, margin checks)
- **Pluggable strategy engine** — strategies are Python classes that analyze market data and emit BUY/SELL/HOLD signals; new strategies can be added by implementing a simple interface
- **Custom per-strategy scheduling** — strategies can run on every tick, on a fixed interval, or at specific times of day, instead of a one-size-fits-all polling loop
- **Risk management** — per-order value caps, daily trade count and loss limits, symbol/exchange validation before any order reaches the broker
- **Automatic stop-loss & take-profit** — protective SL orders placed alongside entries, plus a background monitor that tracks live positions against SL/TP levels
- **Resilience** — rate limiting (to respect broker API limits), circuit breakers around broker calls, retry with backoff on transient failures
- **Job queue architecture** — BullMQ (Redis-backed) decouples scheduling from execution, so strategy runs and order placement are queued, retryable, and don't block each other
- **Encrypted credential storage** — user broker credentials are encrypted at rest (AES-256-GCM) and only decrypted in-memory when needed
- **Audit trail** — trades, signals, and admin actions are logged to MongoDB for review

## Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌────────────────────┐
│   Frontend   │─────▶│  Backend (Node)   │─────▶│   MongoDB / Redis   │
│  (React/Vite)│      │  Express + BullMQ │      └────────────────────┘
└─────────────┘      └──────────────────┘                 ▲
                              │  enqueues jobs              │
                              ▼                             │
                     ┌──────────────────┐                   │
                     │ Python Engine     │───────────────────┘
                     │ (asyncio worker)  │
                     │  • Strategies     │
                     │  • Risk Manager   │
                     │  • Order Manager  │
                     │  • SL/TP Monitor  │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  Kotak Neo API    │
                     │  (live broker)    │
                     └──────────────────┘
```

- **Backend** (`backend/`) — Node.js + TypeScript + Express. Handles auth, user/strategy management, and a scheduler that enqueues strategy-run jobs on BullMQ.
- **Python engine** (`python-engine/`) — asyncio-based worker that consumes queued jobs, runs strategy logic, applies risk checks, and talks to the broker.
- **Frontend** (`frontend/`) — React + Vite dashboard for managing strategies, viewing trades, and monitoring P&L.
- **MongoDB** — persistent storage (users, strategies, trades, audit logs).
- **Redis** — job queue (BullMQ), session caching, rate-limit counters.

## Tech Stack

| Layer | Tech |
|---|---|
| Backend API | Node.js, TypeScript, Express, BullMQ |
| Execution engine | Python 3.11, asyncio, httpx |
| Frontend | React, Vite |
| Database | MongoDB |
| Queue / Cache | Redis |
| Broker | Kotak Neo Trade API |
| Infra | Docker, Docker Compose |

## Getting Started

### Prerequisites

- Docker & Docker Compose
- A Kotak Neo trading account with API access enabled
- A **static IP** registered with Kotak Neo (required for live order placement — see [Broker Setup](#broker-setup) below)

### Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/<your-username>/algo_trading_platform.git
   cd algo_trading_platform
   ```

2. Copy the example environment files and fill in your own values (MongoDB URI, Redis config, AES encryption key, Kotak Neo API credentials):
   ```bash
   cp backend/.env.example backend/.env
   cp python-engine/.env.example python-engine/.env
   ```

3. Build and start the stack:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
   ```

4. Open the frontend at `http://localhost:5173`.

### Broker Setup

Kotak Neo enforces static IP whitelisting on order-placement endpoints (SEBI algo-trading requirement):

1. Find your server's outbound IPv4 address (from inside the `python-engine` container, not your browser):
   ```bash
   docker exec -it algo_trading_platform-python-engine-1 python3 -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org').read().decode())"
   ```
2. Register that exact IP in Kotak Neo → **More → Trade API → your application**.
3. Complete a fresh TOTP → MPIN login through the app after whitelisting — sessions are bound to the IP they were created from.

For local development with a non-static home IP, running the stack on a small always-on VPS with a fixed IP is strongly recommended over re-whitelisting every session.

## Adding a New Strategy

Strategies live in `python-engine/src/strategies/` and subclass `BaseStrategy`:

```python
class MyStrategy(BaseStrategy):
    run_times = ["09:20"]          # optional: specific time(s), IST
    # or: run_interval_minutes = 5 # optional: every N minutes
    # (omit both to run on every tick)

    @property
    def name(self) -> str:
        return "my-strategy"

    def symbols(self) -> list[str]:
        return ["NIFTY"]

    async def analyze(self, snapshots: dict) -> Signal:
        # your logic here — return a Signal (BUY/SELL/HOLD)
        ...
```

Register it in `python-engine/src/strategies/registry.py`. Strategies that want to act on multiple symbols in one run (e.g. "buy the top 3 gainers") can override `analyze_many()` instead of `analyze()` to return a list of signals.

## Risk & Safety Notes

- All orders pass through `risk_manager.py` (order value cap, daily trade/loss limits) and `validator.py` before reaching the broker.
- Every entry order is paired with a protective stop-loss order at the broker.
- A kill switch (global and per-user) can halt trading immediately — see `models/KillSwitch.ts`.
- This is a personal/educational project, not audited for production financial use. Review the risk logic carefully and test with minimal capital before scaling up.

## License

MIT (or update to whatever license you intend to use)

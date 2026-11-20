# Algo Trading Platform

A full-stack algorithmic trading platform that connects to a live broker (Kotak Neo) to run automated strategies, manage risk, and execute real orders. Built with a **Node.js/TypeScript backend, Python asynchronous execution engine, and React frontend**.

> ⚠️ **Disclaimer:** This project can place real orders in a live trading account when configured with real credentials. Trading involves financial risk. Use at your own risk, and test thoroughly with small quantities before relying on it for real capital.

## Features

- **Live broker integration** — Kotak Neo Trade API (TOTP + MPIN login flow, session caching, order placement, order status polling, and margin checks).
- **Pluggable strategy engine** — Strategies are Python classes that analyze market data and emit BUY/SELL/HOLD signals. New strategies can be added by implementing a simple interface.
- **Custom per-strategy scheduling** — Strategies can run on every tick, at fixed intervals, or at specific times of day.
- **Risk management** — Per-order value caps, daily trade count and loss limits, and symbol/exchange validation before orders reach the broker.
- **Automatic stop-loss & take-profit** — Protective SL orders placed alongside entries, plus a background monitor that tracks live positions against SL/TP levels.
- **Resilience** — Rate limiting, circuit breakers around broker calls, and retries with backoff on transient failures.
- **Job queue architecture** — BullMQ (Redis-backed) decouples scheduling from execution, so strategy runs and order placement are queued, retryable, and do not block each other.
- **Encrypted credential storage** — User broker credentials are encrypted at rest using AES-256-GCM and decrypted only in memory when needed.
- **Audit trail** — Trades, signals, and admin actions are logged to MongoDB for review.
- **Performance optimization** — Migrated the backend API from Express to Fastify, achieving significantly higher throughput in initial benchmarks.

## Performance Benchmarks

### Express vs Fastify

Migrated the Node.js/TypeScript backend from Express.js to Fastify and benchmarked API throughput and response latency.

| Endpoint | Express (req/s) | Fastify (req/s) | Improvement |
|---|---:|---:|---:|
| `/api/health` | 4,115 | **~14,100** | **3.43×** |
| `/api/trades` | 35 | **~121** | **3.46×** |
| `/api/trades/pnl` | 102 | **~4,060** | **~39.8×** |

### Latency Comparison

| Endpoint | Metric | Express | Fastify |
|---|---|---:|---:|
| `/api/health` | p50 | 11 ms | **3 ms** |
| `/api/health` | p99 | 19 ms | **6 ms** |
| `/api/trades` | p50 | 1,049 ms | **82 ms** |
| `/api/trades` | p99 | 2,939 ms | **~1,000 ms** |
| `/api/trades/pnl` | p50 | 104 ms | **11 ms** |
| `/api/trades/pnl` | p99 | 1,736 ms | **19 ms** |

### Key Results

- Achieved approximately **3.4× higher throughput** on the health endpoint.
- Improved trade listing throughput by approximately **3.5×**.
- Observed approximately **40× higher PnL endpoint throughput** in the initial benchmark.
- Reduced median trade listing latency from 1,049 ms to 82 ms.
- Reduced median PnL endpoint latency from 104 ms to 11 ms.

> **Benchmark note:** These are initial benchmark results from an intermediate Fastify run, not independently validated production results. The PnL endpoint uses Redis caching (60-second TTL), so the observed improvement may include caching effects rather than framework-level gains alone. A controlled comparison with identical cache states, database workloads, hardware, concurrency, and test duration is required to isolate the actual Fastify performance improvement.

## Architecture

```text
┌─────────────┐       ┌──────────────────┐       ┌────────────────────┐
│  Frontend   │──────▶│ Backend (Node.js)│──────▶│   MongoDB / Redis  │
│ React/Vite  │       │ Fastify + BullMQ │       └────────────────────┘
└─────────────┘       └──────────────────┘                  ▲
                              │                             │
                              │ Enqueues jobs               │
                              ▼                             │
                     ┌──────────────────┐                   │
                     │  Python Engine   │───────────────────┘
                     │  asyncio worker  │
                     │                  │
                     │ • Strategies     │
                     │ • Risk Manager   │
                     │ • Order Manager  │
                     │ • SL/TP Monitor  │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │   Kotak Neo API  │
                     │   Live Broker    │
                     └──────────────────┘
```

### Architecture Components

- **Backend** (`backend/`) — Node.js + TypeScript + Fastify. Handles authentication, user and strategy management, API requests, and a scheduler that enqueues strategy-run jobs using BullMQ.
- **Python Engine** (`python-engine/`) — Asyncio-based worker that consumes queued jobs, runs strategy logic, applies risk checks, and communicates with the broker.
- **Frontend** (`frontend/`) — React + Vite dashboard for managing strategies, viewing trades, and monitoring P&L.
- **MongoDB** — Persistent storage for users, strategies, trades, and audit logs.
- **Redis** — Job queue (BullMQ), session caching, and rate-limit counters.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Node.js, TypeScript, Fastify |
| Execution Engine | Python 3.11, asyncio, httpx |
| Frontend | React, Vite |
| Database | MongoDB |
| Queue / Cache | Redis, BullMQ |
| Broker | Kotak Neo Trade API |
| Infrastructure | Docker, Docker Compose |

## Getting Started

### Prerequisites

- Docker & Docker Compose
- A Kotak Neo trading account with API access enabled
- A **static IP** registered with Kotak Neo (required for live order placement — see Broker Setup).

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/tushar5201/algo_trading_platform.git
   cd algo_trading_platform
   ```

2. Copy the example environment files and fill in your own values (MongoDB URI, Redis configuration, AES encryption key, and Kotak Neo API credentials):

   ```bash
   cp backend/.env.example backend/.env
   cp python-engine/.env.example python-engine/.env
   ```

3. Build and start the stack:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
   ```

4. Open the frontend:

   ```text
   http://localhost:5173
   ```

### Broker Setup

Kotak Neo enforces static IP whitelisting on order-placement endpoints.

1. Find your server's outbound IPv4 address from inside the `python-engine` container:

   ```bash
   docker exec -it algo_trading_platform-python-engine-1 python3 -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org').read().decode())"
   ```

2. Register that exact IP in Kotak Neo → **More → Trade API → Your Application**.
3. Complete a fresh TOTP + MPIN login through the app after whitelisting. Sessions are bound to the IP they were created from.

For local development with a dynamic home IP, running the stack on a VPS with a fixed outbound IP is recommended.

## Adding a New Strategy

Strategies live in `python-engine/src/strategies/` and subclass `BaseStrategy`.

```python
class MyStrategy(BaseStrategy):
    run_times = ["09:20"]          # Optional: specific time(s), IST
    # or: run_interval_minutes = 5 # Optional: every N minutes
    # Omit both to run on every tick

    @property
    def name(self) -> str:
        return "my-strategy"

    def symbols(self) -> list[str]:
        return ["NIFTY"]

    async def analyze(self, snapshots: dict) -> Signal:
        # Your logic here — return a Signal (BUY/SELL/HOLD)
        ...
```

Register the strategy in `python-engine/src/strategies/registry.py`.

Strategies that need to act on multiple symbols in one run (for example, buying the top three gainers) can override `analyze_many()` instead of `analyze()` to return a list of signals.

## Risk & Safety Notes

- All orders pass through `risk_manager.py` (order value cap, daily trade/loss limits) and `validator.py` before reaching the broker.
- Every entry order is paired with a protective stop-loss order at the broker.
- A kill switch (global and per-user) can halt trading immediately — see `models/KillSwitch.ts`.
- This is a personal/educational project and has not been independently audited for production financial use. Review the risk logic carefully and test with minimal capital before scaling up.

## License

MIT (or update to the license you intend to use).

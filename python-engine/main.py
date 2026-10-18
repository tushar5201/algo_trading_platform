import asyncio
import signal as sys_signal
import structlog
from aiohttp import web

from src.ai.jobs import AIWorker
from src.config.logging_conf import setup_logging
from src.config.settings import settings
from src.db.mongo import connect_db, disconnect_db
from src.db.redis_client import connect_redis, disconnect_redis
from src.queue.worker import StrategyWorker
from src.broker.session_manager import SessionManager
from src.execution.sl_tp_monitor import SLTPMonitor
from src.health.checker import get_health

setup_logging()
log = structlog.get_logger()


async def health_handler(request: web.Request) -> web.Response:
    health = await get_health()
    status = 200 if health["status"] == "healthy" else 207
    return web.json_response(health, status=status)


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8001)
    await site.start()
    log.info("health_server.started", port=8001)
    return runner


async def main() -> None:
    log.info("python_engine.starting", env=settings.node_env)

    await connect_db()
    await connect_redis()

    session_manager = SessionManager()
    worker = StrategyWorker()
    sl_tp_monitor = SLTPMonitor()
    ai_worker = AIWorker()

    # Restore any existing Kotak session from Redis and kick off scrip master load
    await session_manager.restore_sessions()

    # Wait up to 20 s for scrip master to finish loading before accepting jobs.
    # Without this, the first scheduler tick fires before the CSV downloads and
    # every option chain query returns 0 strikes.
    import time as _time
    from src.utils.market_data import get_scrip_master

    _deadline = _time.monotonic() + 20.0
    while _time.monotonic() < _deadline:
        scrip = get_scrip_master()
        if scrip and scrip._loaded:
            log.info("python_engine.scrip_master_ready", count=len(scrip._cache))
            break
        await asyncio.sleep(0.5)
    else:
        log.warning(
            "python_engine.scrip_master_timeout",
            note="Starting worker anyway — first tick may HOLD if tokens not found",
        )

    # Graceful shutdown
    loop = asyncio.get_running_loop()

    def shutdown():
        log.info("python_engine.shutdown_signal")
        asyncio.create_task(worker.stop())
        session_manager.stop()
        sl_tp_monitor.stop()
        ai_worker.stop()

    loop.add_signal_handler(sys_signal.SIGTERM, shutdown)
    loop.add_signal_handler(sys_signal.SIGINT, shutdown)

    health_runner = await start_health_server()

    try:
        log.info("python_engine.all_services_starting")
        await asyncio.gather(
            worker.start(),
            session_manager.start(),
            sl_tp_monitor.start(),
            ai_worker.start(),
        )
    finally:
        await health_runner.cleanup()
        await disconnect_db()
        await disconnect_redis()
        log.info("python_engine.stopped")


if __name__ == "__main__":
    asyncio.run(main())

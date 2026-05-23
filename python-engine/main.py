import asyncio

import structlog
import signal as sys_signal

from src.broker.session_manager import SessionManager
from src.config.logging_conf import setup_logging
from src.db.mongo import connect_db, disconnect_db
from src.db.redis_client import connect_redis, get_redis, disconnect_redis
from src.queue.worker import StrategyWorker

setup_logging()
log = structlog.get_logger()


async def main() -> None:
    log.info("python_engine.starting")
    await connect_db()
    await connect_redis()

    redis = get_redis()
    worker = StrategyWorker(redis)
    session_manager = SessionManager()

    # Graceful shutdown on SIGTERM / SIGINT (Docker stop)
    loop = asyncio.get_running_loop()

    def shutdown():
        log.info("python_engine.shutdown_signal")
        worker.stop()
        session_manager.stop()

    loop.add_signal_handler(sys_signal.SIGTERM, shutdown)
    loop.add_signal_handler(sys_signal.SIGINT, shutdown)

    try:
        await asyncio.gather(
            worker.start(),
            session_manager.start()
        )
    finally:
        await disconnect_db()
        await disconnect_redis()
        log.info("python_engine.stopped")

if __name__ == "__main__":
    asyncio.run(main())

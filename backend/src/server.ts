import "dotenv/config";
import { buildApp } from "./app";
import { connectDB } from "./config/db";
import { env } from "./config/env";
import { redis } from "./config/redis";
import { startScheduler, stopScheduler } from "./queues/scheduler";
import { startTradeCacheInvalidator, stopTradeCacheInvalidator } from "./services/cacheInvalidator.service";
import { strategyQueue, strategyQueueEvents, tradeQueue, tradeQueueEvents } from "./queues";
import mongoose from "mongoose";

const start = async (): Promise<void> => {
    await connectDB();
    await redis.connect();

    startTradeCacheInvalidator();
    await startScheduler();

    if (env.RATE_LIMIT_SCALE > 1) {
        console.warn(`[Warning] RATE_LIMIT_SCALE=${env.RATE_LIMIT_SCALE}: all rate limits are multiplied. Use only for load tests.`);
    }

    const app = await buildApp();
    await app.listen({ port: parseInt(env.PORT, 10), host: "0.0.0.0" });
    console.log(`Server running on port ${env.PORT}`);

    let shuttingDown = false;
    const shutdown = async (signal: string): Promise<void> => {
        if (shuttingDown) return;
        shuttingDown = true;
        console.log(`${signal} received, shutting down`);
        stopScheduler();
        stopTradeCacheInvalidator();
        try {
            await app.close();
            await Promise.allSettled([
                strategyQueue.close(), tradeQueue.close(),
                strategyQueueEvents.close(), tradeQueueEvents.close()
            ]);
            await mongoose.disconnect();
            redis.disconnect();
        } finally {
            process.exit(0);
        }
    };
    process.on("SIGTERM", () => void shutdown('SIGTERM'));
    process.on("SIGINT", () => void shutdown('SIGINT'));
};

process.on('unhandledRejection', (reason) => {
    console.error("Unhandled promise rejection", reason);
});

start().catch((err) => {
    console.error("Failed to start server:", err);
    process.exit(1);
});

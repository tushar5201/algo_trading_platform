import "dotenv/config";
import app from "./app";
import { connectDB } from "./config/db";
import { env } from "./config/env";
import { redis } from "./config/redis";
import {startScheduler, stopScheduler} from "./queues/scheduler";
import {startTradeCacheInvalidator, stopTradeCacheInvalidator} from "./services/cacheInvalidator.service";
import {strategyQueue, strategyQueueEvents, tradeQueue, tradeQueueEvents} from "./queues";
import mongoose from "mongoose";

const start = async (): Promise<void> => {
    await connectDB();
    await redis.connect();

    startTradeCacheInvalidator();
    await startScheduler();

    const server = app.listen(parseInt(env.PORT, 10), () => {
        console.log(`Server running on port ${env.PORT}`);
    });

    let shuttingDown = false;
    const shutdown = async (signal: string): Promise<void> => {
        if(shuttingDown) return;
        shuttingDown = true;
        console.log(`${signal} received, shutting down`);
        stopScheduler();
        stopTradeCacheInvalidator();
        server.close();
        try {
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

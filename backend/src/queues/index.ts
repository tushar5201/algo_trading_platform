import { Queue, QueueEvents } from "bullmq";
import { redis } from "../config/redis";

export const queueConnection = {
    host: redis.options.host as string,
    port: redis.options.port as number,
}

export const strategyQueue = new Queue("strategy-runner", {
    connection: queueConnection,
    defaultJobOptions: {
        attempts: 3,
        backoff: { type: "exponential", delay: 5000 },
        removeOnComplete: { count: 100 },
        removeOnFail: { count: 200 },
    }
});

export const tradeQueue = new Queue("trade-executor", {
    connection: queueConnection,
    defaultJobOptions: {
        attempts: 3,
        backoff: { type: "fixed", delay: 3000 },
        removeOnComplete: { count: 500 },
        removeOnFail: { count: 500 },
    },
});

// Queue event listeners
export const strategyQueueEvents = new QueueEvents("strategy-runner", { connection: queueConnection });
export const tradeQueueEvents = new QueueEvents("trade-executor", { connection: queueConnection });

strategyQueueEvents.on("completed", ({ jobId }) => {
    console.log(`[Queue] Strategy-runner job ${jobId} completed successfully.`);
});

strategyQueueEvents.on("failed", ({ jobId, failedReason }) => {
    console.error(`[Queue] Strategy-runner job ${jobId} failed: ${failedReason}`);
});

tradeQueueEvents.on("completed", ({ jobId }) => {
    console.log(`[Queue] Trade-executor job ${jobId} completed successfully.`);
});

tradeQueueEvents.on("failed", ({ jobId, failedReason }) => {
    console.error(`[Queue] Trade-executor job ${jobId} failed: ${failedReason}`);
});

import { Queue, QueueEvents } from "bullmq";
import { redis } from "../config/redis";

export const queueConnection = {
    host: redis.options.host as string,
    port: redis.options.port as number,
}

export const strategyQueue = new Queue("strategy-runner", {
    connection: queueConnection,
    defaultJobOptions: {
        attempts: 1,
        removeOnComplete: { count: 100 },
        removeOnFail: { count: 200 },
    }
});

export const AI_QUEUE_NAME = "ai-analysis";
export const aiAnalysisQueue = new Queue(AI_QUEUE_NAME, {
    connection: queueConnection,
    defaultJobOptions: {
        attempts: 1,
        removeOnComplete: { age: 3600, count: 500 },
        removeOnFail: { age: 86400, count: 500 },
    }
});

export const tradeQueue = new Queue("trade-executor", {
    connection: queueConnection,
    defaultJobOptions: {
        attempts: 1,
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

tradeQueueEvents.on("failed", ({ jobId, failedReason }) => {
    console.error(`[Queue] Trade-executor job ${jobId} failed: ${failedReason}`);
});

strategyQueueEvents.on("error", (err) => console.error("[Queue] strategy events error:", err.message))
tradeQueueEvents.on("error", (err) => console.error("[Queue] trade events error:", err.message))


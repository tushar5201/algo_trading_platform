import "dotenv/config";
import app from "./app";
import { connectDB } from "./config/db";
import { env } from "./config/env";
import { redis } from "./config/redis";
import { startScheduler } from "./queues/scheduler";

const start = async (): Promise<void> => {
    await connectDB();
    await redis.connect();

    await startScheduler();

    app.listen(env.PORT, () => {
        console.log(`Server running on port ${env.PORT}`);
    });
};

start().catch((err) => {
    console.error("Failed to start server:", err);
    process.exit(1);
});

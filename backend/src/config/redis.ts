import Redis from "ioredis";
import { env } from "./env";

export const redis = new Redis({
    host: env.REDIS_HOST,
    port: parseInt(env.REDIS_PORT, 10),
    maxRetriesPerRequest: null,
    lazyConnect: true
});

redis.on('connect', () => console.log("Redis connected"));
redis.on('error', (err) => console.error("Redis error" + err));

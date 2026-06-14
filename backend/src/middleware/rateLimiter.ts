import rateLimit, {Options, RateLimitRequestHandler} from "express-rate-limit";
import {redis} from "../config/redis";
import express from "express";

class RedisStore {
    public prefix: string;
    public windowMs: number;

    constructor(prefix: string, windowMs: number) {
        this.prefix = prefix;
        this.windowMs = windowMs;
    }

    private key(identifier: string): string {
        return `r1:${this.prefix}:${identifier}`;
    }

    async increment(key: string): Promise<{ totalHits: number; resetTime: Date }> {
        const redisKey = this.key(key);
        const windowsSecs = Math.ceil(this.windowMs / 1000);
        const hits = await redis.incr(redisKey);

        if (hits == 1) {
            await redis.expire(redisKey, windowsSecs);
        }

        const ttl = await redis.ttl(redisKey);
        const resetTime = new Date(Date.now() + ttl * 1000);
        return {totalHits: hits, resetTime};
    }

    async decrement(key: string): Promise<void> {
        const redisKey = this.key(key);
        await redis.decr(redisKey);
    }

    async resetKey(key: string): Promise<void> {
        await redis.del(this.key(key));
    }
}

function tooManyRequestHandler(req: express.Request, res: express.Response, _next: any, options: Options): void {
    const retryAfter = Math.ceil((options.windowMs ?? 60000) / 1000);
    res.status(429).json({
        success: false,
        error: "TOO_MANY_REQUESTS",
        message: options.message as string,
        retryAfter,
        retryAfterMs: options.windowMs
    });
}

function makeLimiter(opts: {
    prefix: string;
    windowMs: number;
    max: number;
    message: string;
    keyFn?: (req: express.Request) => string;
}): RateLimitRequestHandler {
    const store = new RedisStore(opts.prefix, opts.windowMs);

    return rateLimit({
        windowMs: opts.windowMs,
        max: opts.max,
        message: opts.message,
        standardHeaders: "draft-7", // Rateimit-Policy header
        legacyHeaders: false,
        store,
        keyGenerator: opts.keyFn ?? ((req) => req.ip ?? "unknown"),
        handler: tooManyRequestHandler,
        skip: (req) => req.method === "OPTIONS",
    });
}

export const authLimiter = makeLimiter({
    prefix: "auth",
    windowMs: 15 * 60 * 1000,
    max: 5,
    message: "Too many login attempts from this IP. Please wait 15 mins before trying again."
});

export const apiLimiter = makeLimiter({
    windowMs: 60 * 60 * 1000, // 1 hour
    max: 100,
    message: "Too many requests, please try again after an 15 mins",
    prefix: "api"
});

export const tradeLimiter = makeLimiter({
    prefix: "trades",
    windowMs: 60 * 1000,
    max: 60,
    message: "Too many trade history requests. The dashboard refreshes automatically — no need to refresh manually.",
    keyFn: (req) => (req as any).user?.id ?? req.ip ?? "unknown"
});

export const kotakAuthLimiter = makeLimiter({
    prefix: "kotak_auth",
    windowMs: 5 * 60 * 1000,
    max: 3,
    message: "Too many Kotak authentication attempts. Kotak may lock your account after repeated failures. Please wait 5 minutes",
    keyFn: (req) => (req as any).user?.id ?? req.ip ?? "unknown"
});

export const triggerLimiter = makeLimiter({
    prefix: "trigger",
    windowMs: 60 * 1000,
    max: 5,
    message: "Too many manual strategy triggers attempts",
    keyFn: (req) => (req as any).user?.id ?? req.ip ?? "unknown"
});

export const killSwitchLimiter = makeLimiter({
    prefix: "kill_switch",
    windowMs: 60 * 1000,
    max: 10,
    message: "Too many kills attempts. wait 60 secs",
});

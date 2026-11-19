import type { FastifyReply, FastifyRequest } from "fastify";
import { redis } from "../config/redis";
import { env } from "../config/env";
import "../http";

const INCREMENT_LUA = `
local hits = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if hits == 1 or ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {hits, ttl}
`;

export class RedisStore {
    constructor(private prefix: string, private windowMs: number) {}

    private key(identifier: string): string {
        return `r1:${this.prefix}:${identifier}`;
    }

    async increment(identifier: string): Promise<{ hits: number; ttl: number }> {
        const windowSecs = Math.ceil(this.windowMs / 1000);
        const result = (await redis.eval(INCREMENT_LUA, 1, this.key(identifier), windowSecs)) as [number, number];
        return { hits: Number(result[0]), ttl: Number(result[1]) };
    }

    async decrement(identifier: string): Promise<void> {
        await redis.decr(this.key(identifier));
    }
}

export type Limiter = (request: FastifyRequest, reply: FastifyReply) => Promise<FastifyReply | void>;

function makeLimiter(opts: {
    prefix: string;
    windowMs: number;
    max: number;
    message: string;
    keyFn?: (request: FastifyRequest) => string;
    skipSuccessfulRequests?: boolean;
}): Limiter {
    const store = new RedisStore(opts.prefix, opts.windowMs);
    const windowSecs = Math.ceil(opts.windowMs / 1000);

    const max = opts.max * env.RATE_LIMIT_SCALE;
    const keyFn = opts.keyFn ?? byIp;

    return async (request, reply) => {
        if (request.method === "OPTIONS") return;

        const identifier = keyFn(request);
        let hits: number;
        let ttl: number;
        try {
            ({ hits, ttl } = await store.increment(identifier));
        } catch {
            return;
        }

        reply.header("RateLimit-Policy", `${max};w=${windowSecs}`);
        reply.header("RateLimit", `limit=${max}, remaining=${Math.max(0, max - hits)}, reset=${ttl}`);

        if (hits > max) {
            reply.header("Retry-After", String(windowSecs));
            return reply.code(429).send({
                success: false,
                error: "TOO_MANY_REQUESTS",
                message: opts.message,
                retryAfter: windowSecs,
                retryAfterMs: opts.windowMs,
            });
        }

        if (opts.skipSuccessfulRequests) {
            reply.raw.once("finish", () => {
                if (reply.statusCode < 400) store.decrement(identifier).catch(() => undefined);
            });
        }
    };
}

const byIp = (request: FastifyRequest): string => request.ip ?? "unknown";
const byUserOrIp = (request: FastifyRequest): string => request.user?.userId ?? request.ip ?? "unknown";

export const authLimiter = makeLimiter({
    prefix: "auth",
    windowMs: 15 * 60 * 1000,
    max: 10,
    skipSuccessfulRequests: true,
    message: "Too many login attempts from this IP. Please wait 15 mins before trying again.",
});

export const apiLimiter = makeLimiter({
    prefix: "api",
    windowMs: 15 * 60 * 1000,
    max: 1000,
    message: "Too many requests, please try again after an 15 mins",
});

export const tradeLimiter = makeLimiter({
    prefix: "trades",
    windowMs: 60 * 1000,
    max: 120,
    message: "Too many trade history requests. The dashboard refreshes automatically, no need to refresh manually.",
    keyFn: byUserOrIp,
});

export const aiReviewLimiter = makeLimiter({
    prefix: "ai_review",
    windowMs: 60 * 60 * 1000,
    max: 20,
    message: "Too many AI review requests. Try again later.",
    keyFn: byUserOrIp,
});

export const kotakAuthLimiter = makeLimiter({
    prefix: "kotak_auth",
    windowMs: 5 * 60 * 1000,
    max: 5,
    message: "Too many Kotak authentication attempts. Kotak may lock your account after repeated failures. Please wait 5 minutes",
    keyFn: byUserOrIp,
});

export const triggerLimiter = makeLimiter({
    prefix: "trigger",
    windowMs: 60 * 1000,
    max: 5,
    message: "Too many manual strategy triggers attempts",
    keyFn: byUserOrIp,
});

export const killSwitchLimiter = makeLimiter({
    prefix: "kill_switch",
    windowMs: 60 * 1000,
    max: 10,
    message: "Too many kills attempts. wait 60 secs",
    keyFn: byUserOrIp,
});

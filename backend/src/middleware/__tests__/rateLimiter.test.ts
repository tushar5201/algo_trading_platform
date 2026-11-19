import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock("../../config/redis", () => ({ redis: { eval: jest.fn(), decr: jest.fn() } }));

import Fastify from "fastify";
import { redis } from "../../config/redis";
import { aiReviewLimiter, authLimiter, killSwitchLimiter, kotakAuthLimiter, tradeLimiter, triggerLimiter, type Limiter } from "../rateLimiter";

type Fn = jest.Mock<(...a: any[]) => any>;
const r = redis as unknown as { eval: Fn; decr: Fn };

/** Redis stand-in that really counts, mirroring what the Lua script does (INCR + TTL). */
function useCountingRedis(ttl = 60) {
    const counts = new Map<string, number>();
    r.eval.mockImplementation(async (_script: string, _n: number, key: string) => {
        const hits = (counts.get(key) ?? 0) + 1;
        counts.set(key, hits);
        return [hits, ttl];
    });
    r.decr.mockImplementation(async (key: string) => { counts.set(key, (counts.get(key) ?? 1) - 1); return 0; });
    return counts;
}

async function appWith(limiter: Limiter, user?: { userId: string }, statusFor?: () => number) {
    const app = Fastify();
    app.decorateRequest("user", null);
    app.addHook("preHandler", async (req) => { if (user) req.user = user as any; });
    app.addHook("preHandler", limiter);
    app.all("/x", async (_req, reply) => reply.code(statusFor ? statusFor() : 200).send({ ok: true }));
    await app.ready();
    return app;
}
const hit = (app: any, method = "POST", extra: Record<string, unknown> = {}) => app.inject({ method, url: "/x", ...extra });

describe("rate limiters", () => {
    beforeEach(() => { r.eval.mockReset(); r.decr.mockReset(); });

    it("triggerLimiter allows 5 requests per window, then answers 429 with the old body shape and Retry-After", async () => {
        useCountingRedis();
        const app = await appWith(triggerLimiter, { userId: "u1" });
        for (let i = 0; i < 5; i++) expect((await hit(app)).statusCode).toBe(200);

        const res = await hit(app);
        expect(res.statusCode).toBe(429);
        expect(res.json()).toMatchObject({ success: false, error: "TOO_MANY_REQUESTS", retryAfter: 60, retryAfterMs: 60000 });
        expect(res.headers["retry-after"]).toBe("60");
    });

    it("sends draft-7 rate limit headers on allowed requests", async () => {
        useCountingRedis(42);
        const app = await appWith(triggerLimiter, { userId: "u1" });
        const res = await hit(app);
        expect(res.headers["ratelimit-policy"]).toBe("5;w=60");
        expect(res.headers["ratelimit"]).toBe("limit=5, remaining=4, reset=42");
    });

    it("keys per user, so one user hitting the limit does not block another", async () => {
        useCountingRedis();
        const a = await appWith(triggerLimiter, { userId: "alice" });
        const b = await appWith(triggerLimiter, { userId: "bob" });
        for (let i = 0; i < 6; i++) await hit(a);
        expect((await hit(a)).statusCode).toBe(429);
        expect((await hit(b)).statusCode).toBe(200);
    });

    it("namespaces redis keys with the limiter prefix and the identifier, in one atomic eval call", async () => {
        const counts = useCountingRedis();
        await hit(await appWith(killSwitchLimiter, { userId: "u9" }));
        expect([...counts.keys()]).toEqual(["r1:kill_switch:u9"]);
        expect(r.eval).toHaveBeenCalledTimes(1);
        const [script, nKeys, , windowSecs] = r.eval.mock.calls[0];
        expect(script).toContain("INCR");
        expect(script).toContain("EXPIRE");
        expect([nKeys, windowSecs]).toEqual([1, 60]);
    });

    it("does not count CORS preflight (OPTIONS) requests", async () => {
        useCountingRedis();
        const res = await hit(await appWith(kotakAuthLimiter, { userId: "u1" }), "OPTIONS");
        expect(res.statusCode).toBe(200);
        expect(r.eval).not.toHaveBeenCalled();
    });

    it("fails open when redis is down", async () => {
        r.eval.mockRejectedValue(new Error("redis down"));
        const res = await hit(await appWith(aiReviewLimiter, { userId: "u1" }));
        expect(res.statusCode).toBe(200);
    });

    it("keys by client ip when there is no authenticated user", async () => {
        const counts = useCountingRedis();
        const app = Fastify({ trustProxy: "loopback,linklocal,uniquelocal" });
        app.addHook("preHandler", authLimiter);
        app.post("/x", async () => ({}));
        await app.inject({ method: "POST", url: "/x", remoteAddress: "10.0.0.2", headers: { "x-forwarded-for": "203.0.113.7" } });
        expect([...counts.keys()]).toEqual(["r1:auth:203.0.113.7"]);
    });

    it("kotakAuthLimiter is strict: 5 per window", async () => {
        useCountingRedis();
        const app = await appWith(kotakAuthLimiter, { userId: "u1" });
        const statuses: number[] = [];
        for (let i = 0; i < 6; i++) statuses.push((await hit(app)).statusCode);
        expect(statuses).toEqual([200, 200, 200, 200, 200, 429]);
    });

    it("tradeLimiter allows 120 per minute", async () => {
        useCountingRedis();
        r.eval.mockImplementationOnce(async () => [120, 60]).mockImplementationOnce(async () => [121, 60]);
        const app = await appWith(tradeLimiter, { userId: "u1" });
        expect((await hit(app, "GET")).statusCode).toBe(200);
        expect((await hit(app, "GET")).statusCode).toBe(429);
    });

    describe("authLimiter only counts failed attempts (skipSuccessfulRequests)", () => {
        it("gives the hit back after a successful response", async () => {
            const counts = useCountingRedis();
            const app = await appWith(authLimiter, undefined, () => 200);
            await hit(app);
            await new Promise(r => setImmediate(r));
            expect(r.decr).toHaveBeenCalledTimes(1);
            expect([...counts.values()]).toEqual([0]);
        });

        it("keeps the hit after a failed response (wrong password)", async () => {
            const counts = useCountingRedis();
            const app = await appWith(authLimiter, undefined, () => 401);
            await hit(app);
            await new Promise(r => setImmediate(r));
            expect(r.decr).not.toHaveBeenCalled();
            expect([...counts.values()]).toEqual([1]);
        });

        it("blocks the 11th failed attempt", async () => {
            useCountingRedis();
            const app = await appWith(authLimiter, undefined, () => 401);
            const statuses: number[] = [];
            for (let i = 0; i < 11; i++) statuses.push((await hit(app)).statusCode);
            expect(statuses.slice(0, 10)).toEqual(Array(10).fill(401));
            expect(statuses[10]).toBe(429);
        });
    });
});

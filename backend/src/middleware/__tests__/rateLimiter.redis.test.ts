/**
 * Runs the real Lua script against a real Redis. Skipped by default.
 * Run with:  REDIS_TEST_PORT=6379 pnpm test rateLimiter.redis     (optional REDIS_TEST_HOST)
 */
import { afterAll, beforeAll, describe, expect, it, jest } from "@jest/globals";
import Redis from "ioredis";

const PORT = process.env.REDIS_TEST_PORT;
const client = PORT ? new Redis({ host: process.env.REDIS_TEST_HOST ?? "127.0.0.1", port: Number(PORT), lazyConnect: true }) : (null as unknown as Redis);

jest.mock("../../config/redis", () => ({ get redis() { return client; } }));

import { RedisStore } from "../rateLimiter";

const maybe = PORT ? describe : describe.skip;

maybe("RedisStore against a real redis", () => {
    const prefix = `itest_${Date.now()}`;
    beforeAll(async () => { await client.connect(); });
    afterAll(async () => { const keys = await client.keys(`r1:${prefix}:*`); if (keys.length) await client.del(...keys); client.disconnect(); });

    it("counts hits and returns the remaining ttl", async () => {
        const store = new RedisStore(prefix, 30_000);
        expect(await store.increment("a")).toEqual({ hits: 1, ttl: 30 });
        const second = await store.increment("a");
        expect(second.hits).toBe(2);
        expect(second.ttl).toBeGreaterThan(0);
        expect(second.ttl).toBeLessThanOrEqual(30);
    });

    it("sets an expiry on the key (no key can live forever)", async () => {
        const store = new RedisStore(prefix, 20_000);
        await store.increment("b");
        const ttl = await client.ttl(`r1:${prefix}:b`);
        expect(ttl).toBeGreaterThan(0);
        expect(ttl).toBeLessThanOrEqual(20);
    });

    it("repairs a key that lost its ttl", async () => {
        const store = new RedisStore(prefix, 10_000);
        await client.set(`r1:${prefix}:c`, "5");                   // no TTL, as after a crash
        const out = await store.increment("c");
        expect(out.hits).toBe(6);
        expect(await client.ttl(`r1:${prefix}:c`)).toBeGreaterThan(0);
    });

    it("keeps separate counters per identifier and supports decrement", async () => {
        const store = new RedisStore(prefix, 30_000);
        await store.increment("d1"); await store.increment("d1");
        expect((await store.increment("d2")).hits).toBe(1);
        await store.decrement("d1");
        expect((await store.increment("d1")).hits).toBe(2);
    });

    it("is safe under concurrency: 50 parallel increments give exactly 1..50", async () => {
        const store = new RedisStore(prefix, 30_000);
        const results = await Promise.all(Array.from({ length: 50 }, () => store.increment("e")));
        expect(results.map(r => r.hits).sort((a, b) => a - b)).toEqual(Array.from({ length: 50 }, (_, i) => i + 1));
    });
});

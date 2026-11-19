import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock("../../config/redis", () => ({ redis: { get: jest.fn(), setex: jest.fn(), del: jest.fn() } }));
jest.mock("../../models/User", () => ({ User: { findById: jest.fn() } }));

import { redis } from "../../config/redis";
import { User } from "../../models/User";
import { AUTH_CACHE_TTL_SECONDS, getAccount, invalidateAccount } from "../auth.cache.service";

type Fn = jest.Mock<(...a: any[]) => any>;
const R = redis as unknown as Record<"get" | "setex" | "del", Fn>;
const findById = (User as unknown as { findById: Fn }).findById;
const select = (doc: unknown) => ({ select: jest.fn(async (_f?: string) => doc) });
const doc = { role: "user", status: "APPROVED", isActive: true, email: "a@b.c", passwordHash: "never-cached" };

describe("auth.cache.service", () => {
    beforeEach(() => {
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        [R.get, R.setex, R.del, findById].forEach(f => f.mockReset());
        R.setex.mockResolvedValue("OK");
    });

    it("cache hit: no database read at all", async () => {
        R.get.mockResolvedValue(JSON.stringify({ role: "admin", status: "APPROVED", isActive: true, email: "x@y.z" }));
        const out = await getAccount("u1");
        expect(out).toMatchObject({ role: "admin" });
        expect(findById).not.toHaveBeenCalled();
        expect(R.get).toHaveBeenCalledWith("auth:acct:u1");
    });

    it("cache miss: reads only the access fields, then caches them with a short TTL", async () => {
        R.get.mockResolvedValue(null);
        const q = select(doc); findById.mockReturnValue(q);
        const out = await getAccount("u1");
        expect(q.select).toHaveBeenCalledWith("role status isActive email");
        expect(out).toEqual({ role: "user", status: "APPROVED", isActive: true, email: "a@b.c" });
        const [key, ttl, value] = R.setex.mock.calls[0];
        expect([key, ttl]).toEqual(["auth:acct:u1", AUTH_CACHE_TTL_SECONDS]);
        expect(value).not.toContain("never-cached");
        expect(AUTH_CACHE_TTL_SECONDS).toBeLessThanOrEqual(60);
    });

    it("a missing user is cached too (as null), so bad tokens do not hit the database every time", async () => {
        R.get.mockResolvedValueOnce(null);
        findById.mockReturnValue(select(null));
        expect(await getAccount("ghost")).toBeNull();
        expect(R.setex.mock.calls[0][2]).toBe("null");

        R.get.mockResolvedValueOnce("null");
        findById.mockClear();
        expect(await getAccount("ghost")).toBeNull();
        expect(findById).not.toHaveBeenCalled();
    });

    it("redis read failure falls back to the database", async () => {
        R.get.mockRejectedValue(new Error("redis down"));
        findById.mockReturnValue(select(doc));
        expect(await getAccount("u1")).toMatchObject({ role: "user" });
    });

    it("redis write failure still returns the account", async () => {
        R.get.mockResolvedValue(null);
        R.setex.mockRejectedValue(new Error("redis down"));
        findById.mockReturnValue(select(doc));
        expect(await getAccount("u1")).toMatchObject({ status: "APPROVED" });
    });

    it("invalidateAccount deletes the key and never throws", async () => {
        R.del.mockResolvedValue(1);
        await invalidateAccount("u1");
        expect(R.del).toHaveBeenCalledWith("auth:acct:u1");
        R.del.mockRejectedValue(new Error("down"));
        await expect(invalidateAccount("u1")).resolves.toBeUndefined();
    });
});

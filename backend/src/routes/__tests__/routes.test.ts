import { afterAll, beforeAll, beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock("../../config/redis", () => ({
    redis: {
        eval: jest.fn(async () => [1, 60]),
        decr: jest.fn(), del: jest.fn(), get: jest.fn(async () => null), setex: jest.fn(), ping: jest.fn(),
        options: { host: "localhost", port: 6379 },
    },
}));
jest.mock("../../queues", () => ({
    strategyQueue: { add: jest.fn() }, tradeQueue: {}, aiAnalysisQueue: { add: jest.fn() },
}));
jest.mock("../../models/User", () => ({
    User: Object.assign(jest.fn(), { findById: jest.fn(), findOne: jest.fn(), find: jest.fn(), countDocuments: jest.fn() }),
}));
jest.mock("../../models/Trade", () => ({
    Trade: { find: jest.fn(), countDocuments: jest.fn(), aggregate: jest.fn(), findOne: jest.fn() },
    TRADE_STATUSES: ["PENDING", "EXECUTED", "EXIT_PENDING", "CLOSED", "FAILED", "CANCELED", "NEEDS_REVIEW"],
}));
jest.mock("../../models/KillSwitch", () => ({ KillSwitch: { findOne: jest.fn(), findOneAndUpdate: jest.fn() } }));
jest.mock("../../models/Strategy", () => ({ Strategy: { find: jest.fn() } }));
jest.mock("../../services/audit.service", () => ({ writeAuditLog: jest.fn(async () => undefined) }));

import type { FastifyInstance } from "fastify";
import { buildApp } from "../../app";
import { User } from "../../models/User";
import { KillSwitch } from "../../models/KillSwitch";
import { signToken } from "../../services/auth.service";

type Fn = jest.Mock<(...a: any[]) => any>;
const U = User as unknown as Record<string, Fn>;
const K = KillSwitch as unknown as Record<string, Fn>;

const ID = "507f1f77bcf86cd799439011";
const tokenFor = (role: string) => signToken({ _id: { toString: () => ID }, role, email: "a@b.c" } as any);
/** authenticate() reads the account (and its role) from the DB, so that is what decides access. */
const loginAs = (role: "user" | "admin") =>
    U.findById.mockReturnValue({ select: jest.fn(async (_f?: string) => ({ role, status: "APPROVED", isActive: true, email: "a@b.c" })) });
const bearer = (role: string) => ({ authorization: `Bearer ${tokenFor(role)}` });

let app: FastifyInstance;
beforeAll(async () => { app = await buildApp(); await app.ready(); });
afterAll(async () => { await app.close(); });

/** Small wrapper so the tests read like before: call(method, path, {headers, body}) */
const call = (method: string, path: string, opts: { headers?: Record<string, string>; body?: unknown } = {}) =>
    app.inject({ method: method.toUpperCase() as any, url: path, headers: opts.headers, payload: opts.body as any });

beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    for (const m of ["findById", "findOne", "find", "countDocuments"]) U[m].mockReset();
    for (const m of ["findOne", "findOneAndUpdate"]) K[m].mockReset();
});

describe("route protection", () => {
    const protectedRoutes: Array<[string, string]> = [
        ["get", "/api/auth/me"],
        ["get", "/api/admin/users"], ["get", "/api/admin/strategies"], ["get", "/api/admin/queue-status"],
        ["get", "/api/admin/health"], ["get", "/api/admin/kill-switch"], ["get", "/api/admin/trades"],
        ["post", "/api/admin/kill-switch"],
        ["get", "/api/user/strategies"], ["get", "/api/user/credentials"], ["get", "/api/user/kotak/auth-status"],
        ["post", "/api/user/credentials"], ["patch", "/api/user/toggle-trading"],
        ["get", "/api/trades"], ["get", "/api/trades/pnl"],
    ];

    it.each(protectedRoutes)("%s %s → 401 without a token", async (method, path) => {
        const res = await call(method, path);
        expect(res.statusCode).toBe(401);
    });

    it.each(protectedRoutes.filter(([, p]) => p.startsWith("/api/admin")))(
        "%s %s → 403 for a normal user", async (method, path) => {
            loginAs("user");
            const res = await call(method, path, { headers: bearer("user") });
            expect(res.statusCode).toBe(403);
        });

    it.each([["get", "/api/trades"], ["get", "/api/trades/pnl"], ["get", "/api/user/strategies"], ["patch", "/api/user/toggle-trading"]])(
        "%s %s → 403 for an admin (user-only routes)", async (method, path) => {
            loginAs("admin");
            const res = await call(method, path, { headers: bearer("admin") });
            expect(res.statusCode).toBe(403);
        });

    it("trusts the DB role, not the role claimed in the token", async () => {
        loginAs("user");                                           // DB says user ...
        const res = await call("get", "/api/admin/users", { headers: bearer("admin") });   // ... token says admin
        expect(res.statusCode).toBe(403);
    });

    it("a deactivated account's token is rejected", async () => {
        U.findById.mockReturnValue({ select: jest.fn(async () => ({ role: "admin", status: "APPROVED", isActive: false })) });
        const res = await call("get", "/api/admin/users", { headers: bearer("admin") });
        expect(res.statusCode).toBe(401);
    });
});

describe("routing", () => {
    it("an admin reaches the admin controller (GET /api/admin/kill-switch)", async () => {
        loginAs("admin");
        K.findOne.mockResolvedValue({ isActive: true, reason: "halt" });
        const res = await call("get", "/api/admin/kill-switch", { headers: bearer("admin") });
        expect(res.statusCode).toBe(200);
        expect(res.json().data).toMatchObject({ isActive: true, reason: "halt" });
    });

    it("validation errors from controllers come back as JSON 400s via the error handler", async () => {
        loginAs("admin");
        const res = await call("post", "/api/admin/kill-switch", { headers: bearer("admin"), body: { isActive: "maybe" } });
        expect(res.statusCode).toBe(400);
        expect(res.json().message).toBeDefined();
    });

    it("POST /api/auth/login validates the body", async () => {
        const res = await call("post", "/api/auth/login", { body: { email: "not-an-email", password: "x" } });
        expect(res.statusCode).toBe(400);
    });

    it("POST /api/auth/signup rejects a short password before touching the DB", async () => {
        const res = await call("post", "/api/auth/signup", { body: { name: "Asha", email: "a@b.com", password: "short" } });
        expect(res.statusCode).toBe(400);
        expect(U.findOne).not.toHaveBeenCalled();
    });

    it("unknown routes return a JSON 404", async () => {
        const res = await call("get", "/api/does-not-exist");
        expect(res.statusCode).toBe(404);
        expect(res.json().message).toBe("Route not found");
    });

    it("rejects oversized JSON bodies (10kb limit)", async () => {
        const res = await call("post", "/api/auth/login", { body: { email: "a@b.com", password: "x".repeat(20_000) } });
        expect(res.statusCode).toBe(413);
    });

    it("sends security headers (helmet) and hides x-powered-by", async () => {
        const res = await call("get", "/api/health");
        expect(res.headers["x-powered-by"]).toBeUndefined();
        expect(res.headers["x-content-type-options"]).toBe("nosniff");
    });
});

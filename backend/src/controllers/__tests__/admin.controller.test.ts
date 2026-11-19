import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock("../../models/User", () => ({
    User: { find: jest.fn(), findById: jest.fn(), findByIdAndUpdate: jest.fn(), countDocuments: jest.fn() },
}));
jest.mock("../../models/Strategy", () => ({ Strategy: { find: jest.fn(), findOne: jest.fn(), create: jest.fn() } }));
jest.mock("../../models/KillSwitch", () => ({ KillSwitch: { findOne: jest.fn(), findOneAndUpdate: jest.fn() } }));
jest.mock("../../services/audit.service", () => ({ writeAuditLog: jest.fn(async () => undefined) }));
jest.mock("../../services/queue.service", () => ({ publishStrategyRun: jest.fn() }));
jest.mock("../../config/redis", () => ({ redis: { ping: jest.fn(), get: jest.fn() } }));
jest.mock("../../queues", () => {
    const q = () => ({ getWaitingCount: jest.fn(), getActiveCount: jest.fn(), getFailedCount: jest.fn(), getCompletedCount: jest.fn() });
    return { strategyQueue: q(), tradeQueue: q() };
});

import mongoose from "mongoose";
import { User } from "../../models/User";
import { Strategy } from "../../models/Strategy";
import { KillSwitch } from "../../models/KillSwitch";
import { writeAuditLog } from "../../services/audit.service";
import { publishStrategyRun } from "../../services/queue.service";
import { redis } from "../../config/redis";
import { strategyQueue, tradeQueue } from "../../queues";
import * as admin from "../admin.controller";

type Fn = jest.Mock<(...a: any[]) => any>;
const U = User as unknown as Record<string, Fn>;
const S = Strategy as unknown as Record<string, Fn>;
const K = KillSwitch as unknown as Record<string, Fn>;
const audit = writeAuditLog as Fn;
const publish = publishStrategyRun as Fn;
const R = redis as unknown as { ping: Fn; get: Fn };
const SQ = strategyQueue as unknown as Record<string, Fn>;
const TQ = tradeQueue as unknown as Record<string, Fn>;

const ADMIN_ID = "507f1f77bcf86cd799439011";
const USER_ID = "507f1f77bcf86cd799439022";
const adminReq = (extra: any = {}) => ({ user: { userId: ADMIN_ID, role: "admin", email: "root@x.com" }, query: {}, body: {}, params: {}, ...extra }) as any;

function makeRes() {
    const r: any = { statusCode: 200, body: undefined as any };
    r.status = (c: number) => { r.statusCode = c; return r; };
    r.json = (b: unknown) => { r.body = b; return r; };
    return r;
}
/** Chainable mongoose-style query that resolves to `result`. */
const chain = (result: unknown) => {
    const q: any = {};
    for (const m of ["sort", "skip", "limit", "select"]) q[m] = jest.fn(() => q);
    q.then = (ok: any, bad: any) => Promise.resolve(result).then(ok, bad);
    return q;
};
const userDoc = (over: any = {}) => ({
    _id: new mongoose.Types.ObjectId(USER_ID), email: "u@x.com", role: "user", status: "APPROVED", isActive: true,
    autoTrading: false, encryptedCredentials: "enc", assignedStrategies: ["s1"], save: jest.fn(async () => undefined), ...over,
});

beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    [U, S, K].forEach(o => Object.values(o).forEach(f => f.mockReset()));
    [audit, publish, R.ping, R.get, ...Object.values(SQ), ...Object.values(TQ)].forEach(f => f.mockReset());
    audit.mockResolvedValue(undefined);
});

describe("getAllUsers", () => {
    it("lists only role=user, paginated, without password hashes", async () => {
        const find = chain([{ email: "a" }]);
        U.find.mockReturnValue(find);
        U.countDocuments.mockResolvedValue(45);
        const res = makeRes();
        await admin.getAllUsers(adminReq({ query: { page: "2", limit: "20" } }), res);
        expect(U.find).toHaveBeenCalledWith({ role: "user" });
        expect(find.skip).toHaveBeenCalledWith(20);
        expect(find.limit).toHaveBeenCalledWith(20);
        expect(find.select).toHaveBeenCalledWith("-passwordHash");
        expect(res.body.data).toMatchObject({ total: 45, page: 2, totalPages: 3 });
    });

    it("applies a valid status filter and rejects an invalid one", async () => {
        U.find.mockReturnValue(chain([])); U.countDocuments.mockResolvedValue(0);
        await admin.getAllUsers(adminReq({ query: { status: "PENDING" } }), makeRes());
        expect(U.find).toHaveBeenCalledWith({ role: "user", status: "PENDING" });
        await expect(admin.getAllUsers(adminReq({ query: { status: "HACKED" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
    });
});

describe("updateUserStatus", () => {
    const call = (body: any, id = USER_ID) => admin.updateUserStatus(adminReq({ body, params: { userId: id } }), makeRes());

    it("400 for an invalid status or id; 404 for a missing user", async () => {
        await expect(call({ status: "PENDING" })).rejects.toMatchObject({ statusCode: 400 });
        await expect(call({ status: "APPROVED" }, "nope")).rejects.toMatchObject({ statusCode: 400 });
        U.findById.mockResolvedValue(null);
        await expect(call({ status: "APPROVED" })).rejects.toMatchObject({ statusCode: 404 });
    });

    it("403 when targeting an admin account", async () => {
        U.findById.mockResolvedValue(userDoc({ role: "admin" }));
        await expect(call({ status: "REJECTED" })).rejects.toMatchObject({ statusCode: 403 });
    });

    it("approves a user and audits USER_APPROVED with the reason", async () => {
        const u = userDoc({ status: "PENDING" });
        U.findById.mockResolvedValue(u);
        const res = makeRes();
        await admin.updateUserStatus(adminReq({ body: { status: "APPROVED", reason: "KYC ok" }, params: { userId: USER_ID } }), res);
        expect(u.status).toBe("APPROVED");
        expect(u.save).toHaveBeenCalled();
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "USER_APPROVED", USER_ID, { reason: "KYC ok" });
        expect(res.body.data).toMatchObject({ email: "u@x.com", status: "APPROVED" });
    });

    it("rejecting a user also switches auto-trading off", async () => {
        const u = userDoc({ autoTrading: true });
        U.findById.mockResolvedValue(u);
        await call({ status: "REJECTED" });
        expect(u.autoTrading).toBe(false);
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "USER_REJECTED", USER_ID, { reason: undefined });
    });
});

describe("assignStrategies", () => {
    const call = (strategies: any) => {
        const res = makeRes();
        return admin.assignStrategies(adminReq({ body: { strategies }, params: { userId: USER_ID } }), res).then(() => res);
    };

    it("400 for an empty list; 404 for an unknown user", async () => {
        await expect(call([])).rejects.toMatchObject({ statusCode: 400 });
        U.findById.mockResolvedValue(null);
        await expect(call(["s1"])).rejects.toMatchObject({ statusCode: 404 });
    });

    it("400 when any requested strategy is not an active strategy", async () => {
        U.findById.mockResolvedValue(userDoc());
        S.find.mockResolvedValue([{ name: "s1" }]);
        await expect(call(["s1", "ghost"])).rejects.toMatchObject({ statusCode: 400, message: /not valid/ });
    });

    it("lowercases + dedupes names, saves them and audits", async () => {
        const u = userDoc();
        U.findById.mockResolvedValue(u);
        S.find.mockResolvedValue([{ name: "s1" }, { name: "s2" }]);
        const res = await call(["S1", "s1", "S2"]);
        expect(S.find).toHaveBeenCalledWith({ name: { $in: ["s1", "s2"] }, isActive: true });
        expect(u.assignedStrategies).toEqual(["s1", "s2"]);
        expect(u.save).toHaveBeenCalled();
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "ASSIGN_STRATEGIES", USER_ID, { strategies: ["s1", "s2"] });
        expect(res.body.data.assignedStrategies).toEqual(["s1", "s2"]);
    });
});

describe("toggleUserTrading", () => {
    const call = () => { const res = makeRes(); return admin.toggleUserTrading(adminReq({ params: { userId: USER_ID } }), res).then(() => res); };

    it("404 for a missing user", async () => {
        U.findById.mockResolvedValue(null);
        await expect(call()).rejects.toMatchObject({ statusCode: 404 });
    });

    it("refuses to enable trading for a user who is not approved or is inactive", async () => {
        U.findById.mockResolvedValue(userDoc({ status: "PENDING" }));
        await expect(call()).rejects.toMatchObject({ statusCode: 400 });
        U.findById.mockResolvedValue(userDoc({ isActive: false }));
        await expect(call()).rejects.toMatchObject({ statusCode: 400 });
    });

    it("refuses to enable trading without broker credentials (re-checks the hidden field)", async () => {
        U.findById
            .mockResolvedValueOnce(userDoc({ encryptedCredentials: undefined }))
            .mockReturnValueOnce({ select: jest.fn(async (_f?: string) => ({ encryptedCredentials: null })) });
        await expect(call()).rejects.toMatchObject({ statusCode: 400, message: /credentials/ });
    });

    it("enables trading when credentials are only visible via +encryptedCredentials", async () => {
        const u = userDoc({ encryptedCredentials: undefined });
        const select = jest.fn(async (_f?: string) => ({ encryptedCredentials: "enc" }));
        U.findById.mockResolvedValueOnce(u).mockReturnValueOnce({ select });
        const res = await call();
        expect(select).toHaveBeenCalledWith("+encryptedCredentials");
        expect(u.autoTrading).toBe(true);
        expect(res.body.data.autoTrading).toBe(true);
    });

    it("disabling is always allowed, even for a rejected user", async () => {
        const u = userDoc({ autoTrading: true, status: "REJECTED", encryptedCredentials: undefined });
        U.findById.mockResolvedValue(u);
        const res = await call();
        expect(u.autoTrading).toBe(false);
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "ADMIN_TOGGLE_TRADING", USER_ID, { autoTrading: false });
        expect(res.body.message).toMatch(/disabled/);
    });
});

describe("strategies", () => {
    it("createStrategy lowercases the name and falls back to displayName as description", async () => {
        R.get.mockResolvedValue(JSON.stringify(["ema_x"]));
        S.findOne.mockResolvedValue(null);
        S.create.mockImplementation(async (d: any) => d);
        const res = makeRes();
        await admin.createStrategy(adminReq({ body: { name: "  EMA_X ", displayName: "EMA X" } }), res);
        expect(S.create).toHaveBeenCalledWith(expect.objectContaining({ name: "ema_x", description: "EMA X" }));
        expect(res.statusCode).toBe(201);
    });

    it("createStrategy rejects a name that has no logic file in the engine", async () => {
        R.get.mockResolvedValue(JSON.stringify(["real_one"]));
        await expect(admin.createStrategy(adminReq({ body: { name: "made_up", displayName: "Made Up" } }), makeRes()))
            .rejects.toMatchObject({ statusCode: 400, message: /No strategy logic found/ });
        expect(S.create).not.toHaveBeenCalled();
    });

    it("createStrategy returns 503 when the engine has not published its strategies", async () => {
        R.get.mockResolvedValue(null);
        await expect(admin.createStrategy(adminReq({ body: { name: "real_one", displayName: "Real" } }), makeRes()))
            .rejects.toMatchObject({ statusCode: 503 });
        R.get.mockResolvedValue("not json");
        await expect(admin.createStrategy(adminReq({ body: { name: "real_one", displayName: "Real" } }), makeRes()))
            .rejects.toMatchObject({ statusCode: 503 });
    });

    it("createStrategy returns 409 for a strategy that is already registered", async () => {
        R.get.mockResolvedValue(JSON.stringify(["real_one"]));
        S.findOne.mockResolvedValue({ name: "real_one" });
        await expect(admin.createStrategy(adminReq({ body: { name: "real_one", displayName: "Real" } }), makeRes()))
            .rejects.toMatchObject({ statusCode: 409 });
        expect(S.create).not.toHaveBeenCalled();
    });

    it("getAvailableStrategies lists engine strategies and flags the ones already registered", async () => {
        R.get.mockResolvedValue(JSON.stringify(["a", "B"]));
        S.find.mockReturnValue({ select: jest.fn(async (_f?: string) => [{ name: "a" }]) });
        const res = makeRes();
        await admin.getAvailableStrategies(adminReq(), res);
        expect(res.body.data).toEqual([{ name: "a", registered: true }, { name: "b", registered: false }]);
    });

    it("createStrategy validates input", async () => {
        R.get.mockResolvedValue(JSON.stringify(["ok"]));
        await expect(admin.createStrategy(adminReq({ body: { name: "a", displayName: "b" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
        await expect(admin.createStrategy(adminReq({ body: { name: "ok", displayName: "OK", riskLevel: "WILD" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
    });

    it("listStrategies returns active strategies sorted by name", async () => {
        const q = chain([{ name: "a" }]); S.find.mockReturnValue(q);
        const res = makeRes();
        await admin.listStrategies(adminReq(), res);
        expect(S.find).toHaveBeenCalledWith({ isActive: true });
        expect(q.sort).toHaveBeenCalledWith({ name: 1 });
        expect(res.body.data).toEqual([{ name: "a" }]);
    });
});

describe("triggerStrategy", () => {
    const call = (body: any = { strategyName: "S1" }) => {
        const res = makeRes();
        return admin.triggerStrategy(adminReq({ body, params: { userId: USER_ID } }), res).then(() => res);
    };
    const arrange = (user: any, { global = null, userKill = null, strategy = { name: "s1" } }: any = {}) => {
        U.findById.mockReturnValue({ select: jest.fn(async (_f?: string) => user) });
        K.findOne.mockImplementation(async (q: any) => (q.scope === "global" ? global : userKill));
        S.findOne.mockResolvedValue(strategy);
        publish.mockResolvedValue("job-9");
    };

    it("400 without a strategy name; 404 for an unknown user", async () => {
        await expect(call({})).rejects.toMatchObject({ statusCode: 400 });
        arrange(null);
        await expect(call()).rejects.toMatchObject({ statusCode: 404 });
    });

    it.each([
        ["not approved", { status: "PENDING" }, /not approved/],
        ["inactive", { isActive: false }, /not approved/],
        ["no credentials", { encryptedCredentials: null }, /credentials/],
        ["strategy not assigned", { assignedStrategies: ["other"] }, /not assigned/],
    ])("400 when the user is %s", async (_n, over, msg) => {
        arrange(userDoc(over));
        await expect(call()).rejects.toMatchObject({ statusCode: 400, message: expect.stringMatching(msg) });
        expect(publish).not.toHaveBeenCalled();
    });

    it("409 when the global or the user kill switch is on", async () => {
        arrange(userDoc(), { global: { isActive: true } });
        await expect(call()).rejects.toMatchObject({ statusCode: 409, message: /Global/ });
        arrange(userDoc(), { userKill: { isActive: true } });
        await expect(call()).rejects.toMatchObject({ statusCode: 409, message: /this user/ });
        expect(publish).not.toHaveBeenCalled();
    });

    it("400 when the strategy is not active", async () => {
        arrange(userDoc(), { strategy: null });
        await expect(call()).rejects.toMatchObject({ statusCode: 400, message: /not active/ });
    });

    it("queues the run as ADMIN with the user's credentials and audits it", async () => {
        arrange(userDoc());
        const res = await call();
        expect(publish).toHaveBeenCalledWith(expect.objectContaining({
            userId: USER_ID, strategyName: "s1", encryptedCredentials: "enc", triggeredBy: "ADMIN",
        }));
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "MANUAL_STRATEGY_TRIGGER", USER_ID, { strategyName: "s1", jobId: "job-9" });
        expect(res.body.data).toEqual({ jobId: "job-9" });
    });
});

describe("getQueueStatus", () => {
    it("reports counts for both queues", async () => {
        SQ.getWaitingCount.mockResolvedValue(1); SQ.getActiveCount.mockResolvedValue(2);
        SQ.getFailedCount.mockResolvedValue(3); SQ.getCompletedCount.mockResolvedValue(4);
        TQ.getWaitingCount.mockResolvedValue(5); TQ.getActiveCount.mockResolvedValue(6);
        TQ.getFailedCount.mockResolvedValue(7); TQ.getCompletedCount.mockResolvedValue(8);
        const res = makeRes();
        await admin.getQueueStatus(adminReq(), res);
        expect(res.body.data).toEqual({
            strategyRunner: { waiting: 1, active: 2, failed: 3, completed: 4 },
            tradeExecutor: { waiting: 5, active: 6, failed: 7, completed: 8 },
        });
    });

    it("returns null for a count that fails instead of failing the whole call", async () => {
        Object.values(SQ).forEach(f => f.mockResolvedValue(0));
        Object.values(TQ).forEach(f => f.mockResolvedValue(0));
        SQ.getFailedCount.mockRejectedValue(new Error("redis"));
        const res = makeRes();
        await admin.getQueueStatus(adminReq(), res);
        expect(res.body.data.strategyRunner.failed).toBeNull();
        expect(res.body.data.strategyRunner.waiting).toBe(0);
    });
});

describe("getHealth", () => {
    const setDb = (db: unknown) => Object.defineProperty(mongoose.connection, "db", { value: db, configurable: true, writable: true });

    it("healthy when mongo and redis answer", async () => {
        setDb({ admin: () => ({ ping: async () => ({}) }) });
        R.ping.mockResolvedValue("PONG"); K.findOne.mockReturnValue(Promise.resolve(null));
        const res = makeRes();
        await admin.getHealth(adminReq(), res);
        expect(res.body.data).toMatchObject({ status: "healthy", killSwitchActive: false,
            checks: { mongodb: { status: "up" }, redis: { status: "up" } } });
    });

    it("degraded, with the error message, when a dependency is down; reports an active kill switch", async () => {
        setDb({ admin: () => ({ ping: async () => { throw new Error("mongo gone"); } }) });
        R.ping.mockResolvedValue("PONG"); K.findOne.mockReturnValue(Promise.resolve({ isActive: true }));
        const res = makeRes();
        await admin.getHealth(adminReq(), res);
        expect(res.body.data.status).toBe("degraded");
        expect(res.body.data.checks.mongodb).toEqual({ status: "down", error: "mongo gone" });
        expect(res.body.data.killSwitchActive).toBe(true);
    });

    it("treats redis failure as degraded", async () => {
        setDb({ admin: () => ({ ping: async () => ({}) }) });
        R.ping.mockRejectedValue(new Error("no redis")); K.findOne.mockReturnValue(Promise.resolve(null));
        const res = makeRes();
        await admin.getHealth(adminReq(), res);
        expect(res.body.data.checks.redis).toEqual({ status: "down", error: "no redis" });
    });
});

describe("kill switches", () => {
    it("getKillSwitchStatus reflects the global switch", async () => {
        K.findOne.mockResolvedValue({ isActive: true, reason: "market crash", updatedAt: "t" });
        const res = makeRes();
        await admin.getKillSwitchStatus(adminReq(), res);
        expect(res.body.data).toEqual({ isActive: true, reason: "market crash", updatedAt: "t" });
        K.findOne.mockResolvedValue(null);
        const res2 = makeRes();
        await admin.getKillSwitchStatus(adminReq(), res2);
        expect(res2.body.data).toMatchObject({ isActive: false, reason: "" });
    });

    it("setGlobalKillSwitch upserts the global record, audits KILL_SWITCH_ON, and validates input", async () => {
        K.findOneAndUpdate.mockResolvedValue({});
        const res = makeRes();
        await admin.setGlobalKillSwitch(adminReq({ body: { isActive: true, reason: "halt" } }), res);
        const [filter, update, opts] = K.findOneAndUpdate.mock.calls[0];
        expect(filter).toEqual({ scope: "global", userId: null });
        expect(update).toMatchObject({ scope: "global", isActive: true, reason: "halt" });
        expect(opts).toMatchObject({ upsert: true });
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "KILL_SWITCH_ON", undefined, { reason: "halt" });
        expect(res.body.message).toMatch(/ACTIVATED/);

        await expect(admin.setGlobalKillSwitch(adminReq({ body: { isActive: "yes" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
        await expect(admin.setGlobalKillSwitch(adminReq({ body: { isActive: true, reason: "x".repeat(501) } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
    });

    it("deactivating audits KILL_SWITCH_OFF", async () => {
        K.findOneAndUpdate.mockResolvedValue({});
        const res = makeRes();
        await admin.setGlobalKillSwitch(adminReq({ body: { isActive: false } }), res);
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "KILL_SWITCH_OFF", undefined, { reason: undefined });
        expect(res.body.message).toMatch(/DEACTIVATED/);
    });

    it("setUserKillSwitch is scoped to the user and 404s for unknown users", async () => {
        U.findById.mockReturnValue({ select: jest.fn(async (_f?: string) => null) });
        await expect(admin.setUserKillSwitch(adminReq({ body: { isActive: true }, params: { userId: USER_ID } }), makeRes())).rejects.toMatchObject({ statusCode: 404 });

        const id = new mongoose.Types.ObjectId(USER_ID);
        U.findById.mockReturnValue({ select: jest.fn(async (_f?: string) => ({ _id: id })) });
        K.findOneAndUpdate.mockResolvedValue({});
        const res = makeRes();
        await admin.setUserKillSwitch(adminReq({ body: { isActive: true, reason: "r" }, params: { userId: USER_ID } }), res);
        expect(K.findOneAndUpdate.mock.calls[0][0]).toEqual({ scope: "user", userId: id });
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "KILL_SWITCH_ON", USER_ID, { reason: "r" });
        expect(res.body.message).toMatch(/User kill switch ACTIVATED/);
    });
});

describe("setUserRiskLimits", () => {
    const call = (body: any) => { const res = makeRes(); return admin.setUserRiskLimits(adminReq({ body, params: { userId: USER_ID } }), res).then(() => res); };

    it("requires at least one limit and rejects non-positive / fractional values", async () => {
        await expect(call({})).rejects.toMatchObject({ statusCode: 400 });
        await expect(call({ maxDailyLoss: 0 })).rejects.toMatchObject({ statusCode: 400 });
        await expect(call({ maxDailyTrades: 1.5 })).rejects.toMatchObject({ statusCode: 400 });
        await expect(call({ maxDailyTrades: -3 })).rejects.toMatchObject({ statusCode: 400 });
    });

    it("sets only the provided limits via $set and audits", async () => {
        U.findByIdAndUpdate.mockResolvedValue({ riskLimits: { maxDailyLoss: 1000, maxDailyTrades: 50 } });
        const res = await call({ maxDailyLoss: 1000 });
        expect(U.findByIdAndUpdate).toHaveBeenCalledWith(USER_ID, { $set: { "riskLimits.maxDailyLoss": 1000 } }, { new: true, runValidators: true });
        expect(audit).toHaveBeenCalledWith(ADMIN_ID, "admin", "SET_RISK_LIMITS", USER_ID, { maxDailyLoss: 1000 });
        expect(res.body.data.riskLimits.maxDailyLoss).toBe(1000);
    });

    it("sets both limits together", async () => {
        U.findByIdAndUpdate.mockResolvedValue({ riskLimits: {} });
        await call({ maxDailyLoss: 5, maxDailyTrades: 3 });
        expect(U.findByIdAndUpdate.mock.calls[0][1]).toEqual({ $set: { "riskLimits.maxDailyLoss": 5, "riskLimits.maxDailyTrades": 3 } });
    });

    it("404 when the user does not exist", async () => {
        U.findByIdAndUpdate.mockResolvedValue(null);
        await expect(call({ maxDailyTrades: 5 })).rejects.toMatchObject({ statusCode: 404 });
    });
});

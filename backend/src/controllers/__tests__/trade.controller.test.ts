import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock("../../models/Trade", () => ({
    Trade: { find: jest.fn(), countDocuments: jest.fn(), aggregate: jest.fn(), findById: jest.fn() },
    TRADE_STATUSES: ["PENDING", "EXECUTED", "EXIT_PENDING", "CLOSED", "FAILED", "CANCELED", "NEEDS_REVIEW"],
}));
jest.mock("../../services/cache.service", () => ({
    getOrSetCache: jest.fn(),
    cacheKeys: { userPnl: (id: string) => `cache:pnl:${id}` },
}));
jest.mock("../../services/audit.service", () => ({ writeAuditLog: jest.fn() }));
jest.mock("../../queues", () => ({ aiAnalysisQueue: { add: jest.fn() } }));

import mongoose from "mongoose";
import { Trade } from "../../models/Trade";
import { getOrSetCache } from "../../services/cache.service";
import { getAllTrades, getMyPnl, getMyTrades, getTradeByIdAdmin, reviewJobId } from "../trade.controller";

type Fn = jest.Mock<(...a: any[]) => any>;
const T = Trade as unknown as Record<string, Fn>;
const cacheMock = getOrSetCache as Fn;

const USER_ID = "507f1f77bcf86cd799439011";
const OTHER_ID = "507f1f77bcf86cd799439022";
const req = (extra: any = {}) => ({ user: { userId: USER_ID, role: "user", email: "u@x.com" }, query: {}, params: {}, ...extra }) as any;

function makeRes() {
    const r: any = { statusCode: 200, body: undefined as any };
    r.status = (c: number) => { r.statusCode = c; return r; };
    r.json = (b: unknown) => { r.body = b; return r; };
    return r;
}
const chain = (result: unknown) => {
    const q: any = {};
    for (const m of ["sort", "skip", "limit", "populate"]) q[m] = jest.fn(() => q);
    q.then = (ok: any, bad: any) => Promise.resolve(result).then(ok, bad);
    return q;
};

beforeEach(() => { Object.values(T).forEach(f => f.mockReset()); cacheMock.mockReset(); });

describe("getMyTrades", () => {
    it("is always scoped to the caller's userId, newest first, and paginated", async () => {
        const q = chain([{ symbol: "A" }]);
        T.find.mockReturnValue(q); T.countDocuments.mockResolvedValue(41);
        const res = makeRes();
        await getMyTrades(req({ query: { page: "3", limit: "10" } }), res);

        const filter = T.find.mock.calls[0][0];
        expect(filter.userId.toString()).toBe(USER_ID);
        expect(q.sort).toHaveBeenCalledWith({ createdAt: -1 });
        expect(q.skip).toHaveBeenCalledWith(20);
        expect(q.limit).toHaveBeenCalledWith(10);
        expect(res.body.data).toMatchObject({ total: 41, page: 3, totalPages: 5 });
    });

    it("skips the count query when the first page is not full (saves a database operation)", async () => {
        T.find.mockReturnValue(chain([{}, {}, {}]));
        const res = makeRes();
        await getMyTrades(req(), res);
        expect(T.countDocuments).not.toHaveBeenCalled();
        expect(res.body.data).toMatchObject({ total: 3, page: 1, totalPages: 1 });
    });

    it("still counts when the first page is full", async () => {
        T.find.mockReturnValue(chain(Array.from({ length: 20 }, () => ({}))));
        T.countDocuments.mockResolvedValue(57);
        const res = makeRes();
        await getMyTrades(req(), res);
        expect(T.countDocuments).toHaveBeenCalledTimes(1);
        expect(res.body.data).toMatchObject({ total: 57, totalPages: 3 });
    });

    it("filters by status and strategy", async () => {
        T.find.mockReturnValue(chain([])); T.countDocuments.mockResolvedValue(0);
        await getMyTrades(req({ query: { status: "CLOSED", strategy: "ema" } }), makeRes());
        expect(T.find.mock.calls[0][0]).toMatchObject({ status: "CLOSED", strategyName: "ema" });
    });

    it("400 for an unknown status filter (and never queries)", async () => {
        await expect(getMyTrades(req({ query: { status: "BOGUS" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
        expect(T.find).not.toHaveBeenCalled();
    });

    it("ignores a non-string status so operator objects cannot be injected", async () => {
        T.find.mockReturnValue(chain([])); T.countDocuments.mockResolvedValue(0);
        await getMyTrades(req({ query: { status: { $ne: "CLOSED" } } }), makeRes());
        expect(T.find.mock.calls[0][0].status).toBeUndefined();
    });
});

describe("getMyPnl", () => {
    /** Run the real compute callback so the aggregation shaping is exercised. */
    const arrangeCache = () => cacheMock.mockImplementation(async (_k: any, _ttl: any, compute: any) => ({ data: await compute(), cached: false }));

    it("caches per user for 60 seconds and reports cache hits", async () => {
        cacheMock.mockResolvedValue({ data: { summary: {} }, cached: true });
        const res = makeRes();
        await getMyPnl(req(), res);
        expect(cacheMock).toHaveBeenCalledWith(`cache:pnl:${USER_ID}`, 60, expect.any(Function));
        expect(res.body).toMatchObject({ success: true, cached: true });
    });

    it("computes the summary, win rate and open positions from the aggregates", async () => {
        arrangeCache();
        T.aggregate
            .mockResolvedValueOnce([{ totalPnl: 500, totalTrades: 4, winTrades: 3, lossTrades: 1, avgPnl: 125, bestTrade: 400, worstTrade: -100 }])
            .mockResolvedValueOnce([{ _id: "ema", pnl: 500, trades: 4, winTrades: 3 }])
            .mockResolvedValueOnce([{ _id: "2026-01-02", pnl: 500, trades: 4 }]);
        T.countDocuments.mockResolvedValue(2);
        const res = makeRes();
        await getMyPnl(req(), res);

        expect(res.body.cached).toBe(false);
        expect(res.body.data.summary).toEqual({
            totalPnl: 500, totalTrades: 4, winTrades: 3, lossTrades: 1, winRate: "75.0%",
            avgPnl: 125, bestTrade: 400, worstTrade: -100, openPositions: 2,
        });
        expect(res.body.data.byStrategy).toEqual([{ _id: "ema", pnl: 500, trades: 4, winTrades: 3 }]);
        expect(res.body.data.byDay).toHaveLength(1);
    });

    it("returns zeros (and no divide-by-zero) for a user with no closed trades", async () => {
        arrangeCache();
        T.aggregate.mockResolvedValue([]); T.countDocuments.mockResolvedValue(0);
        const res = makeRes();
        await getMyPnl(req(), res);
        expect(res.body.data.summary).toEqual({
            totalPnl: 0, totalTrades: 0, winTrades: 0, lossTrades: 0, winRate: "0%",
            avgPnl: 0, bestTrade: 0, worstTrade: 0, openPositions: 0,
        });
    });

    it("only aggregates this user's CLOSED trades that have a pnl; open = EXECUTED + EXIT_PENDING", async () => {
        arrangeCache();
        T.aggregate.mockResolvedValue([]); T.countDocuments.mockResolvedValue(0);
        await getMyPnl(req(), makeRes());
        for (const call of T.aggregate.mock.calls) {
            const match = call[0][0].$match;
            expect(match.userId.toString()).toBe(USER_ID);
            expect(match).toMatchObject({ status: "CLOSED", pnl: { $ne: null } });
        }
        const open = T.countDocuments.mock.calls[0][0];
        expect(open.status).toEqual({ $in: ["EXECUTED", "EXIT_PENDING"] });
    });

    it("limits the per-day series to the last 30 days in IST", async () => {
        arrangeCache();
        T.aggregate.mockResolvedValue([]); T.countDocuments.mockResolvedValue(0);
        const before = Date.now();
        await getMyPnl(req(), makeRes());
        const byDay = T.aggregate.mock.calls[2][0];
        const since = byDay[0].$match.closedAt.$gte.getTime();
        expect(Math.abs((before - since) - 30 * 86400_000)).toBeLessThan(5000);
        expect(byDay[1].$group._id.$dateToString.timezone).toBe("Asia/Kolkata");
    });
});

describe("getAllTrades (admin)", () => {
    it("lists every user's trades with owner name/email populated", async () => {
        const q = chain([{ symbol: "A" }]);
        T.find.mockReturnValue(q); T.countDocuments.mockResolvedValue(1);
        const res = makeRes();
        await getAllTrades(req({ user: { userId: "adm", role: "admin" } }), res);
        expect(T.find).toHaveBeenCalledWith({});
        expect(q.populate).toHaveBeenCalledWith("userId", "name email");
        expect(res.body.data).toMatchObject({ total: 1, page: 1, totalPages: 1 });
    });

    it("admin list also skips the count on a short first page", async () => {
        T.find.mockReturnValue(chain([{}]));
        const res = makeRes();
        await getAllTrades(req({ user: { userId: "adm", role: "admin" } }), res);
        expect(T.countDocuments).not.toHaveBeenCalled();
        expect(res.body.data.total).toBe(1);
    });

    it("can filter by userId, status and strategy", async () => {
        T.find.mockReturnValue(chain([])); T.countDocuments.mockResolvedValue(0);
        await getAllTrades(req({ query: { userId: OTHER_ID, status: "FAILED", strategy: "oi" } }), makeRes());
        const filter = T.find.mock.calls[0][0];
        expect(filter.userId).toBeInstanceOf(mongoose.Types.ObjectId);
        expect(filter.userId.toString()).toBe(OTHER_ID);
        expect(filter).toMatchObject({ status: "FAILED", strategyName: "oi" });
    });

    it("400 for an invalid userId or status", async () => {
        await expect(getAllTrades(req({ query: { userId: "x" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
        await expect(getAllTrades(req({ query: { status: "NOPE" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
    });
});

describe("getTradeByIdAdmin", () => {
    it("returns the trade with its owner populated", async () => {
        const q = chain({ _id: "t1" }); T.findById.mockReturnValue(q);
        const res = makeRes();
        await getTradeByIdAdmin(req({ params: { tradeId: OTHER_ID } }), res);
        expect(T.findById).toHaveBeenCalledWith(OTHER_ID);
        expect(q.populate).toHaveBeenCalledWith("userId", "name email");
        expect(res.body).toEqual({ success: true, data: { _id: "t1" } });
    });

    it("404 when the trade does not exist and 400 for a malformed id", async () => {
        T.findById.mockReturnValue(chain(null));
        await expect(getTradeByIdAdmin(req({ params: { tradeId: OTHER_ID } }), makeRes())).rejects.toMatchObject({ statusCode: 404 });
        await expect(getTradeByIdAdmin(req({ params: { tradeId: "zzz" } }), makeRes())).rejects.toMatchObject({ statusCode: 400 });
    });
});

describe("reviewJobId", () => {
    it("is deterministic and contains no colon (BullMQ rejects colons in custom ids)", () => {
        expect(reviewJobId("abc123")).toBe("review-abc123");
        expect(reviewJobId("abc123")).not.toContain(":");
    });
});

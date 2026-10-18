import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock('../../config/redis', () => ({
    redis: { incr: jest.fn(), expire: jest.fn(), ttl: jest.fn(), decr: jest.fn(), del: jest.fn(), options: { host: 'localhost', port: 6379 } },
}));
jest.mock('../../queues', () => ({ aiAnalysisQueue: { add: jest.fn() } }));
jest.mock('../../models/Trade', () => ({
    TRADE_STATUSES: ['PENDING', 'EXECUTED', 'EXIT_PENDING', 'CLOSED', 'FAILED', 'CANCELED', 'NEEDS_REVIEW'],
    Trade: { findOne: jest.fn(), findOneAndUpdate: jest.fn(), updateOne: jest.fn(), findById: jest.fn() },
}));
jest.mock('../../services/audit.service', () => ({ writeAuditLog: jest.fn() }));
// authentication itself is covered in middleware/__tests__/auth.test.ts: here it just sets req.user
jest.mock('../../middleware/auth', () => ({
    authenticate: (req: any, _res: any, next: any) => {
        req.user = { userId: OWNER, role: req.headers['x-role'] ?? 'user', email: 'owner@example.com' };
        next();
    },
}));

const OWNER = '64b000000000000000000001';
const TRADE_ID = '64b0000000000000000000aa';

import express from "express";
import request from "supertest";
import { redis } from "../../config/redis";
import { aiAnalysisQueue } from "../../queues";
import { Trade } from "../../models/Trade";
import { errorHandler } from "../../middleware/errorhandler";
import tradesRoutes from "../../routes/trades.routes";
import { getTradeByIdAdmin } from "../trade.controller";

const mockedTrade = jest.mocked(Trade) as any;
const mockedQueue = jest.mocked(aiAnalysisQueue) as any;
const mockedRedis = jest.mocked(redis) as any;

const app = express();
app.use(express.json());
app.use('/api/trades', tradesRoutes);
app.use(errorHandler);

const url = (id = TRADE_ID) => `/api/trades/${id}/ai-review`;
const closedTrade = (extra: Record<string, unknown> = {}) => ({ _id: TRADE_ID, userId: OWNER, status: 'CLOSED', ...extra });

beforeEach(() => {
    jest.resetAllMocks();
    process.env.AI_ENABLED = 'true';
    mockedRedis.incr.mockResolvedValue(1);
    mockedRedis.ttl.mockResolvedValue(60);
    mockedQueue.add.mockResolvedValue({});
    mockedTrade.updateOne.mockResolvedValue({});
});

describe('POST /api/trades/:tradeId/ai-review', () => {
    it('rejects a malformed trade id with 400', async () => {
        const res = await request(app).post(url('not-an-id'));
        expect(res.status).toBe(400);
        expect(mockedQueue.add).not.toHaveBeenCalled();
    });

    it('returns 503 when AI is not enabled', async () => {
        process.env.AI_ENABLED = 'false';
        const res = await request(app).post(url());
        expect(res.status).toBe(503);
        expect(mockedTrade.findOne).not.toHaveBeenCalled();
    });

    it('is owner-only: another user\'s (or missing) trade is a 404 and the query is scoped to the caller', async () => {
        mockedTrade.findOne.mockResolvedValue(null);
        const res = await request(app).post(url());
        expect(res.status).toBe(404);
        expect(String(mockedTrade.findOne.mock.calls[0][0].userId)).toBe(OWNER);
        expect(mockedQueue.add).not.toHaveBeenCalled();
    });

    it('refuses a trade that is still open with 409', async () => {
        mockedTrade.findOne.mockResolvedValue(closedTrade({ status: 'EXECUTED' }));
        const res = await request(app).post(url());
        expect(res.status).toBe(409);
        expect(mockedQueue.add).not.toHaveBeenCalled();
    });

    it('does not queue a second review when one is already DONE', async () => {
        mockedTrade.findOne.mockResolvedValue(closedTrade({ aiReview: { status: 'DONE', summary: 's' } }));
        const res = await request(app).post(url());
        expect(res.status).toBe(200);
        expect(res.body.alreadyDone).toBe(true);
        expect(mockedQueue.add).not.toHaveBeenCalled();
    });

    it('queues one idempotent job (id has no colon) and answers 202', async () => {
        mockedTrade.findOne.mockResolvedValue(closedTrade());
        mockedTrade.findOneAndUpdate.mockResolvedValue(closedTrade({ aiReview: { status: 'PENDING' } }));
        const res = await request(app).post(url());
        expect(res.status).toBe(202);
        expect(res.body.data.aiReview.status).toBe('PENDING');
        expect(mockedQueue.add).toHaveBeenCalledTimes(1);
        const [name, data, opts] = mockedQueue.add.mock.calls[0];
        expect(name).toBe('review');
        expect(data).toEqual({ kind: 'trade', id: TRADE_ID, attempt: 0 });
        expect(opts.jobId).toBe(`review-${TRADE_ID}`);
        expect(opts.jobId).not.toContain(':');
        expect(JSON.stringify(data)).not.toContain(OWNER);        // no user id leaves for the worker/LLM
    });

    it('does not queue twice while a fresh review is already pending (lost the atomic claim)', async () => {
        mockedTrade.findOne.mockResolvedValue(closedTrade({ aiReview: { status: 'PENDING', createdAt: new Date() } }));
        mockedTrade.findOneAndUpdate.mockResolvedValue(null);
        const res = await request(app).post(url());
        expect(res.status).toBe(202);
        expect(res.body.alreadyQueued).toBe(true);
        expect(mockedQueue.add).not.toHaveBeenCalled();
    });

    it('rolls the PENDING marker back and answers 503 when the queue is down', async () => {
        mockedTrade.findOne.mockResolvedValue(closedTrade());
        mockedTrade.findOneAndUpdate.mockResolvedValue(closedTrade());
        mockedQueue.add.mockRejectedValue(new Error('redis down'));
        jest.spyOn(console, 'error').mockImplementation(() => undefined);
        const res = await request(app).post(url());
        expect(res.status).toBe(503);
        expect(mockedTrade.updateOne).toHaveBeenCalledWith(
            { _id: TRADE_ID, 'aiReview.status': 'PENDING' }, { $unset: { aiReview: 1 } });
    });

    it('is rate limited per user (429 with Retry-After)', async () => {
        mockedRedis.incr.mockResolvedValue(21);                   // limit is 20/hour
        const res = await request(app).post(url());
        expect(res.status).toBe(429);
        expect(res.headers['retry-after']).toBeDefined();
        expect(mockedTrade.findOne).not.toHaveBeenCalled();
    });
});

describe('admin trade detail', () => {
    const run = async (id: string) => {
        const r: any = { statusCode: 200, body: undefined };
        r.status = (c: number) => { r.statusCode = c; return r; };
        r.json = (b: unknown) => { r.body = b; return r; };
        await getTradeByIdAdmin({ params: { tradeId: id } } as any, r);
        return r;
    };

    it('returns any trade including its aiReview', async () => {
        mockedTrade.findById.mockReturnValue({ populate: async () => closedTrade({ aiReview: { status: 'DONE' } }) });
        const r = await run(TRADE_ID);
        expect(r.body.data.aiReview.status).toBe('DONE');
    });

    it('404s on a missing trade and 400s on a bad id', async () => {
        mockedTrade.findById.mockReturnValue({ populate: async () => null });
        await expect(run(TRADE_ID)).rejects.toMatchObject({ statusCode: 404 });
        await expect(run('zzz')).rejects.toMatchObject({ statusCode: 400 });
    });
});

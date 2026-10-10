
import { describe, expect, it, jest } from '@jest/globals';
import { redis } from '../config/redis';
import { strategyQueue, tradeQueue } from '../queues';
import { User } from '../models/User';

jest.mock('../config/redis');
jest.mock('../queues');
jest.mock('../models/User');

const mockedRedis = jest.mocked(redis);
const mockedStrategyQueue = jest.mocked(strategyQueue);
const mockedTradeQueue = jest.mocked(tradeQueue);
const mockedUser = jest.mocked(User);

mockedRedis.incr.mockResolvedValue(1);
mockedRedis.expire.mockResolvedValue(1);
mockedRedis.ttl.mockResolvedValue(60);
mockedRedis.decr.mockResolvedValue(0);
mockedRedis.del.mockResolvedValue(1);
mockedRedis.get.mockResolvedValue(null);
mockedRedis.setex.mockResolvedValue('OK');
mockedRedis.options = {
    host: 'localhost',
    port: 6379
};

// Strategy Queue
mockedStrategyQueue.add.mockResolvedValue(undefined as any);
mockedStrategyQueue.getWaitingCount.mockResolvedValue(0);
mockedStrategyQueue.getActiveCount.mockResolvedValue(0);
mockedStrategyQueue.getFailedCount.mockResolvedValue(0);
mockedStrategyQueue.getCompletedCount.mockResolvedValue(0);

// Trade Queue
mockedTradeQueue.add.mockResolvedValue(undefined as any);
mockedTradeQueue.getWaitingCount.mockResolvedValue(0);
mockedTradeQueue.getActiveCount.mockResolvedValue(0);
mockedTradeQueue.getFailedCount.mockResolvedValue(0);
mockedTradeQueue.getCompletedCount.mockResolvedValue(0);

// User
mockedUser.findOne.mockReturnValue({
    // cast the mock to any to satisfy TS when resolving null
    select: jest.fn(async () => null),
} as any);

import request from "supertest";
import app from '../app';

describe('app', () => {
    it("GET /api/health returns 200 without authentication", async () => {
        const res = await request(app).get('/api/health');
        expect(res.status).toBe(200);
        expect(res.body.status).toBe("ok");
    });
});

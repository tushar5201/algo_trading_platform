import { beforeAll, describe, expect, it, jest } from '@jest/globals';

jest.mock('../config/redis', () => ({
    redis: { eval: jest.fn(async () => [1, 60]), get: jest.fn(async () => null), setex: jest.fn(), del: jest.fn(), decr: jest.fn() },
}));
jest.mock('../queues', () => ({ strategyQueue: {}, tradeQueue: {}, aiAnalysisQueue: {} }));
jest.mock('../models/User');

import type { FastifyInstance } from 'fastify';
import { buildApp } from '../app';

describe('app', () => {
    let app: FastifyInstance;
    beforeAll(async () => { app = await buildApp(); await app.ready(); });

    it('GET /api/health returns 200 without authentication', async () => {
        const res = await app.inject({ method: 'GET', url: '/api/health' });
        expect(res.statusCode).toBe(200);
        expect(res.json().status).toBe('ok');
    });

    it('unknown routes return a JSON 404', async () => {
        const res = await app.inject({ method: 'GET', url: '/api/nope' });
        expect(res.statusCode).toBe(404);
        expect(res.json()).toEqual({ message: 'Route not found' });
    });

    it('an empty body with a JSON content type is accepted (same as express.json)', async () => {
        const res = await app.inject({ method: 'POST', url: '/api/auth/login', headers: { 'content-type': 'application/json' } });
        expect(res.statusCode).toBe(400);               // reaches the controller, which rejects the missing fields
        expect(res.json().message).toBeDefined();
    });

    it('invalid JSON is a 400, not a 500', async () => {
        const res = await app.inject({ method: 'POST', url: '/api/auth/login', headers: { 'content-type': 'application/json' }, payload: '{bad' });
        expect(res.statusCode).toBe(400);
        expect(res.json().message).toMatch(/Invalid JSON/);
    });

    it('bodies over 10kb are rejected with 413', async () => {
        const res = await app.inject({ method: 'POST', url: '/api/auth/login', headers: { 'content-type': 'application/json' },
            payload: JSON.stringify({ email: 'a@b.com', password: 'x'.repeat(20_000) }) });
        expect(res.statusCode).toBe(413);
    });

    it('sends security headers and no x-powered-by', async () => {
        const res = await app.inject({ method: 'GET', url: '/api/health' });
        expect(res.headers['x-powered-by']).toBeUndefined();
        expect(res.headers['x-content-type-options']).toBe('nosniff');
    });

    it('answers CORS preflight', async () => {
        const res = await app.inject({ method: 'OPTIONS', url: '/api/auth/login',
            headers: { origin: 'http://localhost:5173', 'access-control-request-method': 'POST' } });
        expect(res.statusCode).toBe(204);
        expect(res.headers['access-control-allow-origin']).toBeDefined();
    });

    it('uses X-Forwarded-For from a private proxy (nginx) as the client ip', async () => {
        let seen = '';
        const probe = await buildApp();
        probe.get('/api/ip', async (req) => { seen = req.ip; return {}; });
        await probe.inject({ method: 'GET', url: '/api/ip', remoteAddress: '172.18.0.5', headers: { 'x-forwarded-for': '203.0.113.9' } });
        expect(seen).toBe('203.0.113.9');
        await probe.close();
    });

    it('ignores X-Forwarded-For from a public address (cannot be spoofed)', async () => {
        let seen = '';
        const probe = await buildApp();
        probe.get('/api/ip', async (req) => { seen = req.ip; return {}; });
        await probe.inject({ method: 'GET', url: '/api/ip', remoteAddress: '198.51.100.7', headers: { 'x-forwarded-for': '203.0.113.9' } });
        expect(seen).toBe('198.51.100.7');
        await probe.close();
    });
});

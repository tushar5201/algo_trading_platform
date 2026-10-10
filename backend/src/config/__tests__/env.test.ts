import { afterAll, beforeEach, describe, expect, it, jest } from "@jest/globals";

describe('config/env', () => {
    const ORIGINAL_ENV = process.env;
    beforeEach(() => {
        jest.resetModules();
        process.env = { ...ORIGINAL_ENV };
    });

    afterAll(() => {
        process.env = ORIGINAL_ENV;
    });

    it('parse valid env vars & expose them on env', () => {
        process.env.JWT_SECRET = 'a'.repeat(32);
        process.env.MONGO_URI = 'mongodb://localhost:27017/db';
        process.env.AES_SECRET_KEY = 'b'.repeat(32);

        const { env } = require('../env');
        expect(env.JWT_SECRET).toBe('a'.repeat(32));
        expect(env.NODE_ENV).toBe('test');
    });

    it('applies defaulrs for optional fields (PORT, NODE_ENV, JWT_EXPIRES_IN, REDIS_HOST/PORT)', () => {
        process.env = {
            JWT_SECRET: 'a'.repeat(32),
            MONGO_URI: 'mongodb://localhost:27017/db',
            AES_SECRET_KEY: 'b'.repeat(32)
        } as any;

        const { env } = require('../env');
        expect(env.PORT).toBe('3000');
        expect(env.JWT_EXPIRES_IN).toBe('7d');
        expect(env.REDIS_HOST).toBe('redis');
        expect(env.REDIS_PORT).toBe('6379');
    });

    it('exits the process when JWT_SECRET is too short', () => {
        process.env.JWT_SECRET = 'too-short';
        process.env.MONGO_URI = 'mongodb://localhost:27017/db';
        process.env.AES_SECRET_KEY = 'b'.repeat(32);

        const exitSpy = jest.spyOn(process, 'exit').mockImplementation(((_code?: number) => {
            throw new Error('process.exit called');
        }) as never);
        jest.spyOn(console, 'log').mockImplementation(() => undefined);
        expect(() => require('../env')).toThrow('process.exit called');
        expect(exitSpy).toHaveBeenCalledWith(1);
        exitSpy.mockRestore();
    });

    it('exits the process when AES_SECRET_KEY is not exactly 32 characters', () => {
        process.env.JWT_SECRET = 'a'.repeat(32);
        process.env.MONGO_URI = 'mongodb://localhost:27017/db';
        process.env.AES_SECRET_KEY = 'too-short';

        const exitSpy = jest.spyOn(process, 'exit').mockImplementation(((_code?: number) => {
            throw new Error('process.exit called');
        }) as never);
        jest.spyOn(console, 'log').mockImplementation(() => undefined);

        expect(() => require('../env')).toThrow('process.exit called');
        expect(exitSpy).toHaveBeenCalledWith(1);
        exitSpy.mockRestore();
    });

    it('exits the process when MONGO_URI is missing', () => {
        process.env = {
            JWT_SECRET: 'a'.repeat(32),
            AES_SECRET_KEY: 'b'.repeat(32),
        } as any;

        const exitSpy = jest.spyOn(process, 'exit').mockImplementation(((_code?: number) => {
            throw new Error('process.exit called');
        }) as never);
        jest.spyOn(console, 'log').mockImplementation(() => undefined);

        expect(() => require('../env')).toThrow('process.exit called');
        exitSpy.mockRestore();
    });
});

import { describe, expect, it, jest } from "@jest/globals";
import { AppError, errorHandler } from "../errorhandler";

function run(err: Error) {
    const res: any = { headersSent: false, statusCode: 0, body: undefined as any };
    res.status = (c: number) => { res.statusCode = c; return res; };
    res.json = (b: unknown) => { res.body = b; return res; };
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    errorHandler(err, {} as any, res, jest.fn() as any);
    return res;
}
const named = (name: string, extra: object = {}) => Object.assign(new Error('boom'), { name }, extra);

describe('errorHandler', () => {
    it('AppError -> its own status', () => {
        const r = run(new AppError(418, 'teapot'));
        expect(r.statusCode).toBe(418);
        expect(r.body.message).toBe('teapot');
    });

    it('ZodError -> 400 (used to be 500)', () => {
        const r = run(named('ZodError', { issues: [{ message: 'bad field' }] }));
        expect(r.statusCode).toBe(400);
        expect(r.body.message).toBe('bad field');
    });

    it('CastError (bad ObjectId) -> 400 (used to be 500)', () => {
        expect(run(named('CastError')).statusCode).toBe(400);
    });

    it('duplicate key on email -> 409 "Email already exists"', () => {
        const r = run(named('MongoServerError', { code: 11000, keyPattern: { email: 1 } }));
        expect(r.statusCode).toBe(409);
        expect(r.body.message).toBe('Email already exists');
    });

    it('duplicate key on another field does NOT claim the email exists', () => {
        const r = run(named('MongoServerError', { code: 11000, keyPattern: { name: 1 } }));
        expect(r.statusCode).toBe(409);
        expect(r.body.message).not.toMatch(/email/i);
    });

    it('a non-duplicate MongoServerError is a 500, not a 409', () => {
        expect(run(named('MongoServerError', { code: 121 })).statusCode).toBe(500);
    });

    it('unknown errors -> 500 without leaking details', () => {
        const r = run(new Error('secret internals'));
        expect(r.statusCode).toBe(500);
        expect(r.body.message).toBe('Internal Server Error');
    });
});

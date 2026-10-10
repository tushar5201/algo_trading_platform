import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock('../../models/User');

import { User } from "../../models/User";
import { authenticate } from "../auth";
import { signToken } from "../../services/auth.service";

const mockedUser = jest.mocked(User);

function makeRes() {
    const res: any = { statusCode: 200, body: undefined as any };
    res.status = (c: number) => { res.statusCode = c; return res; };
    res.json = (b: unknown) => { res.body = b; return res; };
    return res;
}

function tokenFor(role = 'user') {
    return signToken({ _id: { toString: () => '507f1f77bcf86cd799439011' }, role, email: 'a@b.c' } as any);
}

const stubAccount = (acct: any) =>
    mockedUser.findById.mockReturnValue({ select: jest.fn(async () => acct) } as any);

describe('authenticate', () => {
    beforeEach(() => { jest.clearAllMocks(); });

    it('401 without a bearer token', async () => {
        const res = makeRes(); const next = jest.fn();
        await authenticate({ headers: {} } as any, res, next);
        expect(res.statusCode).toBe(401);
        expect(next).not.toHaveBeenCalled();
    });

    it('401 for a garbage token', async () => {
        const res = makeRes(); const next = jest.fn();
        await authenticate({ headers: { authorization: 'Bearer nope' } } as any, res, next);
        expect(res.statusCode).toBe(401);
    });

    it('401 when the account was deactivated after the token was issued', async () => {
        stubAccount({ role: 'user', status: 'APPROVED', isActive: false, email: 'a@b.c' });
        const res = makeRes(); const next = jest.fn();
        await authenticate({ headers: { authorization: `Bearer ${tokenFor()}` } } as any, res, next);
        expect(res.statusCode).toBe(401);
        expect(next).not.toHaveBeenCalled();
    });

    it('401 when the account was rejected after the token was issued', async () => {
        stubAccount({ role: 'user', status: 'REJECTED', isActive: true, email: 'a@b.c' });
        const res = makeRes(); const next = jest.fn();
        await authenticate({ headers: { authorization: `Bearer ${tokenFor()}` } } as any, res, next);
        expect(res.statusCode).toBe(401);
    });

    it('uses the role from the database, not the token', async () => {
        stubAccount({ role: 'user', status: 'APPROVED', isActive: true, email: 'a@b.c' });
        const req: any = { headers: { authorization: `Bearer ${tokenFor('admin')}` } };
        const next = jest.fn();
        await authenticate(req, makeRes(), next);
        expect(next).toHaveBeenCalledWith();
        expect(req.user.role).toBe('user');
    });
});

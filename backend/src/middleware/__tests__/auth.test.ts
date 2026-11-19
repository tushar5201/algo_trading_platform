import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock('../../services/auth.cache.service', () => ({ getAccount: jest.fn() }));

import { getAccount } from "../../services/auth.cache.service";
import { authenticate } from "../auth";
import { signToken } from "../../services/auth.service";

const mockedGetAccount = getAccount as jest.Mock<(...a: any[]) => any>;
const ID = '507f1f77bcf86cd799439011';

function tokenFor(role = 'user') {
    return signToken({ _id: { toString: () => ID }, role, email: 'a@b.c' } as any);
}
const bearer = (token: string) => ({ headers: { authorization: `Bearer ${token}` } }) as any;

describe('authenticate', () => {
    beforeEach(() => { mockedGetAccount.mockReset(); });

    it('401 without a bearer token', async () => {
        await expect(authenticate({ headers: {} } as any)).rejects.toMatchObject({ statusCode: 401, message: 'No token provided' });
        expect(mockedGetAccount).not.toHaveBeenCalled();
    });

    it('401 for a non-bearer scheme', async () => {
        await expect(authenticate({ headers: { authorization: 'Basic abc' } } as any)).rejects.toMatchObject({ statusCode: 401 });
    });

    it('401 for a garbage token, without touching the account store', async () => {
        await expect(authenticate(bearer('nope'))).rejects.toMatchObject({ statusCode: 401, message: 'Invalid token' });
        expect(mockedGetAccount).not.toHaveBeenCalled();
    });

    it('401 when the account no longer exists', async () => {
        mockedGetAccount.mockResolvedValue(null);
        await expect(authenticate(bearer(tokenFor()))).rejects.toMatchObject({ statusCode: 401 });
    });

    it('401 when the account was deactivated after the token was issued', async () => {
        mockedGetAccount.mockResolvedValue({ role: 'user', status: 'APPROVED', isActive: false, email: 'a@b.c' });
        await expect(authenticate(bearer(tokenFor()))).rejects.toMatchObject({ statusCode: 401, message: 'Account is not active' });
    });

    it('401 when the account was rejected after the token was issued', async () => {
        mockedGetAccount.mockResolvedValue({ role: 'user', status: 'REJECTED', isActive: true, email: 'a@b.c' });
        await expect(authenticate(bearer(tokenFor()))).rejects.toMatchObject({ statusCode: 401 });
    });

    it('401 for a pending account', async () => {
        mockedGetAccount.mockResolvedValue({ role: 'user', status: 'PENDING', isActive: true, email: 'a@b.c' });
        await expect(authenticate(bearer(tokenFor()))).rejects.toMatchObject({ statusCode: 401 });
    });

    it('uses the role from the account, not the token', async () => {
        mockedGetAccount.mockResolvedValue({ role: 'user', status: 'APPROVED', isActive: true, email: 'db@b.c' });
        const req = bearer(tokenFor('admin'));
        await authenticate(req);
        expect(req.user).toEqual({ userId: ID, role: 'user', email: 'db@b.c' });
        expect(mockedGetAccount).toHaveBeenCalledWith(ID);
    });
});

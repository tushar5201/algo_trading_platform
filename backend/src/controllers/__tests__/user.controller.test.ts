import { beforeEach, describe, expect, it, jest } from "@jest/globals";

jest.mock('../../models/User');
jest.mock('../../config/redis', () => ({ redis: { setex: jest.fn(), get: jest.fn(), del: jest.fn(), ttl: jest.fn() } }));
jest.mock('../../services/audit.service', () => ({ writeAuditLog: jest.fn() }));

import { User } from "../../models/User";
import { redis } from "../../config/redis";
import { submitTOTP, toggleAutoTrading } from "../user.controller";

const mockedUser = jest.mocked(User);

function res() {
    const r: any = { statusCode: 200, body: undefined as any };
    r.status = (c: number) => { r.statusCode = c; return r; };
    r.json = (b: unknown) => { r.body = b; return r; };
    return r;
}

/**
 * Mimics Mongoose: `encryptedCredentials` is `select: false`, so it is only present on the
 * document when the query explicitly asks for '+encryptedCredentials'.
 */
function stubFindById(stored: { status: string; creds: string | null }) {
    const make = (withCreds: boolean) => ({
        _id: 'u1', status: stored.status, autoTrading: false,
        encryptedCredentials: withCreds ? stored.creds : undefined,
        save: jest.fn(async () => undefined),
    });
    mockedUser.findById.mockImplementation(((_id: unknown) => {
        const plain: any = make(false);
        plain.select = (fields: string) => make(String(fields).includes('+encryptedCredentials'));
        // awaiting the query without .select() resolves to the doc WITHOUT the hidden field
        plain.then = (ok: (v: unknown) => unknown) => ok(make(false));
        return plain;
    }) as any);
}

describe('user.controller with select:false credentials', () => {
    beforeEach(() => { jest.clearAllMocks(); });

    it('toggleAutoTrading works for an approved user who has saved credentials', async () => {
        stubFindById({ status: 'APPROVED', creds: '{"iv":"..","tag":"..","data":".."}' });
        const r = res();
        await toggleAutoTrading({ user: { userId: 'u1' } } as any, r);   // used to throw 400 "No credentials found"
        expect(r.statusCode).toBe(200);
        expect(r.body.data.autoTrading).toBe(true);
    });

    it('toggleAutoTrading still refuses a user with no credentials', async () => {
        stubFindById({ status: 'APPROVED', creds: null });
        await expect(toggleAutoTrading({ user: { userId: 'u1' } } as any, res())).rejects.toThrow(/credentials/i);
    });

    it('submitTOTP reaches the Kotak hand-off when credentials exist', async () => {
        stubFindById({ status: 'APPROVED', creds: '{"iv":"..","tag":"..","data":".."}' });
        const body = { accessToken: 'dashboard-token-123', mobileNumber: '+919999999999', ucc: 'ABCDE', totp: '123456' };
        const r = res();
        await submitTOTP({ user: { userId: 'u1' }, body } as any, r);   // used to throw 400 "Save your Kotak credentials first"
        expect((redis.setex as any)).toHaveBeenCalled();
        expect(r.statusCode).toBe(200);
    });
});

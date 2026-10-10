import { IUser } from "../../models/User";
import mongoose from "mongoose";
import { describe, expect, it } from "@jest/globals"
import { comparePassword, hashPassword, signToken, verifyToken } from "../auth.service";

const makeUser = (overrides: Partial<IUser> = {}): IUser => ({
    _id: new mongoose.Types.ObjectId(),
    role: 'user',
    email: 'trader@example.com',
    ...overrides
} as IUser);

describe('auth.service', () => {
    describe('hashPassword / comparePassword', () => {
        it('produces a bcrypt hashdifferent form the plaintext password', async () => {
            const hash = await hashPassword('mySecretPass123');
            expect(hash).not.toEqual('mySecretPass123');
            expect(hash).toMatch(/^\$2[aby]\$/);
        });

        it('validate a correct password against its hash', async () => {
            const hash = await hashPassword('correct-horse-battery-staple');
            await expect(comparePassword('correct-horse-battery-staple', hash)).resolves.toBe(true);
        });

        it('rejects an incorrect password', async () => {
            const hash = await hashPassword('correct-password');
            await expect(comparePassword('wrong-password', hash)).resolves.toBe(false);
        });

        it('produces different hashes for the same password (random salt)', async () => {
            const [a, b] = await Promise.all([hashPassword('same-pw'), hashPassword('same-pw')]);
            expect(a).not.toEqual(b);
        });
    });

    describe('signToken / verifyToken', () => {
        it('round-trips the user id, role, and email through the JWT', () => {
            const user = makeUser({ role: 'admin', email: 'admin@example.com' });
            const token = signToken(user);
            const payload = verifyToken(token);
            expect(payload.userId).toBe(user._id.toString());
            expect(payload.role).toBe('admin');
            expect(payload.email).toBe('admin@example.com');
        });

        it('throws when verifying a malformed token', () => {
            expect(() => verifyToken('not-a-real-token')).toThrow();
        });

        it('throws when verifying a token signed with a diff secret', () => {
            const jwt = require('jsonwebtoken');
            const foreignToken = jwt.sign({userId: 'x', role: 'user', email: 'a@b.com'}, 'wrong-secret');
            expect(() => verifyToken(foreignToken)).toThrow();
        });
    });
});

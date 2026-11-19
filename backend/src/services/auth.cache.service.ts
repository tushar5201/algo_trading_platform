import { redis } from "../config/redis";
import { User } from "../models/User";

export const AUTH_CACHE_TTL_SECONDS = 30;

export interface CachedAccount {
    role: string;
    status: string;
    isActive: boolean;
    email: string;
}

const key = (userId: string): string => `auth:acct:${userId}`;

export async function getAccount(userId: string): Promise<CachedAccount | null> {
    try {
        const raw = await redis.get(key(userId));
        if (raw !== null && raw !== undefined) return JSON.parse(raw) as CachedAccount | null;
    } catch {
    }

    const doc = await User.findById(userId).select("role status isActive email");
    const account: CachedAccount | null = doc
        ? { role: doc.role, status: doc.status, isActive: doc.isActive, email: doc.email }
        : null;

    try {
        await redis.setex(key(userId), AUTH_CACHE_TTL_SECONDS, JSON.stringify(account));
    } catch {
    }
    return account;
}

export async function invalidateAccount(userId: string): Promise<void> {
    try {
        await redis.del(key(userId));
    } catch (err) {
        console.error("[AuthCache] invalidate failed:", (err as Error).message);
    }
}

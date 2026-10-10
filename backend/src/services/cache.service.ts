import { redis } from "../config/redis";

export async function getOrSetCache<T>(
    key: string,
    ttlSeconds: number,
    compute: () => Promise<T>
): Promise<{ data: T; cached: boolean }> {
    try {
        const cached = await redis.get(key);
        if (cached)
            return { data: JSON.parse(cached) as T, cached: true };
    } catch (error) {
        console.error(`[Cache] read fialed for key "${key}": `, error);
    }

    const data = await compute();
    try {
        await redis.setex(key, ttlSeconds, JSON.stringify(data));
    } catch (error) {
        console.error(`[Cache] Write failed for key "${key}: "`);
    }
    return { data, cached: false };
}

export async function invalidateCache(key:string): Promise<void> {
    try {
        await redis.del(key);
    } catch (error) {
        console.error(`[Cache] Invalidation failed for key "${key}: "`, error);
    }
}

export const cacheKeys = {
    userPnl: (userId: string): string => `cache:pnl:${userId}`
}

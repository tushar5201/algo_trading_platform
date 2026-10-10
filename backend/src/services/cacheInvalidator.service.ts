import {Trade} from "../models/Trade";
import {cacheKeys, invalidateCache} from "./cache.service";

const INITIAL_RETRY_DELAY_MS = 1_000;
const MAX_RETRY_DELAY_MS = 60_000;
const STABLE_AFTER_MS = 30_000;

type TradeChangeStream = ReturnType<typeof Trade.watch>;

interface TradeChangeEvent {
    operationType: string;
    fullDocument?: {
        _id: unknown;
        userId?: { toString(): string };
        status?: string;
    };
}

let retryDelayMS = INITIAL_RETRY_DELAY_MS;
let activeStream: TradeChangeStream | null = null;
let restartTimer: NodeJS.Timeout | null = null;
let stopped = false;
let startedAt = 0;

export function isUnsupported(error: Error): boolean {
    const code = (error as any).code;
    return code === 40573 || code === 40324 || /replica set|only supported on/i.test(error.message);
}

export function startTradeCacheInvalidator(): void {
    stopped = false;
    const relevant = ['EXECUTED', 'EXIT_PENDING', 'CLOSED', 'CANCELED', 'FAILED', 'NEEDS_REVIEW'];
    const pipeline = [
        {
            $match: {
                $or: [
                    {operationType: "insert"},
                    {operationType: "replace"},
                    {operationType: "update", "updateDescription.updateFields.status": {$in: relevant}},
                    {operationType: "update", "updateDescription.updateFields.pnl": {$exists: true}},
                ]
            }
        }
    ];

    let changeStream : TradeChangeStream;
    try {
        changeStream = Trade.watch(pipeline, {fullDocument: "updateLookup"});
    } catch (e) {
        console.error("[CacheInvalidator] Could not open change stream: ", (e as Error).message);
        scheduleRestart();
        return;
    }

    activeStream = changeStream;
    startedAt = Date.now();

    changeStream.on("change", async (change: TradeChangeEvent) => {
        try {
            const userId = change.fullDocument?.userId?.toString();
            if (!userId)
                return;
            await invalidateCache(cacheKeys.userPnl(userId));
            console.log(`[CacheInvalidator] Invalidate PnL cache for user ${userId} (trade ${change.fullDocument?._id})`);
        } catch (error) {
            console.error("[CacheInvalidator] Failed to process change event: ", error);
        }
    });

    changeStream.on("error", (error: Error) => {
        if(isUnsupported(error)) {
            console.warn("[CacheInvalidator] Change streams unsupported (no replica set). Falling back to TTL only PnL cache.");
            teardown();
            return;
        }
        console.error("[CacheInvalidator] Change stream error, will reconnect: ", error);
        scheduleRestart();
    });

    changeStream.on("close", () => {
        console.warn("[CacheInvalidator] Change stream closed, will reconnect.");
        scheduleRestart();
    });

    console.log("[CacheInvalidator] Watching Trade collection.");
}

function teardown(): void {
    const stream = activeStream;
    activeStream = null;
    if(stream) {
        stream.removeAllListeners();
        stream.on("error", () => undefined);
        stream.close().catch(() => undefined);
    }
}

function scheduleRestart(): void {
    if(stopped || restartTimer) return;
    if(startedAt && Date.now() - startedAt > STABLE_AFTER_MS) retryDelayMS = INITIAL_RETRY_DELAY_MS;
    teardown();

    const delay = retryDelayMS;
    retryDelayMS = Math.min(retryDelayMS * 2, MAX_RETRY_DELAY_MS);
    restartTimer = setTimeout(() => {
        restartTimer = null;
        startTradeCacheInvalidator();
    }, delay);
}

export function stopTradeCacheInvalidator(): void {
    stopped = true;
    if (restartTimer) {
        clearTimeout(restartTimer);
        restartTimer = null;
    }
    teardown();
}


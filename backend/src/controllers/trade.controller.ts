import {Request, Response} from "express";
import {Trade, TRADE_STATUSES} from "../models/Trade";
import mongoose from "mongoose";
import {AppError} from "../middleware/errorhandler";
import {cacheKeys, getOrSetCache} from "../services/cache.service";
import {assertObjectId, parsePagination, queryString} from "../utils/pagination";

const PNL_CACEH_TTL_SECONDS = 60;
const REPORT_TZ = 'Asia/Kolkata';

function buildTradeFilter(query: Record<string, unknown>, base: Record<string, unknown>): Record<string, unknown> {
    const filter = {...base};
    const status = queryString(query.status);
    if (status) {
        if (!(TRADE_STATUSES as string[]).includes(status)) throw new AppError(400, "Invalid status filter");
    }
    const strategy = queryString(query.strategy);
    if (strategy) filter.strategyName = strategy;
    return filter;
}

export const getMyTrades = async (req: Request, res: Response): Promise<void> => {
    const {page, limit, skip} = parsePagination(req.query as Record<string, unknown>);
    const filter = buildTradeFilter(req.query as Record<string, unknown>, {
        userId: new mongoose.Types.ObjectId(req.user!.userId)
    });

    const [trades, total] = await Promise.all([
        Trade.find(filter).sort({createdAt: -1}).skip(skip).limit(limit),
        Trade.countDocuments(filter)
    ]);
    res.json({
        success: true,
        data: {trades, total, page, totalPages: Math.ceil(total / limit)}
    });
};

interface PnlPayload {
    summary: {
        totalPnl: number;
        totalTrades: number;
        winTrades: number;
        lossTrades: number;
        winRate: string;
        avgPnl: number;
        bestTrade: number;
        worstTrade: number;
        openPositions: number;
    };
    byStrategy: unknown[];
    byDay: unknown[];
}

const closedWithPnl = (userId: mongoose.Types.ObjectId) => ({
    userId,
    status: 'CLOSED',
    pnl: {$ne: null}
});

async function computeMyPnl(userId: mongoose.Types.ObjectId): Promise<PnlPayload> {
    const closedAtOrCreated = {$ifNull: ['$closedAt', '$createdAt']};

    const [summary, byStrategy, byDay, openPositions] = await Promise.all([
        // Overall summary
        Trade.aggregate([
            {$match: closedWithPnl(userId)},
            {
                $group: {
                    _id: null,
                    totalPnl: {$sum: '$pnl'},
                    totalTrades: {$sum: 1},
                    winTrades: {$sum: {$cond: [{$gt: ['$pnl', 0]}, 1, 0]}},
                    lossTrades: {$sum: {$cond: [{$lt: ['$pnl', 0]}, 1, 0]}},
                    avgPnl: {$avg: '$pnl'},
                    bestTrade: {$max: '$pnl'},
                    worstTrade: {$min: '$pnl'},
                }
            }
        ]),
        // PnL by strategy
        Trade.aggregate([
            {$match: closedWithPnl(userId)},
            {
                $group: {
                    _id: '$strategyName',
                    pnl: {$sum: '$pnl'},
                    trades: {$count: {}},
                    winTrades: {$sum: {$cond: [{$gt: ['$pnl', 0]}, 1, 0]}},
                }
            },
            {$sort: {pnl: -1}}
        ]),
        // PnL by day (last 30 days)
        Trade.aggregate([
            {
                $match: {
                    ...closedWithPnl(userId),
                    closedAt: {$gte: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)},
                }
            },
            {
                $group: {
                    _id: {
                        $dateToString: {format: '%Y-%m-%d', date: closedAtOrCreated, timezone: REPORT_TZ}
                    },
                    pnl: {$sum: '$pnl'},
                    trades: {$sum: 1},
                }
            },
            {$sort: {_id: 1}}
        ]),
        Trade.countDocuments({userId, status: {$in: ['EXECUTED', 'EXIT_PENDING']}},),
    ]);
    const s = summary[0] ?? {
        totalPnl: 0, totalTrades: 0, winTrades: 0,
        lossTrades: 0, avgPnl: 0, bestTrade: 0, worstTrade: 0,
    };
    const winRate = s.totalTrades > 0
        ? ((s.winTrades / s.totalTrades) * 100).toFixed(1)
        : '0';
    return {
        summary: {
            totalPnl: s.totalPnl,
            totalTrades: s.totalTrades,
            winTrades: s.winTrades,
            lossTrades: s.lossTrades,
            winRate: `${winRate}%`,
            avgPnl: s.avgPnl ?? 0,
            bestTrade: s.bestTrade ?? 0,
            worstTrade: s.worstTrade ?? 0,
            openPositions
        },
        byStrategy,
        byDay
    };
}

export const getMyPnl = async (req: Request, res: Response): Promise<void> => {
    const rawUserId = req.user!.userId;
    const userId = new mongoose.Types.ObjectId(rawUserId);

    const {data, cached} = await getOrSetCache(
        cacheKeys.userPnl(rawUserId),
        PNL_CACEH_TTL_SECONDS,
        () => computeMyPnl(userId)
    );
    res.json({
        success: true,
        cached,
        data
    });
};

// Admin: view all trades
export const getAllTrades = async (req: Request, res: Response): Promise<void> => {
    const {page, limit, skip} = parsePagination(req.query as Record<string, unknown>);

    const base: Record<string, unknown> = {};
    const userId = queryString(req.query.userId);
    if(userId) base.userId = new mongoose.Types.ObjectId(assertObjectId(userId, 'user id'));
    const filter = buildTradeFilter(req.query as Record<string, unknown>, base)

    const [trades, total] = await Promise.all([
        Trade.find(filter)
            .populate('userId', 'name email')
            .sort({createdAt: -1})
            .skip(skip)
            .limit(limit),
        Trade.countDocuments(filter),
    ]);
    res.json({success: true, data: {trades, total, page, totalPages: Math.ceil(total / limit)}});
};

// Signal trade detail
export const getTradeById = async (req: Request, res: Response): Promise<void> => {
    const tradeId = assertObjectId(req.params.tradeId, 'trade id');

    const trade = await Trade.findOne({
        _id: tradeId,
        userId: req.user!.userId,
    });
    if (!trade) throw new AppError(404, 'Trade not found');
    res.json({success: true, data: trade});
};
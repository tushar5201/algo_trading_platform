import { Request, Response } from "express";
import { Trade } from "../models/Trade";
import mongoose from "mongoose";
import { AppError } from "../middleware/errorhandler";

export const getMyTrades = async (req: Request, res: Response): Promise<void> => {
    const { page = '1', limit = '20', status, strategy } = req.query;
    const skip = (parseInt(page as string) - 1) * parseInt(limit as string);

    const filter: Record<string, unknown> = { userId: new mongoose.Types.ObjectId(req.user!.userId) };
    if (status) filter.status = status;
    if (strategy) filter.strategyName = strategy;

    const [trades, total] = await Promise.all([
        Trade.find(filter).sort({ createdAt: -1 }).skip(skip).limit(parseInt(limit as string)),
        Trade.countDocuments(filter)
    ]);
    res.json({
        success: true,
        data: { trades, total, page: parseInt(page as string), totalPages: Math.ceil(total / parseInt(limit as string)) }
    });
}

export const getMyPnl = async (req: Request, res: Response): Promise<void> => {
    const userId = new mongoose.Types.ObjectId(req.user!.userId);
    const [summary, byStrategy, byDay] = await Promise.all([
        // Overall summary
        Trade.aggregate([
            { $match: { userId, status: 'EXECUTED' } },
            {
                $group: {
                    _id: null,
                    totalPnl: { $sum: '$pnl' },
                    totalTrades: { $count: {} },
                    winTrades: { $sum: { $cond: [{ $gt: ['$pnl', 0] }, 1, 0] } },
                    lossTrades: { $sum: { $cond: [{ $lt: ['$pnl', 0] }, 1, 0] } },
                    avgPnl: { $avg: '$pnl' },
                    bestTrade: { $max: '$pnl' },
                    worstTrade: { $min: '$pnl' },
                }
            }
        ]),
        // PnL by strategy
        Trade.aggregate([
            { $match: { userId, status: 'EXECUTED' } },
            {
                $group: {
                    _id: '$strategyName',
                    pnl: { $sum: '$pnl' },
                    trades: { $count: {} },
                    winTrades: { $sum: { $cond: [{ $gt: ['$pnl', 0] }, 1, 0] } },
                }
            },
            { $sort: { pnl: -1 } }
        ]),
        // PnL by day (last 30 days)
        Trade.aggregate([
            {
                $match: {
                    userId,
                    status: 'EXECUTED',
                    createdAt: { $gte: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000) },
                }
            },
            {
                $group: {
                    _id: {
                        $dateToString: { format: '%Y-%m-%d', date: '$createdAt' },
                    },
                    pnl: { $sum: '$pnl' },
                    trades: { $count: {} },
                }
            },
            { $sort: { _id: 1 } }
        ])
    ]);

    const s = summary[0] ?? {
        totalPnl: 0, totalTrades: 0, winTrades: 0,
        lossTrades: 0, avgPnl: 0, bestTrade: 0, worstTrade: 0,
    };
    const winRate = s.totalTrades > 0
        ? ((s.winTrades / s.totalTrades) * 100).toFixed(1)
        : '0';
    res.json({
        success: true,
        data: {
            summary: {
                totalPnl: s.totalPnl,
                totalTrades: s.totalTrades,
                winTrades: s.winTrades,
                lossTrades: s.lossTrades,
                winRate: `${winRate}%`,
                avgPnl: s.avgPnl,
                bestTrade: s.bestTrade,
                worstTrade: s.worstTrade,
            },
            byStrategy,
            byDay
        }
    });
};

// Admin: view all trades
export const getAllTrades = async (req: Request, res: Response): Promise<void> => {
    const { page = '1', limit = '20', userId, status, strategy } = req.query;
    const skip = (parseInt(page as string) - 1) * parseInt(limit as string);

    const filter: Record<string, unknown> = {};
    if (userId) filter.userId = new mongoose.Types.ObjectId(userId as string);
    if (status) filter.status = status;
    if (strategy) filter.strategyName = strategy;

    const [trades, total] = await Promise.all([
        Trade.find(filter)
            .populate('userId', 'name email')
            .skip(skip)
            .limit(parseInt(limit as string))
            .sort({ createdAt: -1 }),
        Trade.countDocuments(filter),
    ]);
    res.json({ success: true, data: { trades, total, page: parseInt(page as string) } });
};

// Signal trade detail
export const getTradeById = async (req: Request, res: Response): Promise<void> => {
    const trade = await Trade.findOne({
        _id: req.params.tradeId,
        userId: req.user!.userId,
    });
    if (!trade) throw new AppError(404, 'Trade not found');
    res.json({ success: true, data: trade });
};
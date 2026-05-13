import { Request, Response } from "express";
import { Trade } from "../models/Trade";

export const getMyTrades = async (req: Request, res: Response): Promise<void> => {
    const { page = '1', limit = '20', status } = req.query;
    const skip = (parseInt(page as string) - 1) * parseInt(limit as string);

    const filter: Record<string, unknown> = { userId: req.user!.userId };
    if (status) filter.status = status;

    const [trades, total] = await Promise.all([
        Trade.find(filter).skip(skip).limit(parseInt(limit as string)).sort({ createdAt: -1 }),
        Trade.countDocuments(filter)
    ]);
    res.json({ success: true, data: { trades, total } });
}

export const getMyPnl = async (req: Request, res: Response): Promise<void> => {
    const result = await Trade.aggregate([
        { $match: { userId: req.user!.userId, status: 'EXECUTED' } },
        {
            $group: {
                _id: null,
                totalPnl: { $sum: '$pnl' },
                totalTrades: { $count: {} },
                winTrades: { $sum: { $cond: [{ $gt: ['$pnl', 0] }, 1, 0] } },
            }
        }
    ]);
    const summary = result[0] ?? { totalPnl: 0, totalTrades: 0, winTrades: 0 };
    res.json({ success: true, data: summary });
}

// Admin: view all trades
export const getAllTrades = async (req: Request, res: Response): Promise<void> => {
    const { page = '1', limit = '20', status, userId } = req.query;
    const skip = (parseInt(page as string) - 1) * parseInt(limit as string);

    const filter: Record<string, unknown> = {};
    if (userId) filter.userId = userId;

    const [trades, total] = await Promise.all([
        Trade.find(filter).populate('userId', 'name email').skip(skip).limit(parseInt(limit as string)).sort({ createdAt: -1 }),
        Trade.countDocuments(filter),
    ]);
    res.json({ success: true, data: { trades, total } });
}

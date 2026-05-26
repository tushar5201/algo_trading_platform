import { Request, Response } from "express";
import { User } from "../models/User";
import z from "zod";
import { AppError } from "../middleware/errorhandler";
import { writeAuditLog } from "../services/audit.service";
import { Strategy } from "../models/Strategy";
import { publishStrategyRun } from "../services/queue.service";
import { strategyQueue, tradeQueue } from "../queues";
import { success } from "zod/v4";
import { KillSwitch } from "../models/KillSwitch";
import mongoose from "mongoose";

export const getAllUsers = async (req: Request, res: Response) => {
    const { status, page = '1', limit = '20' } = req.query;

    const filter: Record<string, unknown> = { role: 'user' };
    if (status) filter.status = status;

    const skip = (parseInt(page as string) - 1) * parseInt(limit as string);
    const [users, total] = await Promise.all([
        User.find(filter).skip(skip).limit(parseInt(limit as string)).select("-passwordHash").sort({ createdAt: -1 }),
        User.countDocuments(filter)
    ]);

    res.json({
        data: {
            users,
            total,
            page: parseInt(page as string)
        }
    });
}

const updateStatusSchema = z.object({
    status: z.enum(['APPROVED', 'REJECTED']),
    reason: z.string().optional()
});

export const updateUserStatus = async (req: Request, res: Response): Promise<void> => {
    const { status, reason } = updateStatusSchema.parse(req.body);
    const { userId } = req.params;

    const user = await User.findById(userId);
    if (!user) throw new AppError(404, "User not found");
    if (user.role === 'admin') throw new AppError(403, 'Cannot modify admin accounts');

    user.status = status;
    await user.save();
    await writeAuditLog(req.user!.userId, 'admin', status === 'APPROVED' ? 'USER_APPROVED' : 'USER_REJECTED', user._id.toString(), { reason });

    res.json({
        message: `User ${status.toLowerCase()}.`,
        data: {
            id: user._id,
            email: user.email,
            status: user.status
        }
    });
};

const assignStrategySchema = z.object({
    strategies: z.array(z.string()).min(1)
});

export const assignStrategies = async (req: Request, res: Response): Promise<void> => {
    const parsed = assignStrategySchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const user = await User.findById(req.params.userId);
    if (!user) throw new AppError(404, "User not found");

    const validStrategies = await Strategy.find({
        name: { $in: parsed.data.strategies },
        isActive: true
    });

    if (validStrategies.length !== parsed.data.strategies.length) throw new AppError(400, "Some strategies are not valid");

    user.assignedStrategies = parsed.data.strategies;
    await user.save();

    await writeAuditLog(
        req.user!.userId,
        'admin',
        'ASSIGN_STRATEGIES',
        user._id.toString(),
        { strategies: parsed.data.strategies }
    );

    res.json({
        success: true,
        message: "Strategies assigned successfully",
        data: {
            assignedStrategies: user.assignedStrategies
        }
    });
}

export const toggleUserTrading = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.params.userId);
    if (!user) throw new AppError(404, "User not found");

    user.autoTrading = !user.autoTrading;
    await user.save();

    await writeAuditLog(
        req.user!.userId,
        'admin',
        'ADMIN_TOGGLE_TRADING',
        user._id.toString(),
        { autoTrading: user.autoTrading }
    );

    res.json({
        success: true,
        message: `Auto trading ${user.autoTrading ? 'enabled' : 'disabled'} for user.`,
        data: {
            autoTrading: user.autoTrading
        }
    });
}

export const createStrategy = async (req: Request, res: Response): Promise<void> => {
    const schema = z.object({
        name: z.string().min(2),
        displayName: z.string().min(2),
        description: z.string().optional(),
        riskLevel: z.enum(['LOW', 'MEDIUM', 'HIGH']).optional()
    });

    const parsed = schema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const strategy = await Strategy.create(parsed.data);
    res.status(201).json({
        success: true,
        message: "Strategy created successfully",
        data: strategy
    });
}

export const listStrategies = async (req: Request, res: Response): Promise<void> => {
    const strategies = await Strategy.find({ isActive: true }).sort({ name: 1 });
    res.json({
        success: true,
        data: strategies
    });
}

export const triggerStrategy = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.params.userId);
    if (!user) throw new AppError(404, "User not found");

    if (user.status !== 'APPROVED') throw new AppError(400, "User is not approved");
    if (!user.encryptedCredentials) throw new AppError(400, "User has no encrypted credentials");

    const { strategyName } = req.body;
    if (!user.assignedStrategies.includes(strategyName)) throw new AppError(400, "Strategy not assigned to user");

    const jobId = await publishStrategyRun({
        userId: user._id.toString(),
        userEmail: user.email,
        strategyName,
        encryptedCredentials: user.encryptedCredentials,
        autoTrading: user.autoTrading,
        triggeredBy: 'ADMIN'
    });

    await writeAuditLog(
        req.user!.userId,
        'admin',
        'MANUAL_STRATEGY_TRIGGER',
        user._id.toString(),
        { strategyName, jobId }
    );

    res.json({
        success: true,
        message: `Strategy queued`,
        data: { jobId }
    });
}

export const getQueueStatus = async (req: Request, res: Response): Promise<void> => {
    const [strategyWaiting, strategyActive, strategyFailed] = await Promise.all([
        strategyQueue.getWaitingCount(),
        strategyQueue.getActiveCount(),
        strategyQueue.getFailedCount()
    ]);

    const [tradeWaiting, tradeActive, tradeFailed] = await Promise.all([
        tradeQueue.getWaitingCount(),
        tradeQueue.getActiveCount(),
        tradeQueue.getFailedCount()
    ]);

    res.json({
        success: true,
        data: {
            strategyRunner: { waiting: strategyWaiting, active: strategyActive, failed: strategyFailed },
            tradeExecutor: { waiting: tradeWaiting, active: tradeActive, failed: tradeFailed }
        }
    });
}

export const setGlobalKillSwitch = async (req: Request, res: Response): Promise<void> => {
    const schema = z.object({
        isActive: z.boolean(),
        reason: z.string().optional()
    });

    const parsed = schema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);
    await KillSwitch.findOneAndUpdate(
        { scope: 'global' },
        {
            scope: 'global',
            userId: null,
            isActive: parsed.data.isActive,
            reason: parsed.data.reason,
            setBy: new mongoose.Types.ObjectId(req.user!.userId),
        },
        { upsert: true, new: true }
    );

    await writeAuditLog(
        req.user!.userId,
        'admin',
        parsed.data.isActive ? 'KILL_SWITCH_ON' : 'KILL_SWITCH_OFF',
        undefined,
        { reason: parsed.data.reason }
    );

    res.json({
        success: true,
        message: `Global kill switch ${parsed.data.isActive ? 'ACTIVATED' : 'DEACTIVATED'}.`
    });
}

export const setUserRiskLimits = async (req: Request, res: Response): Promise<void> => {
    const schema = z.object({
        maxDailyLoss: z.number().positive().optional(),
        maxDailyTrades: z.number().int().positive().optional(),
    });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const user = await User.findByIdAndUpdate(
        req.params.userId,
        {$set: {riskLimits: parsed.data}},
        {new: true}
    )
    if(!user) throw new AppError(404, "User not found");
    await writeAuditLog(
        req.user!.userId, 'admin', 'SET_RISK_LIMITS',
        req.params.userId, parsed.data
    );
    res.json({success: true, data: {riskLimits: user.riskLimits}});
}

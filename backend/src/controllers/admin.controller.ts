import {Request, Response} from "express";
import {User} from "../models/User";
import z from "zod";
import {AppError} from "../middleware/errorhandler";
import {writeAuditLog} from "../services/audit.service";
import {Strategy} from "../models/Strategy";
import {publishStrategyRun} from "../services/queue.service";
import {strategyQueue, tradeQueue} from "../queues";
import {KillSwitch} from "../models/KillSwitch";
import mongoose from "mongoose";
import {redis} from "../config/redis";
import {assertObjectId, parsePagination, queryString} from "../utils/pagination";

const USER_STATUSES = ['PENDING', 'APPROVED', 'REJECTED'];

export const getAllUsers = async (req: Request, res: Response): Promise<void> => {
    const {page, limit, skip} = parsePagination(req.query as Record<string, unknown>);

    const filter: Record<string, unknown> = {role: 'user'};
    const status = queryString(req.query.status);
    if (status) {
        if (!USER_STATUSES.includes(status))
            throw new AppError(400, "Invalid status filter");
        filter.status = status;
    }

    const [users, total] = await Promise.all([
        User.find(filter).sort({createdAt: -1}).skip(skip).limit(limit).select("-passwordHash"),
        User.countDocuments(filter)
    ]);

    res.json({
        data: {
            users,
            total,
            page,
            totalPages: Math.ceil(total / limit)
        }
    });
}

const updateStatusSchema = z.object({
    status: z.enum(['APPROVED', 'REJECTED']),
    reason: z.string().optional()
});

export const updateUserStatus = async (req: Request, res: Response): Promise<void> => {
    const parsed = updateStatusSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);
    const {status, reason} = parsed.data;
    const userId = assertObjectId(req.params.userId, 'user id');

    const user = await User.findById(userId);
    if (!user) throw new AppError(404, "User not found");
    if (user.role === 'admin') throw new AppError(403, 'Cannot modify admin accounts');

    user.status = status;
    if (status === 'REJECTED') user.autoTrading = false;
    await user.save();
    await writeAuditLog(req.user!.userId, 'admin', status === 'APPROVED' ? 'USER_APPROVED' : 'USER_REJECTED', user._id.toString(), {reason});

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
    strategies: z.array(z.string().trim().min(1)).min(1)
});

export const assignStrategies = async (req: Request, res: Response): Promise<void> => {
    const parsed = assignStrategySchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const userId = assertObjectId(req.params.userId, 'user id');
    const user = await User.findById(userId);
    if (!user) throw new AppError(404, "User not found");

    const requested = [...new Set(parsed.data.strategies.map(s => s.toLowerCase()))];

    const validStrategies = await Strategy.find({
        name: {$in: requested},
        isActive: true
    });
    if (validStrategies.length !== requested.length) throw new AppError(400, "Some strategies are not valid");

    user.assignedStrategies = requested;
    await user.save();

    await writeAuditLog(
        req.user!.userId,
        'admin',
        'ASSIGN_STRATEGIES',
        user._id.toString(),
        {strategies: requested}
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
    const userId = assertObjectId(req.params.userId, 'user id');
    const user = await User.findById(userId);
    if (!user) throw new AppError(404, "User not found");

    const enabling = !user.autoTrading;
    if (enabling && (user.status !== 'APPROVED' || !user.isActive))
        throw new AppError(400, "User must be approved and active to enable trading");

    if (enabling && !user.encryptedCredentials) {
        const withCreds = await User.findById(userId).select('+encryptedCredentials');
        if (!withCreds?.encryptedCredentials)
            throw new AppError(400, "User has no broker credentials");
    }

    user.autoTrading = enabling;
    await user.save();

    await writeAuditLog(
        req.user!.userId,
        'admin',
        'ADMIN_TOGGLE_TRADING',
        user._id.toString(),
        {autoTrading: user.autoTrading}
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
        name: z.string().trim().min(2),
        displayName: z.string().trim().min(2),
        description: z.string().optional(),
        riskLevel: z.enum(['LOW', 'MEDIUM', 'HIGH']).optional()
    });

    const parsed = schema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const strategy = await Strategy.create({
        ...parsed.data,
        name: parsed.data.name.toLowerCase(),
        description: parsed.data.description?.trim() || parsed.data.displayName,
    });
    res.status(201).json({
        success: true,
        message: "Strategy created successfully",
        data: strategy
    });
}

export const listStrategies = async (req: Request, res: Response): Promise<void> => {
    const strategies = await Strategy.find({isActive: true}).sort({name: 1});
    res.json({
        success: true,
        data: strategies
    });
}

export const triggerStrategy = async (req: Request, res: Response): Promise<void> => {
    const parsed = z.object({strategyName: z.string().trim().min(1)}).safeParse(req.body);
    if (!parsed.success) throw new AppError(400, "StrategyName is required");
    const strategyName = parsed.data.strategyName.toLowerCase();

    const userId = assertObjectId(req.params.userId, 'user id');
    const user = await User.findById(userId).select('+encryptedCredentials');
    if (!user) throw new AppError(404, "User not found");

    if (user.status !== 'APPROVED' || !user.isActive) throw new AppError(400, "User is not approved");
    if (!user.encryptedCredentials) throw new AppError(400, "User has no encrypted credentials");
    if (!user.assignedStrategies.includes(strategyName)) throw new AppError(400, "Strategy not assigned to user");

    const [globalKill, userKill, strategy] = await Promise.all([
        KillSwitch.findOne({scope: 'global', isActive: true}),
        KillSwitch.findOne({scope: 'user', userId: user._id, isActive: true}),
        Strategy.findOne({name: strategyName, isActive: true})
    ])

    if (globalKill) throw new AppError(409, "Global kill switch is active");
    if (userKill) throw new AppError(409, "A kill switch is active for this user");
    if (!strategy) throw new AppError(400, "Strategy is not active");

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
        {strategyName, jobId}
    );

    res.json({
        success: true,
        message: `Strategy queued`,
        data: {jobId}
    });
}

export const getQueueStatus = async (req: Request, res: Response): Promise<void> => {
    const safeCount = async (fn: () => Promise<number>): Promise<number | null> => {
        try {
            return await fn();
        } catch (error) {
            return null;
        }
    };

    const [strategyWaiting, strategyActive, strategyFailed, strategyCompleted] = await Promise.all([
        safeCount(() => strategyQueue.getWaitingCount()),
        safeCount(() => strategyQueue.getActiveCount()),
        safeCount(() => strategyQueue.getFailedCount()),
        safeCount(() => strategyQueue.getCompletedCount())
    ]);

    const [tradeWaiting, tradeActive, tradeFailed, tradeCompleted] = await Promise.all([
        safeCount(() => tradeQueue.getWaitingCount()),
        safeCount(() => tradeQueue.getActiveCount()),
        safeCount(() => tradeQueue.getFailedCount()),
        safeCount(() => tradeQueue.getCompletedCount())
    ]);

    res.json({
        success: true,
        data: {
            strategyRunner: {
                waiting: strategyWaiting,
                active: strategyActive,
                failed: strategyFailed,
                completed: strategyCompleted
            },
            tradeExecutor: {waiting: tradeWaiting, active: tradeActive, failed: tradeFailed, completed: tradeCompleted}
        }
    });
}

export const getHealth = async (req: Request, res: Response): Promise<void> => {
    const checks: Record<string, { status: 'up' | 'down'; error?: string }> = {};
    try {
        await mongoose.connection.db!.admin().ping();
        checks.mongodb = {status: 'up'};
    } catch (e) {
        checks.mongodb = {status: 'down', error: (e as Error).message};
    }
    try {
        await redis.ping();
        checks.redis = {status: 'up'};
    } catch (e) {
        checks.redis = {status: 'down', error: (e as Error).message};
    }
    const globalKill = await KillSwitch.findOne({scope: 'global', isActive: true}).catch(() => null);
    const healthy = Object.values(checks).every(check => check.status === 'up');
    res.json({
        success: true,
        data: {
            status: healthy ? 'healthy': 'degraded',
            checks,
            killSwitchActive: Boolean(globalKill)
        }
    });
};

export const getKillSwitchStatus = async (req: Request, res: Response): Promise<void> => {
    const globalKill = await KillSwitch.findOne({scope: 'global'});
    res.json({
        success: true,
        data: {
            isActive: Boolean(globalKill?.isActive),
            reason: globalKill?.reason ?? '',
            updatedAt: globalKill?.updatedAt
        }
    });
};

const killSwitchSchema = z.object({
    isActive: z.boolean(),
    reason: z.string().max(500).optional()
})

export const setGlobalKillSwitch = async (req: Request, res: Response): Promise<void> => {
    const parsed = killSwitchSchema.safeParse(req.body);
    if(!parsed.success)
        throw new AppError(400, parsed.error.errors[0].message);

    await KillSwitch.findOneAndUpdate(
        {scope: 'global', userId: null},
        {
            scope: 'global',
            userId: null,
            isActive: parsed.data.isActive,
            reason: parsed.data.reason ?? '',
            setBy: new mongoose.Types.ObjectId(req.user!.userId),
        },
        {upsert: true, new: true}
    );

    await writeAuditLog(
        req.user!.userId,
        'admin',
        parsed.data.isActive ? 'KILL_SWITCH_ON' : 'KILL_SWITCH_OFF',
        undefined,
        {reason: parsed.data.reason}
    );

    res.json({
        success: true,
        message: `Global kill switch ${parsed.data.isActive ? 'ACTIVATED' : 'DEACTIVATED'}.`
    });
}

export const setUserKillSwitch = async (req: Request, res: Response): Promise<void> => {
    const parsed = killSwitchSchema.safeParse(req.body);
    if(!parsed.success)
        throw new AppError(400, parsed.error.errors[0].message);
    const userId = assertObjectId(req.params.userId, 'user id');

    const user = await User.findById(userId).select('_id');
    if(!user)
        throw new AppError(404, "User not found");

    await KillSwitch.findOneAndUpdate(
        {scope: 'user', userId: user._id},
        {
            scope: 'user',
            userId: user._id,
            isActive: parsed.data.isActive,
            reason: parsed.data.reason ?? '',
            setBy: new mongoose.Types.ObjectId(req.user!.userId),
        },
        {upsert: true, new: true}
    );

    await writeAuditLog(
        req.user!.userId,
        'admin',
        parsed.data.isActive ? 'KILL_SWITCH_ON' : 'KILL_SWITCH_OFF',
        userId,
        {reason: parsed.data.reason}
    );

    res.json({
        success: true,
        message: `User kill switch ${parsed.data.isActive ? 'ACTIVATED' : 'DEACTIVATED'}.`
    });
}

export const setUserRiskLimits = async (req: Request, res: Response): Promise<void> => {
    const schema = z.object({
        maxDailyLoss: z.number().positive().optional(),
        maxDailyTrades: z.number().int().positive().optional(),
    }).refine(v => v.maxDailyLoss != undefined || v.maxDailyTrades != undefined, 'Provide maxDailyLoss and/or maxDailyTrades');

    const parsed = schema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const userId = assertObjectId(req.params.userId, 'user id');

    const $set: Record<string, number> = {};

    if (parsed.data.maxDailyLoss !== undefined) $set['riskLimits.maxDailyLoss'] = parsed.data.maxDailyLoss;
    if (parsed.data.maxDailyTrades !== undefined) $set['riskLimits.maxDailyTrades'] = parsed.data.maxDailyTrades;

    const user = await User.findByIdAndUpdate(
        userId,
        {$set},
        {new: true, runValidators: true}
    );
    if (!user) throw new AppError(404, "User not found");
    await writeAuditLog(
        req.user!.userId, 'admin', 'SET_RISK_LIMITS',
        userId, parsed.data
    );
    res.json({success: true, data: {riskLimits: user.riskLimits}});
}
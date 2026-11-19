import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import {
    assignStrategies,
    createStrategy,
    getAvailableStrategies,
    getAllUsers,
    getHealth, getKillSwitchStatus,
    getQueueStatus,
    listStrategies,
    setGlobalKillSwitch, setUserKillSwitch,
    setUserRiskLimits,
    toggleUserTrading,
    triggerStrategy,
    updateUserStatus
} from "../controllers/admin.controller";
import { getAllTrades, getTradeByIdAdmin } from "../controllers/trade.controller";
import { killSwitchLimiter, triggerLimiter } from "../middleware/rateLimiter";
import type { FastifyInstance } from "fastify";
import { handler } from "../http";

export default async function adminRoutes(app: FastifyInstance): Promise<void> {
    app.addHook("preHandler", authenticate);
    app.addHook("preHandler", requireRole('admin'));

    app.get('/users', handler(getAllUsers));
    app.patch('/users/:userId/status', handler(updateUserStatus));
    app.post('/users/:userId/strategies', handler(assignStrategies));
    app.patch('/users/:userId/toggle-trading', handler(toggleUserTrading));
    app.post('/users/:userId/risk-limits', handler(setUserRiskLimits));
    app.post('/users/:userId/kill-switch', { preHandler: killSwitchLimiter }, handler(setUserKillSwitch));

    app.get('/trades', handler(getAllTrades));
    app.get('/trades/:tradeId', handler(getTradeByIdAdmin));

    app.post('/strategies', handler(createStrategy));
    app.get('/strategies/available', handler(getAvailableStrategies));
    app.get('/strategies', handler(listStrategies));

    app.post('/users/:userId/trigger-strategy', {preHandler: triggerLimiter}, handler(triggerStrategy));
    app.get('/queue-status', handler(getQueueStatus));
    app.get('/health', handler(getHealth));
    app.get("/kill-switch", handler(getKillSwitchStatus))
    app.post('/kill-switch', {preHandler: killSwitchLimiter}, handler(setGlobalKillSwitch));
}
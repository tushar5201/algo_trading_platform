import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import {
    assignStrategies,
    createStrategy,
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
import { getAllTrades } from "../controllers/trade.controller";
import {killSwitchLimiter, triggerLimiter} from "../middleware/rateLimiter";

const router = Router();
router.use(authenticate, requireRole('admin'));

router.get('/users', asynchandler(getAllUsers));
router.patch('/users/:userId/status', asynchandler(updateUserStatus));
router.post('/users/:userId/strategies', asynchandler(assignStrategies));
router.patch('/users/:userId/toggle-trading', asynchandler(toggleUserTrading));
router.post('/users/:userId/risk-limits', asynchandler(setUserRiskLimits));
router.post('/users/:userId/kill-switch', killSwitchLimiter, asynchandler(setUserKillSwitch));

router.get('/trades', asynchandler(getAllTrades));

router.post('/strategies', asynchandler(createStrategy));
router.get('/strategies', asynchandler(listStrategies));

router.post('/users/:userId/trigger-strategy', triggerLimiter, asynchandler(triggerStrategy));
router.get('/queue-status', asynchandler(getQueueStatus));
router.get('/health', asynchandler(getHealth));
router.get("/kill-switch", asynchandler(getKillSwitchStatus))
router.post('/kill-switch', killSwitchLimiter, asynchandler(setGlobalKillSwitch));

export default router;
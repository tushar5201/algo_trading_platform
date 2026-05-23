import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import { assignStrategies, createStrategy, getAllUsers, getQueueStatus, listStrategies, setGlobalKillSwitch, toggleUserTrading, triggerStrategy, updateUserStatus } from "../controllers/admin.controller";
import { getAllTrades } from "../controllers/trade.controller";

const router = Router();
router.use(authenticate, requireRole('admin'));

router.get('/users', asynchandler(getAllUsers));
router.patch('/users/:userId/status', asynchandler(updateUserStatus));
router.post('/users/:userId/strategies', asynchandler(assignStrategies));
router.patch('/users/:userId/toggle-trading', asynchandler(toggleUserTrading));

router.get('/trades', asynchandler(getAllTrades));

router.post('/strategies', asynchandler(createStrategy));
router.get('/strategies', asynchandler(listStrategies));

router.post('/users/:userId/trigger-strategy', asynchandler(triggerStrategy));
router.get('/queue-status', asynchandler(getQueueStatus));
router.post('/kill-switch', asynchandler(setGlobalKillSwitch));

export default router;

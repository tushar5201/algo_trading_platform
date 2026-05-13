import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import { getMyStrategies, saveCredentials, toggleAutoTrading } from "../controllers/user.controller";

const router = Router();
router.use(authenticate, requireRole('user'));

router.post('/credentials', asynchandler(saveCredentials));
router.patch('/toggle-trading', asynchandler(toggleAutoTrading));
router.get('/strategies', asynchandler(getMyStrategies));

export default router;

import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import {
    getAuthStatus,
    getMyStrategies,
    saveCredentials,
    submitMPIN,
    submitTOTP,
    toggleAutoTrading,
    getCredentials
} from "../controllers/user.controller";
import {kotakAuthLimiter} from "../middleware/rateLimiter";

const router = Router();
router.use(authenticate, requireRole('user'));

router.post('/credentials', asynchandler(saveCredentials));
router.patch('/toggle-trading', asynchandler(toggleAutoTrading));
router.get('/strategies', asynchandler(getMyStrategies));
router.get('/credentials', asynchandler(getCredentials));

router.post('/kotak/totp', kotakAuthLimiter, asynchandler(submitTOTP));
router.post('/kotak/mpin', kotakAuthLimiter, asynchandler(submitMPIN));
router.get('/kotak/auth-status', asynchandler(getAuthStatus));

export default router;

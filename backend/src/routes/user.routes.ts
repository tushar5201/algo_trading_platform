import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import {
    getAuthStatus,
    getKotakSessionStatus,
    getMyStrategies,
    saveCredentials,
    submitMPIN,
    submitTOTP,
    toggleAutoTrading,
    triggerKotakOTP,
    validateKotakOTP
} from "../controllers/user.controller";

const router = Router();
router.use(authenticate, requireRole('user'));

router.post('/credentials', asynchandler(saveCredentials));
router.patch('/toggle-trading', asynchandler(toggleAutoTrading));
router.get('/strategies', asynchandler(getMyStrategies));

router.post('/kotak/totp', asynchandler(submitTOTP));
router.post('/kotak/mpin', asynchandler(submitMPIN));
router.get('/kotak/auth-status', asynchandler(getAuthStatus));

export default router;

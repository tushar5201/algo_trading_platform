import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import { getMyPnl, getMyTrades } from "../controllers/trade.controller";

const router = Router();
router.use(authenticate, requireRole('user'));

router.get("/", asynchandler(getMyTrades));
router.get("/pnl", asynchandler(getMyPnl));

export default router;

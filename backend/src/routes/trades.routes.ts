import { Router } from "express";
import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { asynchandler } from "../middleware/errorhandler";
import {getMyPnl, getMyTrades, getTradeById, requestAiReview} from "../controllers/trade.controller";
import {aiReviewLimiter, tradeLimiter} from "../middleware/rateLimiter";

const router = Router();
router.use(authenticate, requireRole('user'), tradeLimiter);

router.get("/", asynchandler(getMyTrades));
router.get("/pnl", asynchandler(getMyPnl));
router.get("/:tradeId", asynchandler(getTradeById));
router.post("/:tradeId/ai-review", aiReviewLimiter, asynchandler(requestAiReview));

export default router;

import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import { getMyPnl, getMyTrades, getTradeById, requestAiReview } from "../controllers/trade.controller";
import { aiReviewLimiter, tradeLimiter } from "../middleware/rateLimiter";
import { FastifyInstance } from "fastify";
import { handler } from "../http";

export default async function tradesRoutes(app: FastifyInstance): Promise<void> {
    app.addHook('preHandler', authenticate);
    app.addHook('preHandler', requireRole('user'));
    app.addHook('preHandler', tradeLimiter);

    app.get("/", handler(getMyTrades));
    app.get("/pnl", handler(getMyPnl));
    app.get("/:tradeId", handler(getTradeById));
    app.post("/:tradeId/ai-review", { preHandler: aiReviewLimiter }, handler(requestAiReview));
}

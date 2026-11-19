import { authenticate } from "../middleware/auth";
import { requireRole } from "../middleware/roleGuard";
import {
    getAuthStatus,
    getMyStrategies,
    saveCredentials,
    submitMPIN,
    submitTOTP,
    toggleAutoTrading,
    getCredentials
} from "../controllers/user.controller";
import { kotakAuthLimiter } from "../middleware/rateLimiter";
import { handler } from "../http";
import { FastifyInstance } from "fastify";

export default async function userRoutes(app: FastifyInstance): Promise<void> {
    app.addHook('preHandler', authenticate);
    app.addHook('preHandler', requireRole('user'));

    app.post('/credentials', handler(saveCredentials));
    app.patch('/toggle-trading', handler(toggleAutoTrading));
    app.get('/strategies', handler(getMyStrategies));
    app.get('/credentials', handler(getCredentials));

    app.post('/kotak/totp', { preHandler: kotakAuthLimiter }, handler(submitTOTP));
    app.post('/kotak/mpin', { preHandler: kotakAuthLimiter }, handler(submitMPIN));
    app.get('/kotak/auth-status', handler(getAuthStatus));
}

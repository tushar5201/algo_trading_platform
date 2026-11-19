import type { FastifyInstance } from "fastify";
import { getMe, login, signup } from "../controllers/auth.controller";
import { authenticate } from "../middleware/auth";
import { authLimiter } from "../middleware/rateLimiter";
import { handler } from "../http";

export default async function authRoutes(app: FastifyInstance): Promise<void> {
    app.post('/signup', { preHandler: authLimiter }, handler(signup));
    app.post('/login', { preHandler: authLimiter }, handler(login));
    app.get('/me', { preHandler: authenticate }, handler(getMe));
}
import dotenv from "dotenv";
import helmet from "@fastify/helmet";
import cors from "@fastify/cors";
import { apiLimiter } from "./middleware/rateLimiter";
import authRoutes from "./routes/auth.routes";
import { errorHandler, notFoundHandler } from "./middleware/errorhandler";
import adminRoutes from "./routes/admin.routes";
import userRoutes from "./routes/user.routes";
import tradesRoutes from "./routes/trades.routes";
import { env } from "./config/env";
import "./http";
import Fastify, { FastifyInstance } from "fastify";

dotenv.config();

const BODY_LIMIT_BYTES = 10 * 1024;

export async function buildApp(): Promise<FastifyInstance> {
    const app = Fastify({
        logger: env.NODE_ENV === "test" ? false : { level: "info" },
        disableRequestLogging: true,
        trustProxy: "loopback,linklocal,uniquelocal",
        bodyLimit: BODY_LIMIT_BYTES
    });

    app.register(helmet);
    app.register(cors, { origin: env.FRONTEND_URL ?? (env.NODE_ENV === 'production' ? false : true) });

    app.decorateReply("json", function (this: any, body: unknown) {
        return this.send(body);
    });
    app.decorateRequest("user", null);

    app.addContentTypeParser("application/json", { parseAs: "string", bodyLimit: BODY_LIMIT_BYTES }, (_req, body, done) => {
        const text = typeof body === "string" ? body : body.toString("utf8");
        if (text.length === 0) return done(null, {});
        try {
            done(null, JSON.parse(text));
        } catch {
            const err: any = new Error("Invalid JSON body");
            err.statusCode = 400;
            done(err, undefined);
        }
    });

    app.addHook("onRequest", async (request, reply) => {
        if (request.url.startsWith("/api")) return apiLimiter(request, reply);
    });

    app.addHook("onResponse", async (request, reply) => {
        if (request.url === "/api/health") return;
        request.log.info({
            method: request.method,
            url: request.url,
            status: reply.statusCode,
            ms: Math.round(reply.elapsedTime)
        }, "rquest");
    });

    app.setErrorHandler(errorHandler);
    app.setNotFoundHandler(notFoundHandler);

    app.get("/api/health", async () => ({ success: true, status: "ok", time: new Date().toISOString() }));

    app.register(authRoutes, { prefix: "/api/auth" });
    app.register(adminRoutes, { prefix: "/api/admin" });
    app.register(userRoutes, { prefix: "/api/user" });
    app.register(tradesRoutes, { prefix: "/api/trades" });

    return app;
}
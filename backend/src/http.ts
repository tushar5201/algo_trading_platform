import { FastifyReply, FastifyRequest, RouteHandlerMethod } from "fastify";
import { JwtPayload } from "jsonwebtoken";

export type Request = FastifyRequest<{
    Querystring: Record<string, any>;
    Params: Record<string, string>;
    Body: any;
}>;

export type Response = FastifyReply;

declare module "fastify" {
    interface FastifyRequest {
        user?: JwtPayload | null;
    }
    interface FastifyReply {
        json(body: unknown): FastifyReply;
    }
}

export const handler = (fn: (req: Request, reply: Response) => Promise<void>): RouteHandlerMethod =>
    async (req, reply) => {
        await fn(req as unknown as Request, reply);
        return reply;
    };


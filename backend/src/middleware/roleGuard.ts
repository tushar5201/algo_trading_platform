import { FastifyRequest } from "fastify";
import { AppError } from "./errorhandler";


export const requireRole = (...roles: string[]) => async (request: FastifyRequest): Promise<void> => {
    if(!request.user)
        throw new AppError(401, "Unauthorized");
    
    if(!roles.includes(request.user.role))
        throw new AppError(403, "Insufficient permissions");
}

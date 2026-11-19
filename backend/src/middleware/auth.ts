import { JwtPayload, verifyToken } from "../services/auth.service";
import { FastifyRequest } from "fastify";
import { AppError } from "./errorhandler";
import { getAccount } from "../services/auth.cache.service";
import "../http"

export const authenticate = async (req: FastifyRequest): Promise<void> => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
        throw new AppError(401, "No token provided");
    }
    const token = authHeader.split(" ")[1];

    let payload: JwtPayload;
    try {
        payload = verifyToken(token);
    } catch (error) {
        throw new AppError(401, "Invalid token");
    }

    const account = await getAccount(payload.userId);
    if (!account || !account.isActive || account.status !== "APPROVED") {
        throw new AppError(401, "Account is not active");
    }
    req.user = {
        userId: payload.userId,
        role: account.role,
        email: account.email
    };
};

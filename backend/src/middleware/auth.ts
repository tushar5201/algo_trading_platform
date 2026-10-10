import { NextFunction, Request, Response } from "express";
import { JwtPayload, verifyToken } from "../services/auth.service";
import {User} from "../models/User";

declare global {
    namespace Express {
        interface Request {
            user?: JwtPayload;
        }
    }
}

export const authenticate = async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
        res.status(401).json({ message: "No token provided" });
        return;
    }
    const token = authHeader.split(" ")[1];

    let payload: JwtPayload;
    try {
        payload = verifyToken(token);
    } catch (error) {
        res.status(401).json({ message: "Invalid token" });
        return;
    }

    try {
        const account = await User.findById(payload.userId).select("role status isActive email");
        if(!account || !account.isActive || account.status !== "APPROVED") {
            res.status(401).json({ message: "Account is not active" });
            return;
        }
        req.user = {
            userId: payload.userId,
            role: account.role,
            email: account.email
        };
        next();
    } catch (error) {
        next(error);
    }
};

import { NextFunction, Request, Response } from "express";
import { JwtPayload, verifyToken } from "../services/auth.service";

declare global {
    namespace Express {
        interface Request {
            user?: JwtPayload;
        }
    }
}

export const authenticate = (req: Request, res: Response, next: NextFunction): void => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
        res.status(401).json({ message: "No token provided" });
        return;
    }
    const token = authHeader.split(" ")[1];
    try {
        req.user = verifyToken(token);
        next();
    } catch (error) {
        res.status(401).json({ message: "Invalid token" });
    }
};

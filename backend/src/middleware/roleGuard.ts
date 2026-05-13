import { NextFunction, Request, Response } from "express";

export const requireRole = (...roles: string[]) => (req: Request, res: Response, next: NextFunction):void => {
    if(!req.user) {
        res.status(401).json({ message: "Unauthorized" });
        return;
    }
    if(!roles.includes(req.user.role)) {
        res.status(403).json({ message: "Insufficient permissions" });
        return;
    }
    next();
}

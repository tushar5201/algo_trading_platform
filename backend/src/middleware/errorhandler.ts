import { NextFunction, Request, Response } from "express";

export class AppError extends Error {
    constructor(public statusCode: number, public message: string) {
        super(message);
        this.name = 'AppError';
    }
}

export const asynchandler = (fn: (req: Request, res: Response, next: NextFunction) => Promise<void>) =>
    (req: Request, res: Response, next: NextFunction): void => {
        fn(req, res, next).catch(next);
    };

export const errorHandler = (err: Error, req: Request, res: Response, next: NextFunction): void => {
    console.error(err.stack);

    if(res.headersSent) {
        next(err);
        return;
    }

    if (err.name === 'AppError') {
        res.status((err as AppError).statusCode).json({ message: err.message });
        return;
    }

    if (err.name === 'ZodError') {
        const issues = (err as any).issues as Array<{ message: string }> | undefined;
        res.status(400).json({ message: issues?.[0]?.message ?? "Invalid request" });
        return;
    }

    if (err.name === 'ValidationError') {
        res.status(400).json({ message: err.message });
        return;
    }

    if (err.name === 'CastError' || err.name === 'BSONError' || err.name === 'BSONTypeError') {
        res.status(400).json({ message: 'Invalid identifier or value' });
        return;
    }

    if ((err.name === 'MongoServerError' || err.name === 'MongoError') && (err as any).code === 11000) {
        const keys = Object.keys((err as any).keyPattern ?? {});
        const message = keys.includes('email')
            ? "Email already exists"
            : keys.length
                ? `Duplicate value for: ${keys.join(', ')}`
                : "Duplicate value";
        res.status(409).json({ message });
        return;
    }

    const knownStatus = (err as any).statusCode ?? (err as any).status;
    if (typeof knownStatus === 'number' && knownStatus >= 400 && knownStatus < 500) {
        res.status(knownStatus).json({ message: err.message || "Bad Request" });
        return;
    }

    res.status(500).json({ message: "Internal Server Error" });
};

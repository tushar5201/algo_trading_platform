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

    if (err.name === 'AppError') {
        res.status((err as AppError).statusCode).json({ message: err.message });
        return;
    }

    if (err.name === 'ValidationError') {
        res.status(400).json({ message: err.message });
        return;
    }

    if (err.name === 'MongoError' && (err as any).code === 11000) {
        res.status(409).json({ message: "Email already exists" });
        return;
    }

    res.status(500).json({ message: "Internal Server Error" });
};

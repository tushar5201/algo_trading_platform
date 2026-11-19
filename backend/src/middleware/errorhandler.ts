import { FastifyReply, FastifyRequest } from "fastify";

export class AppError extends Error {
    constructor(public statusCode: number, public message: string) {
        super(message);
        this.name = 'AppError';
    }
}

export function toHttpError(err: Error): { status: number; message: string } {
    if (err.name === 'AppError') {
        return { status: (err as AppError).statusCode, message: err.message };

    }

    if (err.name === 'ZodError') {
        const issues = (err as any).issues as Array<{ message: string }> | undefined;
        return { status: 400, message: issues?.[0]?.message ?? "Invalid request" };
    }

    if (err.name === 'ValidationError') {
        return { status: 400, message: err.message };
    }

    if (err.name === 'CastError' || err.name === 'BSONError' || err.name === 'BSONTypeError') {
        return { status: 400, message: 'Invalid identifier or value' };
    }

    if ((err.name === 'MongoServerError' || err.name === 'MongoError') && (err as any).code === 11000) {
        const keys = Object.keys((err as any).keyPattern ?? {});
        const message = keys.includes('email')
            ? "Email already exists"
            : keys.length
                ? `Duplicate value for: ${keys.join(', ')}`
                : "Duplicate value";
        return { status: 409, message };
    }

    const knownStatus = (err as any).statusCode ?? (err as any).status;
    if (typeof knownStatus === 'number' && knownStatus >= 400 && knownStatus < 500) {
        return { status: knownStatus, message: err.message || "Bad Request" };
    }

    return { status: 500, message: "Internal Server Error" };
};

export const errorHandler = (err: Error, request: FastifyRequest, reply: FastifyReply): void => {
    const { status, message } = toHttpError(err);

    if (status >= 500) request.log.error({ err }, "request failed");
    else request.log.warn({ status, message }, "request rejected");

    if (reply.sent) return;
    void reply.code(status).send({ message });
}

export const notFoundHandler = (_request: FastifyRequest, reply: FastifyReply): void => {
    void reply.code(404).send({ message: "Route not found" });
};

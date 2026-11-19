import { describe, expect, it, jest } from "@jest/globals";
import { AppError, errorHandler, notFoundHandler, toHttpError } from "../errorhandler";

const named = (name: string, extra: Record<string, unknown> = {}) => Object.assign(new Error("boom"), { name }, extra);

function makeReply() {
    const reply: any = { statusCode: 200, body: undefined as any, sent: false };
    reply.code = (c: number) => { reply.statusCode = c; return reply; };
    reply.send = (b: unknown) => { reply.body = b; reply.sent = true; return reply; };
    return reply;
}
const makeReq = () => ({ log: { error: jest.fn(), warn: jest.fn() } }) as any;

describe("toHttpError", () => {
    it("AppError -> its own status", () => {
        expect(toHttpError(new AppError(418, "teapot"))).toEqual({ status: 418, message: "teapot" });
    });

    it("ZodError -> 400 (used to be 500)", () => {
        expect(toHttpError(named("ZodError", { issues: [{ message: "bad field" }] }))).toEqual({ status: 400, message: "bad field" });
        expect(toHttpError(named("ZodError")).status).toBe(400);
    });

    it("mongoose ValidationError -> 400", () => {
        expect(toHttpError(named("ValidationError")).status).toBe(400);
    });

    it("CastError (bad ObjectId) -> 400 (used to be 500)", () => {
        expect(toHttpError(named("CastError"))).toEqual({ status: 400, message: "Invalid identifier or value" });
        expect(toHttpError(named("BSONError")).status).toBe(400);
    });

    it("duplicate key on email -> 409 \"Email already exists\"", () => {
        expect(toHttpError(named("MongoServerError", { code: 11000, keyPattern: { email: 1 } })))
            .toEqual({ status: 409, message: "Email already exists" });
    });

    it("duplicate key on another field does NOT claim the email exists", () => {
        const out = toHttpError(named("MongoServerError", { code: 11000, keyPattern: { name: 1 } }));
        expect(out.status).toBe(409);
        expect(out.message).toBe("Duplicate value for: name");
        expect(toHttpError(named("MongoServerError", { code: 11000 })).message).toBe("Duplicate value");
    });

    it("a non-duplicate MongoServerError is a 500, not a 409", () => {
        expect(toHttpError(named("MongoServerError", { code: 121 })).status).toBe(500);
    });

    it("client errors that already carry a status keep it (fastify: body too large, bad json, wrong media type)", () => {
        expect(toHttpError(named("FastifyError", { statusCode: 413, message: "Request body is too large" })))
            .toEqual({ status: 413, message: "Request body is too large" });
        expect(toHttpError(named("FastifyError", { statusCode: 415 })).status).toBe(415);
    });

    it("a 5xx status on an unknown error is not trusted: 500 without details", () => {
        const out = toHttpError(named("Error", { statusCode: 503, message: "secret connection string" }));
        expect(out).toEqual({ status: 500, message: "Internal Server Error" });
    });

    it("unknown errors -> 500 without leaking details", () => {
        expect(toHttpError(new Error("db password is hunter2"))).toEqual({ status: 500, message: "Internal Server Error" });
    });
});

describe("errorHandler / notFoundHandler", () => {
    it("replies with the mapped status and a message-only body", () => {
        const reply = makeReply(); const req = makeReq();
        errorHandler(new AppError(404, "Trade not found"), req, reply);
        expect(reply.statusCode).toBe(404);
        expect(reply.body).toEqual({ message: "Trade not found" });
        expect(req.log.warn).toHaveBeenCalled();
        expect(req.log.error).not.toHaveBeenCalled();
    });

    it("logs 500s as errors and hides the cause from the client", () => {
        const reply = makeReply(); const req = makeReq();
        errorHandler(new Error("kaboom"), req, reply);
        expect(reply.statusCode).toBe(500);
        expect(reply.body).toEqual({ message: "Internal Server Error" });
        expect(req.log.error).toHaveBeenCalled();
    });

    it("does not try to reply twice", () => {
        const reply = makeReply(); reply.sent = true;
        errorHandler(new AppError(400, "x"), makeReq(), reply);
        expect(reply.body).toBeUndefined();
    });

    it("notFoundHandler answers a JSON 404", () => {
        const reply = makeReply();
        notFoundHandler({} as any, reply);
        expect(reply.statusCode).toBe(404);
        expect(reply.body).toEqual({ message: "Route not found" });
    });
});

import rateLimit from "express-rate-limit";

export const authLimiter = rateLimit({
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 10,
    message: "Too many login attempts from this IP, please try again after 15 minutes",
    standardHeaders: true,
    legacyHeaders: false,
});

export const apiLimiter = rateLimit({
    windowMs: 60 * 60 * 1000, // 1 hour
    max: 100,
    message: "Too many requests, please try again after an hour",
    standardHeaders: true,
    legacyHeaders: false,
});

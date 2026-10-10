import mongoose from "mongoose";
import {AppError} from "../middleware/errorhandler";

export interface Pagination {
    page: number;
    limit: number;
    skip: number;
}

export function parsePagination(query: Record<string, unknown>, defaultLimit = 20, maxLimit = 100): Pagination {
    const rawPage = parseInt(String(query.page ?? ''), 10);
    const rawLimit = parseInt(String(query.limit ?? ''), 10);
    const page = Number.isFinite(rawPage) && rawPage > 0 ? rawPage : 1;
    const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? Math.min(rawLimit, maxLimit) : defaultLimit;
    return {page, limit, skip: (page - 1) * limit};
}

export function queryString(value: unknown): string | undefined {
    return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function assertObjectId(value: unknown, label='id'): string {
    if(typeof value !== "string" || !mongoose.isValidObjectId(value)) {
        throw new AppError(400, `Invalid id for ${label} with id "${value}"`);
    }
    return value;
}

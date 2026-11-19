import { describe, expect, it, jest } from "@jest/globals";
import { assertObjectId, parsePagination, queryString, resolveTotal } from "../pagination";

describe('parsePagination', () => {
    it('uses defaults for missing / NaN values', () => {
        expect(parsePagination({})).toEqual({ page: 1, limit: 20, skip: 0 });
        expect(parsePagination({ page: 'abc', limit: 'xyz' })).toEqual({ page: 1, limit: 20, skip: 0 });
    });
    it('clamps limit so one request cannot dump the collection', () => {
        expect(parsePagination({ limit: '1000000' }).limit).toBe(100);
    });
    it('computes skip', () => {
        expect(parsePagination({ page: '3', limit: '10' })).toEqual({ page: 3, limit: 10, skip: 20 });
    });
    it('rejects negative / zero pages', () => {
        expect(parsePagination({ page: '-4' }).page).toBe(1);
        expect(parsePagination({ page: '0' }).page).toBe(1);
    });
});

describe('queryString', () => {
    it('accepts plain strings only (blocks operator injection)', () => {
        expect(queryString('EXECUTED')).toBe('EXECUTED');
        expect(queryString({ $ne: 'x' })).toBeUndefined();
        expect(queryString(['a', 'b'])).toBeUndefined();
        expect(queryString('')).toBeUndefined();
    });
});

describe('assertObjectId', () => {
    it('passes a valid id and throws 400 on garbage', () => {
        expect(assertObjectId('507f1f77bcf86cd799439011')).toBe('507f1f77bcf86cd799439011');
        expect(() => assertObjectId('nope')).toThrow('Invalid id');
        expect(() => assertObjectId(undefined)).toThrow();
    });
});

describe('resolveTotal (saves a count query on the common case)', () => {
    const page = (p: number, limit = 20) => ({ page: p, limit, skip: (p - 1) * limit });

    it('page 1 that is not full: total is the number of items, no count query', async () => {
        const count = jest.fn(async () => 999);
        expect(await resolveTotal(page(1), 3, count)).toBe(3);
        expect(await resolveTotal(page(1), 0, count)).toBe(0);
        expect(count).not.toHaveBeenCalled();
    });

    it('page 1 that is full: runs the count (there may be more pages)', async () => {
        const count = jest.fn(async () => 57);
        expect(await resolveTotal(page(1), 20, count)).toBe(57);
        expect(count).toHaveBeenCalledTimes(1);
    });

    it('any later page: always runs the count, even for a short or empty page', async () => {
        const count = jest.fn(async () => 41);
        expect(await resolveTotal(page(3), 1, count)).toBe(41);
        expect(await resolveTotal(page(9), 0, count)).toBe(41);
        expect(count).toHaveBeenCalledTimes(2);
    });

    it('propagates a count failure', async () => {
        await expect(resolveTotal(page(2), 5, async () => { throw new Error('db'); })).rejects.toThrow('db');
    });
});

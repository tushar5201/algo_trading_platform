import { describe, expect, it } from "@jest/globals";
import { assertObjectId, parsePagination, queryString } from "../pagination";

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

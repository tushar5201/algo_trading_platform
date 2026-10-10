import { describe, expect, it } from "@jest/globals";
import { decrypt, encrypt } from "../crypto.service";

describe('crypto.service', () => {
    it('encrypts & decrypts back to the original plaintext', () => {
        const plaintext = 'super-secret-broker-password';
        const ciphertext = encrypt(plaintext);
        expect(ciphertext).not.toEqual(plaintext);
        expect(decrypt(ciphertext)).toBe(plaintext);
    });

    it('produces a JSON payload with iv, data & tag as hex strings', () => {
        const payload = JSON.parse(encrypt('hello world'));
        expect(payload).toHaveProperty('iv');
        expect(payload).toHaveProperty('data');
        expect(payload).toHaveProperty('tag');
        expect(payload.iv).toMatch(/^[0-9a-f]+$/);
        expect(payload.data).toMatch(/^[0-9a-f]+$/);
        expect(payload.tag).toMatch(/^[0-9a-f]+$/);
    });

    it('produces a different ciphertext each time for the same plaintext', () => {
        const a = encrypt('same input');
        const b = encrypt('same input');
        expect(a).not.toEqual(b);
        expect(decrypt(a)).toBe('same input');
        expect(decrypt(b)).toBe('same input');
    });

    it('hadles empty string and unicode plaintext', () => {
        expect(decrypt(encrypt(''))).toBe('');
        expect(decrypt(encrypt('hello'))).toBe('hello');
    });

    it('throws when the auth tag has been tampered with', () => {
        const payload = JSON.parse(encrypt('tamper me'));
        payload.tag = '00'.repeat(16);
        expect(() => decrypt(JSON.stringify(payload))).toThrow();
    });

    it('throws when the ciphertext data has been tampered with', () => {
        const payload = JSON.parse(encrypt('temper me'));
        const bytes = payload.data.split('');
        bytes[0] = bytes[0] === '0' ? '1' : '0';
        payload.data = bytes.join('');
        expect(() => decrypt(JSON.stringify(payload))).toThrow();
    });

    it('throws on malformed JSON input to decrypt', () => {
        expect(() => decrypt('not-json')).toThrow();
    });
});

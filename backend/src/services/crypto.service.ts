import { env } from "../config/env";
import crypto from "crypto";

const ALGORITHM = 'aes-256-gcm';
const IV_LENGTH = 16;
const KEY = Buffer.from(env.AES_SECRET_KEY, 'utf-8');

export interface EncryptedPayload {
    iv: string;
    data: string;
    tag: string;
}

export function encrypt(plaintext: string): string {
    const iv = crypto.randomBytes(IV_LENGTH);
    const cipher = crypto.createCipheriv(ALGORITHM, KEY, iv);
    const encrypted = Buffer.concat([cipher.update(plaintext, 'utf-8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    const payload: EncryptedPayload = {
        iv: iv.toString('hex'),
        data: encrypted.toString('hex'),
        tag: tag.toString('hex')
    };
    return JSON.stringify(payload);
}

export function decrypt(ciphertext: string): string {
    const payload: EncryptedPayload = JSON.parse(ciphertext);
    const decipher = crypto.createDecipheriv(ALGORITHM, KEY, Buffer.from(payload.iv, 'hex'));
    decipher.setAuthTag(Buffer.from(payload.tag, 'hex'));
    const decrypted = Buffer.concat([decipher.update(Buffer.from(payload.data, 'hex')), decipher.final()]);
    return decrypted.toString('utf-8');
}

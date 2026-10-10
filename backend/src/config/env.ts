import { z } from "zod";

const str = () => z.string().trim();

const envSchema = z.object({
    NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
    PORT: str().default('3000'),
    JWT_SECRET: str().min(32, 'JWT_SECRET must be at least 32 characters'),
    JWT_EXPIRES_IN: str().default('7d'),
    MONGO_URI: str().min(1, "MONGO_URI is required"),
    REDIS_HOST: str().default('redis'),
    REDIS_PORT: str().default('6379'),
    AES_SECRET_KEY: str().refine(
        (v) => Buffer.byteLength(v, 'utf-8') === 32,
        'AES_SECRET_KEY must be exactly 32 characters'
    ),
    FRONTEND_URL: str().optional()
});

const parsed = envSchema.safeParse(process.env);
if (!parsed.success) {
    console.log("Invalid env variables:", JSON.stringify(parsed.error.flatten().fieldErrors, null, 2));
    process.exit(1);
}

export const env = parsed.data;

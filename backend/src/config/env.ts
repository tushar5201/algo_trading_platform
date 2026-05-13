import { z } from "zod";

const envSchema = z.object({
    NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
    PORT: z.string().default('3000'),
    JWT_SECRET: z.string().min(32, 'JWT_SECRET must be at least 32 characters'),
    JWT_EXPIRES_IN: z.string().default('7d'),
    MONGO_URI: z.string().min(1, "MONGO_URI is required"),
    REDIS_HOST: z.string().default('redis'),
    REDIS_PORT: z.string().default('6379'),
    AES_SECRET_KEY: z.string().length(32, 'AES_SECRET_KEY must be exactly 32 characters'),
});

const parsed = envSchema.safeParse(process.env);
if (!parsed.success) {
    console.log("Invalid env variables");
    process.exit(1);
}

export const env = parsed.data;

import z from "zod";
import { User } from "../models/User";
import { AppError } from "../middleware/errorhandler";
import { decrypt, encrypt } from "../services/crypto.service";
import { writeAuditLog } from "../services/audit.service";
import { Request, Response } from "express";
import { redis } from "../config/redis";

const credentialsSchema = z.object({
    clientId: z.string().min(1),
    apiKey: z.string().min(1),
    apiSecret: z.string().min(1),
    mobileNumber: z.string().optional(),
    password: z.string().optional()
});

const totpSchema = z.object({
    accessToken: z.string().min(10, 'Kotak API dashboard token required'),
    mobileNumber: z.string().regex(/^\+91\d{10}$/, 'Format: +91XXXXXXXXXX'),
    ucc: z.string().min(2, 'Client code required'),
    totp: z.string().length(6, 'TOTP must be 6 digits'),
});

export const submitTOTP = async (req: Request, res: Response): Promise<void> => {
    const parsed = totpSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, 'User not found');
    if (!user.encryptedCredentials) {
        throw new AppError(400, 'Save your Kotak credentials first');
    }

    // Queue step 1 for Python session manager
    await redis.setex(
        `kotak:auth:step1:${user._id}`,
        300, // 5 min for Python to pick up
        JSON.stringify({
            accessToken: parsed.data.accessToken,
            mobileNumber: parsed.data.mobileNumber,
            ucc: parsed.data.ucc,
            totp: parsed.data.totp,
        })
    );

    await writeAuditLog(user._id.toString(), 'user', 'KOTAK_TOTP_SUBMITTED');

    res.json({
        success: true,
        message: 'TOTP submitted. Now submit your MPIN to complete login.',
    });
};

// ── Step 2: Submit MPIN → activates trading session ──────────────────────
const mpinSchema = z.object({
    mpin: z.string().length(6, 'MPIN must be 6 digits'),
});

export const submitMPIN = async (req: Request, res: Response): Promise<void> => {
    const parsed = mpinSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, 'User not found');

    // Check step 1 view tokens exist (Python stores these)
    const viewTokens = await redis.get(`kotak:auth:view:${user._id}`);
    if (!viewTokens) {
        throw new AppError(400,
            'TOTP step not completed or expired. Submit TOTP first.',
        );
    }

    // Queue step 2 for Python session manager
    await redis.setex(
        `kotak:auth:step2:${user._id}`,
        300,
        JSON.stringify({ mpin: parsed.data.mpin })
    );

    await writeAuditLog(user._id.toString(), 'user', 'KOTAK_MPIN_SUBMITTED');

    res.json({
        success: true,
        message: 'MPIN submitted. Session will be active within seconds.',
    });
};

// ── Check for auth errors ─────────────────────────────────────────────────
export const getAuthStatus = async (req: Request, res: Response): Promise<void> => {
    const userId = req.user!.userId;

    const [sessionRaw, errorRaw, sessionTTL] = await Promise.all([
        redis.get(`kotak:session:${userId}`),
        redis.get(`kotak:auth:error:${userId}`),
        redis.ttl(`kotak:session:${userId}`),
    ]);

    // Clear error after reading
    if (errorRaw) await redis.del(`kotak:auth:error:${userId}`);

    res.json({
        success: true,
        data: {
            hasActiveSession: !!sessionRaw,
            expiresInMinutes: sessionTTL > 0 ? Math.floor(sessionTTL / 60) : 0,
            lastError: errorRaw ?? null,
        },
    });
};


export const saveCredentials = async (req: Request, res: Response): Promise<void> => {
    const parsed = credentialsSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");

    user.encryptedCredentials = encrypt(JSON.stringify(parsed.data));
    await user.save();
    await writeAuditLog(req.user!.userId, 'user', 'SAVE_CREDENTIALS');
    res.status(200).json({ success: true, message: "Credentials saved successfully" });
}

export const toggleAutoTrading = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");
    if (user.status !== "APPROVED") throw new AppError(403, "Your account is not approved for trading");
    if (!user.encryptedCredentials) throw new AppError(400, "No credentials found. Please save your credentials first.");

    user.autoTrading = !user.autoTrading;
    await user.save();
    await writeAuditLog(req.user!.userId, 'user', 'TOGGLE_AUTO_TRADING', undefined, { autoTrading: user.autoTrading });
    res.status(200).json({
        success: true,
        message: "Auto trading toggled successfully",
        data: { autoTrading: user.autoTrading }
    });
}

export const getMyStrategies = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");
    res.json({ success: true, data: { assignedStrategies: user.assignedStrategies } });
}

// STEP 1: Trigger Kotak OTP
export const triggerKotakOTP = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, 'User Not Found');
    if (!user.encryptedCredentials) {
        throw new AppError(400, "No broker credentials saved. Add them first.");
    }

    // Decrypt credentials - just validate they exist & are decryptable
    let credentials: Record<string, string>;
    try {
        credentials = JSON.parse(decrypt(user.encryptedCredentials));
    } catch (error) {
        throw new AppError(400, "Stored credentials are corrupted. Please re-save")
    }
    if (!credentials.mobileNumber) {
        throw new AppError(400, "Mobile number is required in credentials for otp")
    }

    // Store pending OTP state in Redis (expires in 10 min)
    const { redis } = await import("../config/redis");
    await redis.setex(
        `kotak:otp:pending:${user._id}`,
        600,
        JSON.stringify({ requested: true, requestedAt: new Date().toISOString() })
    );

    await writeAuditLog(user._id.toString(), 'user', 'KOTAK_OTP_REQUESTED');
    res.json({
        success: true,
        message: "OTP sent to your registered mobile number. valid for 10 minutes."
    });
};

// STEP 2: Validate OTP -> active session
const otpSchema = z.object({ otp: z.string().length(6) });

export const validateKotakOTP = async (req: Request, res: Response): Promise<void> => {
    const parsed = otpSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, "OTP must be 6 digit");

    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");

    // Check OTP was actually requested
    const { redis } = await import("../config/redis");
    const pending = await redis.get(`kotak:otp:pending:${user._id}`);
    if (!pending) throw new AppError(400, "No OTP request found or has expired. Request a new otp");

    // Store OTP for Python worker to pick up and complete auth
    await redis.setex(
        `kotak:otp:validate:${user._id}`,
        300, // 5 minutes for python to pick up
        JSON.stringify({
            otp: parsed.data.otp,
            userId: user._id.toString(),
            requestedAt: new Date().toISOString()
        })
    );
    await redis.del(`kotak:otp:pending:${user._id}`);
    await writeAuditLog(user._id.toString(), 'user', 'KOTAK_OTP_SUBMITTED');
    res.json({
        success: true,
        message: "OTP submitted. Session will be activated within seconds."
    });
};

// Check Session Status
export const getKotakSessionStatus = async (req: Request, res: Response): Promise<void> => {
    const { redis } = await import("../config/redis");
    const sessionKey = `kotak:session:${req.user!.userId}`;
    const session = await redis.get(sessionKey);
    const ttl = await redis.ttl(sessionKey);

    res.json({
        success: true,
        data: {
            hasActiveSession: !!session,
            expiresInMinutes: ttl > 0 ? Math.floor(ttl / 60) : 0,
        }
    });
};


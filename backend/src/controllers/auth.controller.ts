import { z } from "zod";
import { Request, Response } from "express";
import { AppError } from "../middleware/errorhandler";
import { comparePassword, hashPassword, signToken } from "../services/auth.service";
import { User } from "../models/User";
import { id } from "zod/v4/locales";
import { writeAuditLog } from "../services/audit.service";

const signupSchema = z.object({
    name: z.string().min(2, 'Name must be at least 2 characters long').max(50, 'Name must be less than 50 characters long'),
    email: z.string().email(),
    password: z.string().min(8, 'Password must be at least 8 characters long'),
});

const loginSchema = z.object({
    email: z.string().email(),
    password: z.string().min(1),
});

export const signup = async (req: Request, res: Response): Promise<void> => {
    const parsed = signupSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const { name, email, password } = parsed.data;
    const existingUser = await User.findOne({ email });
    if (existingUser) throw new AppError(409, "Email already exists");

    const passwordHash = await hashPassword(password);
    const newUser = new User({ name, email, passwordHash });
    await newUser.save();
    await writeAuditLog(newUser._id.toString(), 'user', 'USER_SIGNUP', undefined, { email });

    res.status(201).json({
        message: "Account created. Awaiting admin approval.",
        data: {
            id: newUser._id,
            email: newUser.email,
            status: newUser.status
        }
    });
};

export const login = async (req: Request, res: Response): Promise<void> => {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) throw new AppError(400, parsed.error.errors[0].message);

    const { email, password } = parsed.data;

    const user = await User.findOne({ email }).select("+passwordHash");
    if (!user) throw new AppError(401, "Invalid email or password");

    if (user.status === "PENDING") throw new AppError(403, `Your account is pending admin approval.`);
    if (user.status === "REJECTED") throw new AppError(403, `Your account has been rejected.`);
    if (!user.isActive) throw new AppError(403, `Your account is inactive.`);

    const valid = await comparePassword(password, user.passwordHash);
    if (!valid) throw new AppError(401, "Invalid Credentials");

    const token = signToken(user);
    await writeAuditLog(user._id.toString(), user.role, 'USER_LOGIN', undefined, { email });

    res.json({
        data: {
            token,
            user: {
                id: user._id,
                name: user.name,
                email: user.email,
                role: user.role,
                assignedStrategies: user.assignedStrategies,
                autoTrading: user.autoTrading
            }
        }
    });
};

export const getMe = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");
    res.json({
        data: user
    });
}

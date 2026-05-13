import z from "zod";
import { User } from "../models/User";
import { AppError } from "../middleware/errorhandler";
import { encrypt } from "../services/crypto.service";
import { writeAuditLog } from "../services/audit.service";
import { Request, Response } from "express";

const credentialsSchema = z.object({
    clientId: z.string().min(1),
    apiKey: z.string().min(1),
    apiSecret: z.string().min(1),
    mobileNumber: z.string().optional(),
    password: z.string().optional()
});

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
    res.status(200).json({ success: true, message: "Auto trading toggled successfully", data: { autoTrading: user.autoTrading } });
}

export const getMyStrategies = async (req: Request, res: Response): Promise<void> => {
    const user = await User.findById(req.user!.userId);
    if (!user) throw new AppError(404, "User not found");
    res.json({ success: true, data: { assignedStrategies: user.assignedStrategies } });
}

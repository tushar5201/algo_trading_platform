import mongoose from "mongoose";
import { Auditlog } from "../models/Auditlog"

export const writeAuditLog = async (
    actorId: string,
    actorRole: string,
    action: string,
    targetId?: string,
    metaData?: Record<string, unknown>
): Promise<void> => {
    try {
        await Auditlog.create({
            actorId: new mongoose.Types.ObjectId(actorId),
            actorRole,
            action,
            targetId: targetId ? new mongoose.Types.ObjectId(targetId) : null,
            metaData
        });
    } catch (error) {
        console.error("Failed to write audit log:", error);
    }
}

import mongoose, { Document, Schema } from "mongoose";

export interface IAuditlog extends Document {
    actorId: mongoose.Types.ObjectId;
    action: string;
    actorRole: string;
    targetId: mongoose.Types.ObjectId | null;
    metaData: Record<string, unknown>;
    createdAt: Date;
}

const AuditlogSchema = new mongoose.Schema<IAuditlog>({
    actorId: { type: Schema.Types.ObjectId, required: true, ref: 'User' },
    action: { type: String, required: true },
    actorRole: { type: String, required: true },
    targetId: { type: Schema.Types.ObjectId, ref: 'User', default: null },
    metaData: { type: Schema.Types.Mixed, default: {} }
}, {
    timestamps: true
});

export const Auditlog = mongoose.model<IAuditlog>('AuditLog', AuditlogSchema);

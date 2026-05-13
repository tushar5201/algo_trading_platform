import mongoose, { Document } from "mongoose";

export interface IKillSwitch extends Document {
    scope: 'global' | 'user';
    userId: mongoose.Types.ObjectId | null;
    isActive: boolean;
    reason: string;
    sortBy: mongoose.Types.ObjectId;
}

const KillSwitchSchema = new mongoose.Schema<IKillSwitch>({
    scope: { type: String, enum: ['global', 'user'], required: true },
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User', default: null },
    isActive: { type: Boolean, required: true },
    reason: { type: String, default: '' },
    sortBy: { type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true },
}, {
    timestamps: true,
});

export const KillSwitch = mongoose.model<IKillSwitch>('KillSwitch', KillSwitchSchema);

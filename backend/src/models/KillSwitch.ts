import mongoose, {Document} from "mongoose";

export interface IKillSwitch extends Document {
    scope: 'global' | 'user';
    userId: mongoose.Types.ObjectId | null;
    isActive: boolean;
    reason: string;
    setBy: mongoose.Types.ObjectId;
    createdAt: Date;
    updatedAt: Date;
}

const KillSwitchSchema = new mongoose.Schema<IKillSwitch>({
    scope: {type: String, enum: ['global', 'user'], required: true},
    userId: {type: mongoose.Schema.Types.ObjectId, ref: 'User', default: null},
    isActive: {type: Boolean, required: true},
    reason: {type: String, default: ''},
    setBy: {type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true},
}, {
    timestamps: true,
});

KillSwitchSchema.index({scope: 1, userId: 1}, {unique: true});

export const KillSwitch = mongoose.model<IKillSwitch>('KillSwitch', KillSwitchSchema);
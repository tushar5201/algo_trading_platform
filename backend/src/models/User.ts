import mongoose, { Document, Schema } from "mongoose";


export type UserRole = 'admin' | 'user';
export type UserStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface IUser extends Document {
    _id: mongoose.Types.ObjectId;
    name: string;
    email: string;
    passwordHash: string;
    role: UserRole;
    status: UserStatus;
    assignedStrategies: string[];
    encryptedCredentials: string | null;
    autoTrading: boolean;
    isActive: boolean;
    createdAt: Date;
    updatedAt: Date;
    riskLimits: {
        maxDailyLoss: number,
        maxDailyTrades: number
    }
}

const UserSchema = new Schema<IUser> ({
    name: {
        type: String,
        required: true,
        trim: true
    },
    email: {
        type: String,
        required: true,
        unique: true,
        lowercase: true,
        trim: true
    },
    passwordHash: {
        type: String,
        required: true,
        select: false
    },
    role: {
        type: String,
        enum: ['admin', 'user'], 
        default: 'user'
    },
    status: {
        type: String,
        enum: ['PENDING', 'APPROVED', 'REJECTED'],
        default: 'PENDING'
    },
    assignedStrategies: {
        type: [String],
        default: []
    },
    encryptedCredentials: {
        type: String,
        default: null,
        select: true
    },
    autoTrading: {
        type: Boolean,
        default: false
    },
    isActive: {
        type: Boolean,
        default: true
    },
    riskLimits: {
        maxDailyLoss: {type: Number, default: 50000},
        maxDailyTrades: {type: Number, default: 50},
    }
});

UserSchema.set('toJSON', {
    transform: (doc, ret: any) => {
        delete ret.passwordHash;
        delete ret.encryptedCredentials;
        return ret;
    },
});

export const User = mongoose.model<IUser>('User', UserSchema);
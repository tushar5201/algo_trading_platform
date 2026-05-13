import mongoose, { Document, Schema } from "mongoose";

export interface IStrategy extends Document { 
    name: string;
    displayName: string;
    description: string;
    riskLevel: 'LOW' | 'MEDIUM' | 'HIGH';
    isActive: boolean;
}

const strategySchema = new Schema<IStrategy>({
    name: { type: String, required: true, unique: true, lowercase: true },
    displayName: { type: String, required: true },
    description: { type: String, required: true },
    riskLevel: { type: String, enum: ['LOW', 'MEDIUM', 'HIGH'], default: 'MEDIUM'},
    isActive: { type: Boolean, default: true}
}, {
    timestamps: true
});

export const Strategy = mongoose.model<IStrategy>('Strategy', strategySchema);

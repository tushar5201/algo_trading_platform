import mongoose, { Document } from "mongoose";

export type TradeSide = 'BUY' | 'SELL';
export type TradeStatus = 'PENDING' | 'EXECUTED' | 'FAILED' | 'CANCELED';

export interface ITrade extends Document {
    userId: mongoose.Types.ObjectId;
    strategyName: string;
    symbol: string;
    side: TradeSide;
    orderId: string | null;
    pnl: number | null;
    status: TradeStatus;
    price: number;
    qty: number;
    errorMessage: string | null;
    executedAt: Date | null;
    createdAt: Date;
}

const TradeSchema = new mongoose.Schema<ITrade>({
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true, index: true },
    strategyName: { type: String, required: true },
    symbol: { type: String, required: true },
    side: { type: String, enum: ['BUY', 'SELL'], required: true },
    orderId: { type: String, default: null },
    pnl: { type: Number, default: null },
    status: { type: String, enum: ['PENDING', 'EXECUTED', 'FAILED', 'CANCELED'], default: 'PENDING' },
    price: { type: Number, required: true },
    qty: { type: Number, required: true },
    errorMessage: { type: String, default: null },
    executedAt: { type: Date, default: null },
}, {
    timestamps: true,
});

export const Trade = mongoose.model<ITrade>('Trade', TradeSchema);

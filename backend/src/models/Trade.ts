import mongoose, {Document} from "mongoose";

export type TradeSide = 'BUY' | 'SELL';
export type TradeStatus = 'PENDING' | 'EXECUTED' | 'EXIT_PENDING' | 'CLOSED' | 'FAILED' | 'CANCELED' | 'NEEDS_REVIEW';

export const TRADE_STATUSES: TradeStatus[] = ['PENDING', 'EXECUTED', 'EXIT_PENDING', 'CLOSED', 'FAILED', 'CANCELED', 'NEEDS_REVIEW'];

export interface ITrade extends Document {
    userId: mongoose.Types.ObjectId;
    strategyName: string;
    symbol: string;
    brokerSymbol?: string | null;
    side: TradeSide;
    orderId: string | null;
    pnl: number | null;
    status: TradeStatus;
    price: number;
    qty: number;
    exchange?: string | null;
    exchangeSegment?: string | null;
    productType?: string | null;
    symbolToken?: string | null;
    stopLoss?: number | null;
    takeProfit?: number | null;
    slOrderId?: string | null;
    exitPrice?: number | null;
    exitOrderId?: string | null;
    exitReason?: string | null;
    exitAttempts?: number;
    errorMessage: string | null;
    executedAt: Date | null;
    createdAt: Date;
    closedAt?: Date | null;
}

const TradeSchema = new mongoose.Schema<ITrade>({
    userId: {type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true},
    strategyName: {type: String, required: true},
    symbol: {type: String, required: true},
    brokerSymbol: {type: String, default: null},
    side: {type: String, enum: ['BUY', 'SELL'], required: true},
    orderId: {type: String, default: null},
    pnl: {type: Number, default: null},
    status: {type: String, enum: TRADE_STATUSES, default: 'PENDING'},
    price: {type: Number, required: true},
    qty: {type: Number, required: true},
    exchange: {type: String, default: null},
    exchangeSegment: {type: String, default: null},
    productType: {type: String, default: null},
    symbolToken: {type: String, default: null},
    stopLoss: {type: Number, default: null},
    takeProfit: {type: Number, default: null},
    slOrderId: {type: String, default: null},
    exitPrice: {type: Number, default: null},
    exitOrderId: {type: String, default: null},
    exitReason: {type: String, default: null},
    exitAttempts: {type: Number, default: 0},
    errorMessage: {type: String, default: null},
    executedAt: {type: Date, default: null},
    closedAt: {type: Date, default: null},
}, {
    timestamps: true,
});

TradeSchema.index({userId: 1, createdAt: -1});
TradeSchema.index({userId: 1, status: 1});
TradeSchema.index({status: 1});

export const Trade = mongoose.model<ITrade>('Trade', TradeSchema);

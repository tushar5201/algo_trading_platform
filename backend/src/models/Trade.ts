import mongoose, {Document} from "mongoose";

export type TradeSide = 'BUY' | 'SELL';
export type TradeStatus = 'PENDING' | 'EXECUTED' | 'EXIT_PENDING' | 'CLOSED' | 'FAILED' | 'CANCELED' | 'NEEDS_REVIEW';

export type AiReviewStatus = "PENDING" | "DONE" | "FAILED" | "SKIPPED";
export const AI_REVIEW_STATUSES: AiReviewStatus[] = ["PENDING", "DONE", "FAILED", "SKIPPED"];
export const TRADE_STATUSES: TradeStatus[] = ['PENDING', 'EXECUTED', 'EXIT_PENDING', 'CLOSED', 'FAILED', 'CANCELED', 'NEEDS_REVIEW'];

export interface IAiReview {
    status: AiReviewStatus;
    summary?: string | null;
    bullets?: string[];
    riskNotes?: string[];
    whatWorked?: string | null;
    whatDidnt?: string | null;
    confidence?: number | null;
    model?: string | null;
    promptVersion?: string | null;
    createdAt?: Date | null;
    tokensIn?: number;
    tokensOut?: number;
    error?: string | null;
}

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
    confidence?: number | null;
    reason?: string | null;
    metadata?: Record<string, unknown> | null;
    aiReview?: IAiReview | null;
}

const AiReviewSchema = new mongoose.Schema<IAiReview>({
    status: {type: String, enum: AI_REVIEW_STATUSES, required: true},
    summary: {type: String, default: null},
    bullets: {type: [String], default: []},
    riskNotes: {type: [String], default: []},
    whatWorked: {type: String, default: null},
    whatDidnt: {type: String, default: null},
    confidence: {type: Number, default: null},
    model: {type: String, default: null},
    promptVersion: {type: String, default: null},
    createdAt: {type: Date, default: null},
    tokensIn: {type: Number, default: 0},
    tokensOut: {type: Number, default: 0},
    error: {type: String, default: null},
}, {_id: false});

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
    confidence: {type: Number, default: null},
    reason: {type: String, default: null},
    metadata: {type: mongoose.Schema.Types.Mixed, default: null},
    aiReview: {type: AiReviewSchema, default: undefined},
}, {
    timestamps: true,
});

TradeSchema.index({userId: 1, createdAt: -1});
TradeSchema.index({userId: 1, status: 1});
TradeSchema.index({status: 1});

export const Trade = mongoose.model<ITrade>('Trade', TradeSchema);

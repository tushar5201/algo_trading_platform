
// StragetyRunJob
// Published by: Node Scheduler (every 5 mins) or admin trigger 
// Consumed by: Python strategy engine

export interface StrategyRunJob {
    jobType: 'STRATEGY_RUN';
    userId: string;
    userEmail: string;
    strategyName: string;
    encryptedCredentials: string; // Encrypted credentials for the strategy to access necessary resources
    autoTrading: boolean; 
    triggeredBy: 'NODE_SCHEDULER' | 'ADMIN' | 'USER';
    timestamp: string; 
} 

// Trade Executor job
// Published by: Python strategy engine when a trade signal is generated
// Consumed by: Python trade executor

export interface TradeExecutionJob {
    jobType: 'TRADE_EXECUTION';
    userId: string;
    strategyName: string;
    symbol: string; 
    side: 'BUY' | 'SELL';
    qty: number;
    price: number;
    orderType: 'MARKET' | 'LIMIT';
    productType: 'MIS' | 'NORMAL';
    exchange: 'NSE' | 'BSE' | 'NFO';
    encryptedCredentials: string;
    tradeId: string; 
    timestamp: string;
}

// Notification Job
// Future use: email/SMS alert
export interface NotificationJob {
    jobType: 'NOTIFY';
    userId: string;
    channel: 'EMAIL' | 'WEBHOOK';
    subject: string;
    body: string;
    timestamp: string;
}

export type AnyJob = StrategyRunJob | TradeExecutionJob | NotificationJob;


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

export type AnyJob = StrategyRunJob | NotificationJob;

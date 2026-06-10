export type UserRole = 'admin' | 'user';
export type UserStatus = 'PENDING' | 'REJECTED' | 'APPROVED';

export interface User {
    id: string;
    name: string;
    email: string;
    role: UserRole;
    status: UserStatus;
    autoTrading: boolean;
    assignedStrategies: string[];
}

export interface AuthState {
    token: string | null;
    user: User | null;
    setAuth: (token: string, user: User) => void;
    clearAuth: () => void;
}

export interface Trade {
    _id: string;
    strategyName: string;
    symbol: string;
    side: 'BUY' | 'SELL';
    qty: number;
    price: number;
    status: 'PENDING' | 'FAILED' | 'EXECUTED' | 'CANCELLED' | 'CLOSED';
    pnl: number | null;
    orderId: string | null;
    createdAt: string;
    executedAt: string | null;
}

export interface PnLSummary {
    totalPnl: number;
    totalTrades: number;
    winTrades: number;
    lossTrades: number;
    winRate: string;
    bestTrade: number;
    worstTrade: number;
}

export interface Strategy {
    _id: string;
    name: string;
    displayName: string;
    description: string;
    riskLevel: 'LOW' | 'MEDIUM' | 'HIGH';
    isActive: boolean
}

export interface AdminUser {
    _id: string;
    name: string;
    email: string;
    role: UserRole;
    status: UserStatus;
    autoTrading: boolean;
    assignedStrategies: string[];
    createdAt: string;
}
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    TrendingUp, Activity, Zap,
    ZapOff, RefreshCw, AlertCircle,
} from 'lucide-react';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid,
    Tooltip, ResponsiveContainer,
} from 'recharts';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import clsx from 'clsx';
import type {PnLSummary, Trade} from "../../types";

// ── Sub-components ───────────────────────────────────────────────────────────

function StatCard({
                      label, value, sub, positive,
                  }: { label: string; value: string; sub?: string; positive?: boolean }) {
    return (
        <div className="card p-5">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                {label}
            </p>
            <p className={clsx(
                'text-2xl font-bold mt-1',
                positive === true  ? 'text-emerald-600 dark:text-emerald-400' :
                    positive === false ? 'text-red-600 dark:text-red-400' :
                        'text-gray-900 dark:text-white'
            )}>
                {value}
            </p>
            {sub && (
                <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</p>
            )}
        </div>
    );
}

function TradeRow({ trade }: { trade: Trade }) {
    const statusColor = {
        EXECUTED:  'badge-green',
        PENDING:   'badge-yellow',
        FAILED:    'badge-red',
        CANCELLED: 'badge-red',
        CLOSED:    'badge-blue',
    }[trade.status] ?? 'badge-yellow';

    return (
        <tr className="border-b border-gray-100 dark:border-gray-800
                   hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
            <td className="px-4 py-3 text-sm font-medium text-gray-900 dark:text-white">
                {trade.symbol}
            </td>
            <td className="px-4 py-3">
        <span className={trade.side === 'BUY' ? 'badge-green' : 'badge-red'}>
          {trade.side}
        </span>
            </td>
            <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                {trade.qty}
            </td>
            <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                ₹{trade.price.toFixed(2)}
            </td>
            <td className="px-4 py-3">
                <span className={statusColor}>{trade.status}</span>
            </td>
            <td className="px-4 py-3 text-sm font-medium">
                {trade.pnl !== null ? (
                    <span className={trade.pnl >= 0
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-red-600 dark:text-red-400'}>
            {trade.pnl >= 0 ? '+' : ''}₹{trade.pnl.toFixed(2)}
          </span>
                ) : (
                    <span className="text-gray-400">—</span>
                )}
            </td>
            <td className="px-4 py-3 text-xs text-gray-400">
                {new Date(trade.createdAt).toLocaleString('en-IN', {
                    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                })}
            </td>
        </tr>
    );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function DashboardPage() {
    const { user, setAuth } = useAuthStore();
    const qc                = useQueryClient();

    const { data: pnlData } = useQuery({
        queryKey: ['pnl'],
        queryFn:  () => api.get('/trades/pnl').then(r => r.data.data),
        refetchInterval: 60_000,
    });

    const { data: tradesData } = useQuery({
        queryKey: ['trades', 'recent'],
        queryFn:  () => api.get('/trades?limit=5').then(r => r.data.data),
        refetchInterval: 30_000,
    });

    const { data: sessionData } = useQuery({
        queryKey: ['kotak-session'],
        queryFn:  () => api.get('/user/kotak/auth-status').then(r => r.data.data),
        refetchInterval: 30_000,
    });

    const { data: strategiesData } = useQuery({
        queryKey: ['my-strategies'],
        queryFn:  () => api.get('/user/strategies').then(r => r.data.data),
    });

    const toggleMutation = useMutation({
        mutationFn: () => api.patch('/user/toggle-trading'),
        onSuccess: (res) => {
            if (user) {
                setAuth(
                    localStorage.getItem('auth-storage')
                        ? JSON.parse(localStorage.getItem('auth-storage')!).state.token
                        : '',
                    { ...user, autoTrading: res.data.data.autoTrading }
                );
            }
            qc.invalidateQueries({ queryKey: ['kotak-session'] });
        },
    });

    const summary: PnLSummary = pnlData?.summary ?? {
        totalPnl: 0, totalTrades: 0, winTrades: 0,
        lossTrades: 0, winRate: '0%', bestTrade: 0, worstTrade: 0,
    };

    const chartData = pnlData?.byDay?.map((d: any) => ({
        date:   d._id,
        pnl:    d.pnl,
        trades: d.trades,
    })) ?? [];

    const trades: Trade[] = tradesData?.trades ?? [];

    return (
        <div className="p-6 space-y-6 max-w-7xl mx-auto">

            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold text-gray-900 dark:text-white">
                        Dashboard
                    </h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                        Welcome back, {user?.name}
                    </p>
                </div>

                {/* Auto trading toggle */}
                <div className="flex items-center gap-3">
                    <div className={clsx(
                        'flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium',
                        sessionData?.hasActiveSession
                            ? 'bg-emerald-100 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                            : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
                    )}>
            <span className={clsx(
                'w-1.5 h-1.5 rounded-full',
                sessionData?.hasActiveSession ? 'bg-emerald-500' : 'bg-gray-400'
            )} />
                        {sessionData?.hasActiveSession
                            ? `Session active · ${sessionData.expiresInMinutes}m left`
                            : 'No broker session'}
                    </div>

                    <button
                        onClick={() => toggleMutation.mutate()}
                        disabled={toggleMutation.isPending}
                        className={clsx(
                            'flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all',
                            user?.autoTrading
                                ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                                : 'bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300'
                        )}
                    >
                        {user?.autoTrading
                            ? <><Zap className="w-4 h-4" /> Auto ON</>
                            : <><ZapOff className="w-4 h-4" /> Auto OFF</>}
                    </button>
                </div>
            </div>

            {/* Kotak session warning */}
            {!sessionData?.hasActiveSession && (
                <div className="flex items-start gap-3 p-4 rounded-xl bg-amber-50
                        dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
                    <AlertCircle className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                    <div>
                        <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
                            Broker session required
                        </p>
                        <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
                            Go to Credentials to complete TOTP → MPIN authentication before enabling auto trading.
                        </p>
                    </div>
                </div>
            )}

            {/* Stat cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard
                    label="Total P&L"
                    value={`${summary.totalPnl >= 0 ? '+' : ''}₹${summary.totalPnl.toFixed(2)}`}
                    positive={summary.totalPnl >= 0}
                />
                <StatCard
                    label="Total trades"
                    value={String(summary.totalTrades)}
                    sub={`${summary.winTrades} wins · ${summary.lossTrades} losses`}
                />
                <StatCard
                    label="Win rate"
                    value={summary.winRate}
                    positive={parseFloat(summary.winRate) >= 50}
                />
                <StatCard
                    label="Best trade"
                    value={`₹${(summary?.bestTrade ?? 0).toFixed(2)}`}
                    positive={(summary?.bestTrade ?? 0) >= 0}
                />
            </div>

            {/* Chart + Strategies */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                {/* PnL Chart */}
                <div className="card p-5 lg:col-span-2">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                            P&L — Last 30 days
                        </h2>
                        <TrendingUp className="w-4 h-4 text-gray-400" />
                    </div>

                    {chartData.length === 0 ? (
                        <div className="h-48 flex items-center justify-center">
                            <p className="text-sm text-gray-400 dark:text-gray-500">
                                No trade data yet
                            </p>
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height={200}>
                            <AreaChart data={chartData}>
                                <defs>
                                    <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.2} />
                                        <stop offset="95%" stopColor="#6366f1" stopOpacity={0}   />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"
                                               className="dark:stroke-gray-800" />
                                <XAxis dataKey="date" tick={{ fontSize: 11 }}
                                       tickFormatter={d => d.slice(5)} />
                                <YAxis tick={{ fontSize: 11 }}
                                       tickFormatter={v => `₹${v}`} />
                                <Tooltip
                                    // formatter={(v: number) => [`₹${v.toFixed(2)}`, 'P&L']}
                                    contentStyle={{
                                        background: 'var(--tooltip-bg, #fff)',
                                        border:     '1px solid #e5e7eb',
                                        borderRadius: 12,
                                        fontSize: 12,
                                    }}
                                />
                                <Area
                                    type="monotone"
                                    dataKey="pnl"
                                    stroke="#6366f1"
                                    strokeWidth={2}
                                    fill="url(#pnlGrad)"
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    )}
                </div>

                {/* Assigned strategies */}
                <div className="card p-5">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white mb-4">
                        Assigned strategies
                    </h2>
                    {(strategiesData?.assignedStrategies ?? []).length === 0 ? (
                        <p className="text-sm text-gray-400 dark:text-gray-500">
                            No strategies assigned yet
                        </p>
                    ) : (
                        <ul className="space-y-2">
                            {(strategiesData?.assignedStrategies ?? []).map((s: string) => (
                                <li key={s}
                                    className="flex items-center gap-3 p-3 rounded-xl
                             bg-gray-50 dark:bg-gray-800">
                                    <Activity className="w-4 h-4 text-brand-500 shrink-0" />
                                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                    {s.replace(/_/g, ' ')}
                  </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>

            {/* Recent trades */}
            <div className="card overflow-hidden">
                <div className="flex items-center justify-between px-5 py-4
                        border-b border-gray-100 dark:border-gray-800">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                        Recent trades
                    </h2>
                    <a href="/trades"
                       className="text-xs text-brand-600 dark:text-brand-400 hover:underline font-medium">
                        View all
                    </a>
                </div>

                {trades.length === 0 ? (
                    <div className="p-8 text-center">
                        <RefreshCw className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
                        <p className="text-sm text-gray-400 dark:text-gray-500">No trades yet</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead>
                            <tr className="border-b border-gray-100 dark:border-gray-800">
                                {['Symbol', 'Side', 'Qty', 'Price', 'Status', 'P&L', 'Time'].map(h => (
                                    <th key={h}
                                        className="px-4 py-3 text-xs font-medium text-gray-500
                                 dark:text-gray-400 uppercase tracking-wider">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                            </thead>
                            <tbody>
                            {trades.map(t => <TradeRow key={t._id} trade={t} />)}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
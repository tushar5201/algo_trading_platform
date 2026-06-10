import { useQuery, useMutation } from '@tanstack/react-query';
import { Users, Activity, AlertTriangle, Server, RefreshCw } from 'lucide-react';
import { api } from '../../api/client';
import clsx from 'clsx';

export default function AdminDashboard() {
    const { data: usersData } = useQuery({
        queryKey: ['admin-users'],
        queryFn:  () => api.get('/admin/users?limit=5').then(r => r.data.data),
        refetchInterval: 30_000,
    });

    const { data: queueData, refetch: refetchQueue } = useQuery({
        queryKey: ['queue-status'],
        queryFn:  () => api.get('/admin/queue-status').then(r => r.data.data),
        refetchInterval: 15_000,
    });

    const { data: healthData } = useQuery({
        queryKey: ['system-health'],
        queryFn:  () => api.get('/admin/health').then(r => r.data.data),
        refetchInterval: 30_000,
    });

    const killMutation = useMutation({
        mutationFn: (active: boolean) =>
            api.post('/admin/kill-switch', { isActive: active, reason: 'Admin manual trigger' }),
    });

    const pending = usersData?.users?.filter((u: any) => u.status === 'PENDING') ?? [];
    const sr      = queueData?.strategyRunner ?? {};
    const te      = queueData?.tradeExecutor  ?? {};

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">

            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold text-gray-900 dark:text-white">Admin dashboard</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                        Platform overview
                    </p>
                </div>

                {/* Global kill switch */}
                <div className="flex gap-3">
                    <button
                        onClick={() => killMutation.mutate(true)}
                        disabled={killMutation.isPending}
                        className="btn-danger"
                    >
                        <AlertTriangle className="w-4 h-4" />
                        Kill all trading
                    </button>
                    <button
                        onClick={() => killMutation.mutate(false)}
                        disabled={killMutation.isPending}
                        className="btn-secondary"
                    >
                        Resume trading
                    </button>
                </div>
            </div>

            {/* Stat cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {[
                    {
                        label: 'Total users',
                        value: usersData?.total ?? '—',
                        icon:  Users,
                        color: 'text-blue-500',
                        bg:    'bg-blue-50 dark:bg-blue-900/20',
                    },
                    {
                        label: 'Pending approval',
                        value: pending.length,
                        icon:  AlertTriangle,
                        color: pending.length > 0 ? 'text-amber-500' : 'text-gray-400',
                        bg:    pending.length > 0
                            ? 'bg-amber-50 dark:bg-amber-900/20'
                            : 'bg-gray-50 dark:bg-gray-800',
                    },
                    {
                        label: 'Strategy jobs waiting',
                        value: sr.waiting ?? '—',
                        icon:  Activity,
                        color: 'text-brand-500',
                        bg:    'bg-brand-50 dark:bg-brand-900/20',
                    },
                    {
                        label: 'System status',
                        value: healthData?.status === 'healthy' ? 'Healthy' : 'Degraded',
                        icon:  Server,
                        color: healthData?.status === 'healthy' ? 'text-emerald-500' : 'text-red-500',
                        bg:    healthData?.status === 'healthy'
                            ? 'bg-emerald-50 dark:bg-emerald-900/20'
                            : 'bg-red-50 dark:bg-red-900/20',
                    },
                ].map(({ label, value, icon: Icon, color, bg }) => (
                    <div key={label} className="card p-5">
                        <div className="flex items-center gap-3 mb-3">
                            <div className={clsx('w-9 h-9 rounded-xl flex items-center justify-center', bg)}>
                                <Icon className={clsx('w-5 h-5', color)} />
                            </div>
                            <p className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</p>
                        </div>
                        <p className="text-2xl font-bold text-gray-900 dark:text-white">{String(value)}</p>
                    </div>
                ))}
            </div>

            {/* Queue status + Health */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                {/* Queue */}
                <div className="card p-5">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                            Queue status
                        </h2>
                        <button onClick={() => refetchQueue()}
                                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
                            <RefreshCw className="w-4 h-4" />
                        </button>
                    </div>
                    <div className="space-y-3">
                        {[
                            { label: 'Strategy runner — waiting', value: sr.waiting ?? 0 },
                            { label: 'Strategy runner — active',  value: sr.active  ?? 0 },
                            { label: 'Strategy runner — failed',  value: sr.failed  ?? 0, warn: true },
                            { label: 'Trade executor — waiting',  value: te.waiting ?? 0 },
                            { label: 'Trade executor — failed',   value: te.failed  ?? 0, warn: true },
                        ].map(({ label, value, warn }) => (
                            <div key={label} className="flex items-center justify-between">
                                <span className="text-xs text-gray-600 dark:text-gray-400">{label}</span>
                                <span className={clsx(
                                    'text-xs font-semibold px-2 py-0.5 rounded-full',
                                    warn && value > 0
                                        ? 'bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400'
                                        : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'
                                )}>
                  {value}
                </span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* System health */}
                <div className="card p-5">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white mb-4">
                        System health
                    </h2>
                    {healthData ? (
                        <div className="space-y-3">
                            {Object.entries(healthData.checks ?? {}).map(([key, val]: any) => (
                                <div key={key} className="flex items-center justify-between">
                  <span className="text-xs text-gray-600 dark:text-gray-400 capitalize">
                    {key}
                  </span>
                                    <span className={clsx(
                                        'text-xs font-medium px-2 py-0.5 rounded-full',
                                        val.status === 'up'
                                            ? 'bg-emerald-100 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
                                            : 'bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400'
                                    )}>
                    {val.status ?? 'unknown'}
                  </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="text-sm text-gray-400 dark:text-gray-500">Loading…</p>
                    )}
                </div>
            </div>

            {/* Pending users */}
            {pending.length > 0 && (
                <div className="card overflow-hidden">
                    <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
                        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                            Pending approvals ({pending.length})
                        </h2>
                    </div>
                    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                        {pending.map((u: any) => (
                            <li key={u._id} className="flex items-center gap-4 px-5 py-3">
                                <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-900/30
                                flex items-center justify-center shrink-0">
                  <span className="text-xs font-semibold text-amber-700 dark:text-amber-400">
                    {u.name?.[0]?.toUpperCase()}
                  </span>
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                                        {u.name}
                                    </p>
                                    <p className="text-xs text-gray-400 truncate">{u.email}</p>
                                </div>
                                <a href="/admin/users"
                                   className="text-xs text-brand-600 dark:text-brand-400 font-medium hover:underline">
                                    Review →
                                </a>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
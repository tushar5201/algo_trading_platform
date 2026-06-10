import {useState} from 'react';
import {useQuery, useMutation, useQueryClient} from '@tanstack/react-query';
import {Search, Check, X, Zap, ZapOff} from 'lucide-react';
import {api} from '../../api/client';
import clsx from 'clsx';
import type {AdminUser} from "../../types";

export default function UsersPage() {
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState('ALL');
    const qc = useQueryClient();

    const {data, isLoading} = useQuery({
        queryKey: ['admin-users', filter],
        queryFn: () =>
            api.get(`/admin/users${filter !== 'ALL' ? `?status=${filter}` : ''}`)
                .then(r => r.data.data),
    });

    const approveMutation = useMutation({
        mutationFn: ({userId, status}: { userId: string; status: string }) =>
            api.patch(`/admin/users/${userId}/status`, {status}),
        onSuccess: () => qc.invalidateQueries({queryKey: ['admin-users']}),
    });

    const tradeMutation = useMutation({
        mutationFn: (userId: string) => api.patch(`/admin/users/${userId}/toggle-trading`),
        onSuccess: () => qc.invalidateQueries({queryKey: ['admin-users']}),
    });

    const users: AdminUser[] = (data?.users ?? []).filter((u: AdminUser) =>
        u.name.toLowerCase().includes(search.toLowerCase()) ||
        u.email.toLowerCase().includes(search.toLowerCase())
    );

    const statusBadge = (s: string) => ({
        APPROVED: <span className="badge-green">Approved</span>,
        PENDING: <span className="badge-yellow">Pending</span>,
        REJECTED: <span className="badge-red">Rejected</span>,
    }[s] ?? <span className="badge-yellow">{s}</span>);

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">
            <div>
                <h1 className="text-xl font-bold text-gray-900 dark:text-white">Users</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                    Manage user accounts and permissions
                </p>
            </div>

            {/* Filters */}
            <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"/>
                    <input
                        className="input-base pl-9"
                        placeholder="Search by name or email…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                </div>
                <div className="flex gap-2">
                    {['ALL', 'PENDING', 'APPROVED', 'REJECTED'].map(f => (
                        <button
                            key={f}
                            onClick={() => setFilter(f)}
                            className={clsx(
                                'px-3 py-2 rounded-xl text-xs font-medium transition-all',
                                filter === f
                                    ? 'bg-brand-600 text-white'
                                    : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
                            )}
                        >
                            {f}
                        </button>
                    ))}
                </div>
            </div>

            {/* Table */}
            <div className="card overflow-hidden">
                {isLoading ? (
                    <div className="p-8 text-center">
                        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent
                            rounded-full animate-spin mx-auto"/>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-sm">
                            <thead className="border-b border-gray-100 dark:border-gray-800">
                            <tr>
                                {['User', 'Status', 'Auto trading', 'Strategies', 'Joined', 'Actions'].map(h => (
                                    <th key={h} className="px-4 py-3 text-xs font-medium
                                           text-gray-500 dark:text-gray-400
                                           uppercase tracking-wider whitespace-nowrap">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                            </thead>
                            <tbody>
                            {users.map(user => (
                                <tr key={user._id}
                                    className="border-b border-gray-50 dark:border-gray-800/50
                               hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                                    <td className="px-4 py-3">
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/30
                                        flex items-center justify-center shrink-0">
                          <span className="text-xs font-semibold text-brand-700 dark:text-brand-400">
                            {user.name?.[0]?.toUpperCase()}
                          </span>
                                            </div>
                                            <div>
                                                <p className="font-medium text-gray-900 dark:text-white">{user.name}</p>
                                                <p className="text-xs text-gray-400">{user.email}</p>
                                            </div>
                                        </div>
                                    </td>
                                    <td className="px-4 py-3">{statusBadge(user.status)}</td>
                                    <td className="px-4 py-3">
                      <span className={user.autoTrading ? 'badge-green' : 'badge-red'}>
                        {user.autoTrading ? 'ON' : 'OFF'}
                      </span>
                                    </td>
                                    <td className="px-4 py-3">
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {user.assignedStrategies.length === 0
                            ? 'None'
                            : user.assignedStrategies.join(', ')}
                      </span>
                                    </td>
                                    <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                                        {new Date(user.createdAt).toLocaleDateString('en-IN', {
                                            day: '2-digit', month: 'short', year: 'numeric',
                                        })}
                                    </td>
                                    <td className="px-4 py-3">
                                        <div className="flex items-center gap-1">
                                            {user.status === 'PENDING' && (
                                                <>
                                                    <button
                                                        onClick={() => approveMutation.mutate({
                                                            userId: user._id,
                                                            status: 'APPROVED'
                                                        })}
                                                        className="p-1.5 rounded-lg bg-emerald-100 dark:bg-emerald-900/20
                                         text-emerald-700 dark:text-emerald-400
                                         hover:bg-emerald-200 dark:hover:bg-emerald-800/40 transition-colors"
                                                        title="Approve"
                                                    >
                                                        <Check className="w-3.5 h-3.5"/>
                                                    </button>
                                                    <button
                                                        onClick={() => approveMutation.mutate({
                                                            userId: user._id,
                                                            status: 'REJECTED'
                                                        })}
                                                        className="p-1.5 rounded-lg bg-red-100 dark:bg-red-900/20
                                         text-red-700 dark:text-red-400
                                         hover:bg-red-200 dark:hover:bg-red-800/40 transition-colors"
                                                        title="Reject"
                                                    >
                                                        <X className="w-3.5 h-3.5"/>
                                                    </button>
                                                </>
                                            )}
                                            <button
                                                onClick={() => tradeMutation.mutate(user._id)}
                                                className="p-1.5 rounded-lg bg-gray-100 dark:bg-gray-800
                                     text-gray-600 dark:text-gray-400
                                     hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                                                title="Toggle trading"
                                            >
                                                {user.autoTrading
                                                    ? <ZapOff className="w-3.5 h-3.5"/>
                                                    : <Zap className="w-3.5 h-3.5"/>}
                                            </button>
                                            <a
                                                href={`/admin/strategies?userId=${user._id}`}
                                                className="p-1.5 rounded-lg bg-brand-50 dark:bg-brand-900/20
                                            text-brand-700 dark:text-brand-400 hover:bg-brand-100
                                            dark:hover:bg-brand-800/30 transition-colors text-xs font-medium px-2"
                                            >
                                                Strategies
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                            </tbody>
                        </table>
                        {users.length === 0 && (
                            <div className="p-8 text-center">
                                <p className="text-sm text-gray-400 dark:text-gray-500">No users found</p>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
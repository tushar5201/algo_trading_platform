import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Plus, BookOpen, Save } from 'lucide-react';
import { api } from '../../api/client';

export default function StrategyPage() {
    const [params]  = useSearchParams();
    const userId    = params.get('userId') ?? '';
    const qc        = useQueryClient();

    const [newStrat, setNewStrat] = useState({
        name: '', displayName: '', description: '', riskLevel: 'MEDIUM',
    });
    const [selected, setSelected] = useState<string[]>([]);
    const [targetUser, setTargetUser] = useState(userId);
    const [msg, setMsg] = useState('');

    const { data: strategies } = useQuery({
        queryKey: ['strategies'],
        queryFn:  () => api.get('/admin/strategies').then(r => r.data.data),
    });

    const { data: users } = useQuery({
        queryKey: ['admin-users-list'],
        queryFn:  () => api.get('/admin/users?status=APPROVED').then(r => r.data.data),
    });

    const createMutation = useMutation({
        mutationFn: () => api.post('/admin/strategies', newStrat),
        onSuccess:  () => {
            qc.invalidateQueries({ queryKey: ['strategies'] });
            setNewStrat({ name: '', displayName: '', description: '', riskLevel: 'MEDIUM' });
        },
    });

    const assignMutation = useMutation({
        mutationFn: () =>
            api.post(`/admin/users/${targetUser}/strategies`, { strategies: selected }),
        onSuccess:  () => setMsg('Strategies assigned successfully'),
        onError:    (e: any) => setMsg(e.response?.data?.message ?? 'Failed'),
    });

    const triggerMutation = useMutation({
        mutationFn: ({ userId, strategyName }: { userId: string; strategyName: string }) =>
            api.post(`/admin/users/${userId}/trigger-strategy`, { strategyName }),
        onSuccess: () => setMsg('Strategy job queued'),
    });

    const stratList: any[] = strategies ?? [];
    const userList:  any[] = users?.users ?? [];

    const riskColor = { LOW: 'badge-green', MEDIUM: 'badge-yellow', HIGH: 'badge-red' };

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">
            <div>
                <h1 className="text-xl font-bold text-gray-900 dark:text-white">Strategies</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                    Manage and assign trading strategies
                </p>
            </div>

            {msg && (
                <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-900/20
                        border border-emerald-200 dark:border-emerald-800">
                    <p className="text-sm text-emerald-700 dark:text-emerald-400">{msg}</p>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                {/* Strategy list */}
                <div className="card p-5">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white mb-4">
                        All strategies
                    </h2>
                    <div className="space-y-2">
                        {stratList.map((s: any) => (
                            <div key={s._id}
                                 className="flex items-center gap-3 p-3 rounded-xl
                           bg-gray-50 dark:bg-gray-800">
                                <BookOpen className="w-4 h-4 text-brand-500 shrink-0" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-gray-900 dark:text-white">
                                        {s.displayName}
                                    </p>
                                    <p className="text-xs text-gray-400">{s.name}</p>
                                </div>
                                <span className={(riskColor as any)[s.riskLevel] ?? 'badge-yellow'}>
                  {s.riskLevel}
                </span>
                            </div>
                        ))}
                        {stratList.length === 0 && (
                            <p className="text-sm text-gray-400 dark:text-gray-500">
                                No strategies yet
                            </p>
                        )}
                    </div>
                </div>

                {/* Create strategy */}
                <div className="card p-5 space-y-4">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                        Create strategy
                    </h2>
                    {[
                        { key: 'name',        label: 'Slug (unique)',    ph: 'oi_nifty_options' },
                        { key: 'displayName', label: 'Display name',    ph: 'OI Nifty Options' },
                        { key: 'description', label: 'Description',     ph: 'Strategy description' },
                    ].map(({ key, label, ph }) => (
                        <div key={key}>
                            <label className="label">{label}</label>
                            <input
                                className="input-base"
                                placeholder={ph}
                                value={(newStrat as any)[key]}
                                onChange={e => setNewStrat(s => ({ ...s, [key]: e.target.value }))}
                            />
                        </div>
                    ))}
                    <div>
                        <label className="label">Risk level</label>
                        <select
                            className="input-base"
                            value={newStrat.riskLevel}
                            onChange={e => setNewStrat(s => ({ ...s, riskLevel: e.target.value }))}
                        >
                            {['LOW', 'MEDIUM', 'HIGH'].map(r => (
                                <option key={r} value={r}>{r}</option>
                            ))}
                        </select>
                    </div>
                    <button
                        className="btn-primary w-full"
                        disabled={createMutation.isPending}
                        onClick={() => createMutation.mutate()}
                    >
                        <Plus className="w-4 h-4" />
                        {createMutation.isPending ? 'Creating…' : 'Create strategy'}
                    </button>
                </div>
            </div>

            {/* Assign to user */}
            <div className="card p-5 space-y-4">
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                    Assign strategies to user
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                        <label className="label">Select user</label>
                        <select
                            className="input-base"
                            value={targetUser}
                            onChange={e => setTargetUser(e.target.value)}
                        >
                            <option value="">— Choose user —</option>
                            {userList.map((u: any) => (
                                <option key={u._id} value={u._id}>
                                    {u.name} ({u.email})
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="label">Select strategies</label>
                        <div className="space-y-1.5 mt-1">
                            {stratList.map((s: any) => (
                                <label key={s._id} className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="rounded border-gray-300 dark:border-gray-600
                               text-brand-600 focus:ring-brand-500"
                                        checked={selected.includes(s.name)}
                                        onChange={e =>
                                            setSelected(prev =>
                                                e.target.checked
                                                    ? [...prev, s.name]
                                                    : prev.filter(n => n !== s.name)
                                            )
                                        }
                                    />
                                    <span className="text-sm text-gray-700 dark:text-gray-300">
                    {s.displayName}
                  </span>
                                </label>
                            ))}
                        </div>
                    </div>
                </div>
                <button
                    className="btn-primary"
                    disabled={!targetUser || selected.length === 0 || assignMutation.isPending}
                    onClick={() => assignMutation.mutate()}
                >
                    <Save className="w-4 h-4" />
                    {assignMutation.isPending ? 'Saving…' : 'Assign strategies'}
                </button>
            </div>

            {/* Manual trigger */}
            <div className="card p-5 space-y-4">
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                    Manual strategy trigger
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                    Trigger a strategy job immediately without waiting for the scheduler.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    {userList.map((u: any) =>
                        (u.assignedStrategies ?? []).map((sName: string) => (
                            <div key={`${u._id}-${sName}`}
                                 className="flex items-center justify-between p-3 rounded-xl
                           bg-gray-50 dark:bg-gray-800">
                                <div className="min-w-0">
                                    <p className="text-xs font-medium text-gray-900 dark:text-white truncate">
                                        {u.name}
                                    </p>
                                    <p className="text-xs text-gray-400 truncate">{sName}</p>
                                </div>
                                <button
                                    onClick={() => triggerMutation.mutate({ userId: u._id, strategyName: sName })}
                                    disabled={triggerMutation.isPending}
                                    className="ml-2 text-xs btn-primary py-1 px-2"
                                >
                                    Run
                                </button>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}
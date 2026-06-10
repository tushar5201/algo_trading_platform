import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Filter, TrendingUp } from 'lucide-react';
import { api } from '../../api/client';
import clsx from 'clsx';
import type {Trade} from "../../types";

const STATUS_OPTIONS = ['ALL', 'EXECUTED', 'PENDING', 'FAILED', 'CLOSED'];

export default function TradesPage() {
    const [status, setStatus] = useState('ALL');
    const [page,   setPage]   = useState(1);

    const { data, isLoading } = useQuery({
        queryKey: ['trades', status, page],
        queryFn: () =>
            api.get(`/trades?limit=20&page=${page}${status !== 'ALL' ? `&status=${status}` : ''}`)
                .then(r => r.data.data),
    });

    const { data: pnlData } = useQuery({
        queryKey: ['pnl'],
        queryFn:  () => api.get('/trades/pnl').then(r => r.data.data),
    });

    const trades: Trade[] = data?.trades ?? [];
    const total            = data?.total ?? 0;
    const totalPages       = data?.totalPages ?? 1;
    const summary          = pnlData?.summary;

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">

            {/* Header */}
            <div>
                <h1 className="text-xl font-bold text-gray-900 dark:text-white">Trade history</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                    {total} total trades
                </p>
            </div>

            {/* Summary row */}
            {summary && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    {[
                        { label: 'Total P&L',  value: `${summary.totalPnl >= 0 ? '+' : ''}₹${summary.totalPnl.toFixed(2)}`, positive: summary.totalPnl >= 0 },
                        { label: 'Win rate',   value: summary.winRate, positive: parseFloat(summary.winRate) >= 50 },
                        { label: 'Best trade', value: `₹${summary?.bestTrade?.toFixed(2) ?? 0}`,  positive: true  },
                        { label: 'Worst trade',value: `₹${summary?.worstTrade?.toFixed(2) ?? 0}`, positive: false },
                    ].map(({ label, value, positive }) => (
                        <div key={label} className="card p-4">
                            <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</p>
                            <p className={clsx(
                                'text-lg font-bold',
                                positive ? 'text-emerald-600 dark:text-emerald-400'
                                    : 'text-red-600 dark:text-red-400'
                            )}>
                                {value}
                            </p>
                        </div>
                    ))}
                </div>
            )}

            {/* Filters */}
            <div className="flex items-center gap-2 flex-wrap">
                <Filter className="w-4 h-4 text-gray-400" />
                {STATUS_OPTIONS.map(s => (
                    <button
                        key={s}
                        onClick={() => { setStatus(s); setPage(1); }}
                        className={clsx(
                            'px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
                            status === s
                                ? 'bg-brand-600 text-white'
                                : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'
                        )}
                    >
                        {s}
                    </button>
                ))}
            </div>

            {/* Table */}
            <div className="card overflow-hidden">
                {isLoading ? (
                    <div className="p-8 text-center">
                        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent
                            rounded-full animate-spin mx-auto" />
                    </div>
                ) : trades.length === 0 ? (
                    <div className="p-12 text-center">
                        <TrendingUp className="w-10 h-10 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
                        <p className="text-gray-400 dark:text-gray-500 text-sm">No trades found</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-sm">
                            <thead className="border-b border-gray-100 dark:border-gray-800">
                            <tr>
                                {['Symbol', 'Strategy', 'Side', 'Qty', 'Price', 'Status', 'P&L', 'Date'].map(h => (
                                    <th key={h}
                                        className="px-4 py-3 text-xs font-medium text-gray-500
                                 dark:text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                            </thead>
                            <tbody>
                            {trades.map(trade => (
                                <tr key={trade._id}
                                    className="border-b border-gray-50 dark:border-gray-800/50
                               hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors">
                                    <td className="px-4 py-3 font-medium text-gray-900 dark:text-white whitespace-nowrap">
                                        {trade.symbol}
                                    </td>
                                    <td className="px-4 py-3 text-gray-500 dark:text-gray-400 whitespace-nowrap">
                                        {trade.strategyName.replace(/_/g, ' ')}
                                    </td>
                                    <td className="px-4 py-3">
                      <span className={trade.side === 'BUY' ? 'badge-green' : 'badge-red'}>
                        {trade.side}
                      </span>
                                    </td>
                                    <td className="px-4 py-3 text-gray-600 dark:text-gray-400">{trade.qty}</td>
                                    <td className="px-4 py-3 text-gray-600 dark:text-gray-400">
                                        ₹{trade.price.toFixed(2)}
                                    </td>
                                    <td className="px-4 py-3">
                      <span className={{
                          EXECUTED:  'badge-green',
                          PENDING:   'badge-yellow',
                          FAILED:    'badge-red',
                          CANCELLED: 'badge-red',
                          CLOSED:    'badge-blue',
                      }[trade.status] ?? 'badge-yellow'}>
                        {trade.status}
                      </span>
                                    </td>
                                    <td className="px-4 py-3 font-medium whitespace-nowrap">
                                        {trade.pnl !== null ? (
                                            <span className={trade.pnl >= 0
                                                ? 'text-emerald-600 dark:text-emerald-400'
                                                : 'text-red-600 dark:text-red-400'}>
                          {trade.pnl >= 0 ? '+' : ''}₹{trade.pnl.toFixed(2)}
                        </span>
                                        ) : <span className="text-gray-400">—</span>}
                                    </td>
                                    <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                                        {new Date(trade.createdAt).toLocaleString('en-IN', {
                                            day: '2-digit', month: 'short',
                                            hour: '2-digit', minute: '2-digit',
                                        })}
                                    </td>
                                </tr>
                            ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* Pagination */}
                {totalPages > 1 && (
                    <div className="flex items-center justify-between px-4 py-3
                          border-t border-gray-100 dark:border-gray-800">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            Page {page} of {totalPages}
                        </p>
                        <div className="flex gap-2">
                            <button
                                disabled={page === 1}
                                onClick={() => setPage(p => p - 1)}
                                className="btn-secondary text-xs py-1.5 px-3 disabled:opacity-40"
                            >
                                Previous
                            </button>
                            <button
                                disabled={page === totalPages}
                                onClick={() => setPage(p => p + 1)}
                                className="btn-secondary text-xs py-1.5 px-3 disabled:opacity-40"
                            >
                                Next
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react';
import { api } from '../api/client';
import type { AiReview, Trade } from '../types';

// Statuses for which the server accepts a review request (an open trade has no outcome to review yet).
const REVIEWABLE = ['CLOSED', 'FAILED', 'NEEDS_REVIEW', 'CANCELED'];
const POLL_MS = 5000;

function errorMessage(err: unknown): string {
    const e = err as { response?: { status?: number; data?: { message?: string } } };
    if (e.response?.status === 503) return 'AI review is not enabled on this server.';
    if (e.response?.status === 429) return 'Too many review requests. Try again later.';
    return e.response?.data?.message ?? 'Could not request a review.';
}

export default function AiReviewCard({ trade }: { trade: Trade }) {
    const queryClient = useQueryClient();

    // Detail query: starts from the list row and polls only while the review is being generated.
    const { data: live } = useQuery({
        queryKey: ['trade', trade._id],
        queryFn: () => api.get(`/trades/${trade._id}`).then(r => r.data.data as Trade),
        initialData: trade,
        refetchInterval: (q) => (q.state.data?.aiReview?.status === 'PENDING' ? POLL_MS : false),
    });
    const review: AiReview | null | undefined = live?.aiReview ?? trade.aiReview;

    const generate = useMutation({
        mutationFn: () => api.post(`/trades/${trade._id}/ai-review`).then(r => r.data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['trade', trade._id] });
            queryClient.invalidateQueries({ queryKey: ['trades'] });
        },
    });

    const canRequest = REVIEWABLE.includes(trade.status) && (!review || review.status === 'FAILED' || review.status === 'SKIPPED');
    const pending = review?.status === 'PENDING' || generate.isPending;

    return (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-800/40 p-4 space-y-3 text-sm">
            <div className="flex items-center gap-2 font-semibold text-gray-900 dark:text-white">
                <Sparkles className="w-4 h-4 text-brand-500" /> AI review
                {review?.model && <span className="ml-auto text-xs font-normal text-gray-400">{review.model}</span>}
            </div>

            {pending && (
                <p className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
                    <Loader2 className="w-4 h-4 animate-spin" /> Generating review… this can take a minute on Groq's free tier.
                </p>
            )}

            {!pending && review?.status === 'DONE' && (
                <div className="space-y-3 text-gray-700 dark:text-gray-300">
                    <p>{review.summary}</p>
                    {!!review.bullets?.length && (
                        <ul className="list-disc pl-5 space-y-1">{review.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
                    )}
                    {(review.whatWorked || review.whatDidnt) && (
                        <div className="grid sm:grid-cols-2 gap-3">
                            {review.whatWorked && (
                                <div><p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">What worked</p><p>{review.whatWorked}</p></div>
                            )}
                            {review.whatDidnt && (
                                <div><p className="text-xs font-medium text-red-600 dark:text-red-400">What didn&apos;t</p><p>{review.whatDidnt}</p></div>
                            )}
                        </div>
                    )}
                    {!!review.riskNotes?.length && (
                        <div>
                            <p className="text-xs font-medium text-amber-600 dark:text-amber-400">Risk notes</p>
                            <ul className="list-disc pl-5 space-y-1">{review.riskNotes.map((b, i) => <li key={i}>{b}</li>)}</ul>
                        </div>
                    )}
                    {review.confidence != null && (
                        <p className="text-xs text-gray-400">Model confidence in its own analysis: {Math.round(review.confidence * 100)}%</p>
                    )}
                </div>
            )}

            {!pending && (review?.status === 'FAILED' || review?.status === 'SKIPPED') && (
                <p className="flex items-start gap-2 text-amber-600 dark:text-amber-400">
                    <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                    {review.status === 'SKIPPED' ? 'Review skipped' : 'Review failed'}
                    {review.error ? `: ${review.error}` : '.'}
                </p>
            )}

            {!pending && !review && !canRequest && (
                <p className="text-gray-500 dark:text-gray-400">A review can be generated once the trade has closed.</p>
            )}

            {canRequest && !pending && (
                <button className="btn-secondary text-xs py-1.5 px-3" onClick={() => generate.mutate()}>
                    {review ? 'Try again' : 'Generate review'}
                </button>
            )}
            {generate.isError && <p className="text-xs text-red-600 dark:text-red-400">{errorMessage(generate.error)}</p>}

            <p className="text-xs text-gray-400 dark:text-gray-500">
                AI-generated explanation based on the trade facts only. It can be wrong and is not financial advice.
            </p>
        </div>
    );
}

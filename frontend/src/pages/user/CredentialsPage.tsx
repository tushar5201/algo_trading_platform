import {useState} from 'react';
import {useQuery, useMutation, useQueryClient} from '@tanstack/react-query';
import {Key, ShieldCheck, CheckCircle, AlertCircle, ChevronRight} from 'lucide-react';
import {api} from '../../api/client';

type Step = 'credentials' | 'totp' | 'mpin' | 'done';

export default function CredentialsPage() {
    const [step, setStep] = useState<Step>('credentials');
    const [msg, setMsg] = useState('');
    const [err, setErr] = useState('');
    const qc = useQueryClient();

    const {data: sessionData} = useQuery({
        queryKey: ['kotak-session'],
        queryFn: () => api.get('/user/kotak/auth-status').then(r => r.data.data),
        refetchInterval: 10_000,
    });

    // ── Credentials form ──────────────────────────────────────────────────────
    const [creds, setCreds] = useState({
        clientId: '', apiKey: '', apiSecret: '', mobileNumber: '', password: '',
    });

    const saveMutation = useMutation({
        mutationFn: () => api.post('/user/credentials', creds),
        onSuccess: () => {
            setMsg('Credentials saved');
            setStep('totp');
        },
        onError: (e: any) => setErr(e.response?.data?.message ?? 'Failed'),
    });

    // ── TOTP form ─────────────────────────────────────────────────────────────
    const [totpForm, setTotpForm] = useState({
        accessToken: '', mobileNumber: '', ucc: '', totp: '',
    });

    const totpMutation = useMutation({
        mutationFn: () => api.post('/user/kotak/totp', totpForm),
        onSuccess: () => {
            setMsg('TOTP accepted. Now enter your MPIN.');
            setStep('mpin');
        },
        onError: (e: any) => setErr(e.response?.data?.message ?? 'TOTP failed'),
    });

    // ── MPIN form ─────────────────────────────────────────────────────────────
    const [mpin, setMpin] = useState('');

    const mpinMutation = useMutation({
        mutationFn: () => api.post('/user/kotak/mpin', {mpin}),
        onSuccess: () => {
            setMsg('Session activated!');
            setStep('done');
            qc.invalidateQueries({queryKey: ['kotak-session']});
        },
        onError: (e: any) => setErr(e.response?.data?.message ?? 'MPIN failed'),
    });

    const clear = () => setErr('');

    // ── UI ────────────────────────────────────────────────────────────────────

    const steps = ['credentials', 'totp', 'mpin', 'done'];
    const stepIdx = steps.indexOf(step);

    return (
        <div className="p-6 max-w-2xl mx-auto space-y-6">

            {/* Header */}
            <div>
                <h1 className="text-xl font-bold text-gray-900 dark:text-white">
                    Broker credentials
                </h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                    Connect your Kotak Neo account to enable auto trading
                </p>
            </div>

            {/* Session status */}
            <div className={`flex items-center gap-3 p-4 rounded-xl border ${
                sessionData?.hasActiveSession
                    ? 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800'
                    : 'bg-gray-50 dark:bg-gray-800/50 border-gray-200 dark:border-gray-700'
            }`}>
                {sessionData?.hasActiveSession
                    ? <ShieldCheck className="w-5 h-5 text-emerald-600 dark:text-emerald-400"/>
                    : <Key className="w-5 h-5 text-gray-400"/>}
                <div>
                    <p className={`text-sm font-medium ${
                        sessionData?.hasActiveSession
                            ? 'text-emerald-800 dark:text-emerald-300'
                            : 'text-gray-700 dark:text-gray-300'
                    }`}>
                        {sessionData?.hasActiveSession
                            ? `Session active — expires in ${sessionData.expiresInMinutes} minutes`
                            : 'No active broker session'}
                    </p>
                    {sessionData?.lastError && (
                        <p className="text-xs text-red-500 dark:text-red-400 mt-0.5">
                            Last error: {sessionData.lastError}
                        </p>
                    )}
                </div>
                {sessionData?.hasActiveSession && (
                    <button
                        onClick={() => {
                            setStep('totp');
                            setMsg('');
                            setErr('');
                        }}
                        className="ml-auto text-xs text-emerald-700 dark:text-emerald-400
                       hover:underline font-medium"
                    >
                        Renew
                    </button>
                )}
            </div>

            {/* Step indicator */}
            <div className="flex items-center gap-2">
                {['Save credentials', 'Submit TOTP', 'Submit MPIN', 'Done'].map((label, i) => (
                    <div key={i} className="flex items-center gap-2 flex-1">
                        <div className={`w-6 h-6 rounded-full flex items-center justify-center
                            text-xs font-semibold shrink-0 ${
                            i < stepIdx ? 'bg-emerald-500 text-white' :
                                i === stepIdx ? 'bg-brand-600 text-white' :
                                    'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                        }`}>
                            {i < stepIdx ? '✓' : i + 1}
                        </div>
                        <span className={`text-xs hidden sm:block ${
                            i === stepIdx
                                ? 'text-gray-900 dark:text-white font-medium'
                                : 'text-gray-400 dark:text-gray-500'
                        }`}>{label}</span>
                        {i < 3 && (
                            <ChevronRight className="w-3 h-3 text-gray-300 dark:text-gray-600 shrink-0"/>
                        )}
                    </div>
                ))}
            </div>

            {/* Messages */}
            {msg && (
                <div className="flex items-center gap-2 p-3 rounded-xl
                        bg-emerald-50 dark:bg-emerald-900/20
                        border border-emerald-200 dark:border-emerald-800">
                    <CheckCircle className="w-4 h-4 text-emerald-600 dark:text-emerald-400"/>
                    <p className="text-sm text-emerald-700 dark:text-emerald-400">{msg}</p>
                </div>
            )}
            {err && (
                <div className="flex items-center gap-2 p-3 rounded-xl
                        bg-red-50 dark:bg-red-900/20
                        border border-red-200 dark:border-red-800">
                    <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400"/>
                    <p className="text-sm text-red-700 dark:text-red-400">{err}</p>
                    <button onClick={clear} className="ml-auto text-red-400 hover:text-red-600 text-xs">
                        Dismiss
                    </button>
                </div>
            )}

            {/* Step: Credentials */}
            {step === 'credentials' && (
                <div className="card p-6 space-y-4">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                        Kotak Neo credentials
                    </h2>
                    {[
                        {key: 'clientId', label: 'Client ID / UCC', type: 'text', ph: 'AB1234'},
                        {key: 'apiKey', label: 'API access token', type: 'text', ph: 'From NEO dashboard'},
                        {key: 'apiSecret', label: 'API secret (optional)', type: 'password', ph: '••••••'},
                        {key: 'mobileNumber', label: 'Mobile (+91XXXXXXXXXX)', type: 'tel', ph: '+919999999999'},
                        {key: 'password', label: 'Kotak password', type: 'password', ph: '••••••••'},
                    ].map(({key, label, type, ph}) => (
                        <div key={key}>
                            <label className="label">{label}</label>
                            <input
                                type={type}
                                className="input-base"
                                placeholder={ph}
                                value={(creds as any)[key]}
                                onChange={e => setCreds(c => ({...c, [key]: e.target.value}))}
                            />
                        </div>
                    ))}
                    <button
                        className="btn-primary w-full"
                        disabled={saveMutation.isPending}
                        onClick={() => {
                            clear();
                            saveMutation.mutate();
                        }}
                    >
                        {saveMutation.isPending ? 'Saving…' : 'Save credentials'}
                    </button>
                </div>
            )}

            {/* Step: TOTP */}
            {step === 'totp' && (
                <div className="card p-6 space-y-4">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                        Step 1 — TOTP login
                    </h2>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                        Open Google Authenticator and enter the 6-digit code for your Kotak account.
                    </p>
                    {[
                        {
                            key: 'accessToken',
                            label: 'NEO dashboard access token',
                            type: 'text',
                            ph: 'Your access token'
                        },
                        {key: 'mobileNumber', label: 'Mobile (+91XXXXXXXXXX)', type: 'tel', ph: '+919999999999'},
                        {key: 'ucc', label: 'Client code / UCC', type: 'text', ph: 'AB1234'},
                        {key: 'totp', label: 'TOTP (6 digits)', type: 'text', ph: '847291'},
                    ].map(({key, label, type, ph}) => (
                        <div key={key}>
                            <label className="label">{label}</label>
                            <input
                                type={type}
                                className="input-base"
                                placeholder={ph}
                                maxLength={key === 'totp' ? 6 : undefined}
                                value={(totpForm as any)[key]}
                                onChange={e => setTotpForm(f => ({...f, [key]: e.target.value}))}
                            />
                        </div>
                    ))}
                    <div className="flex gap-3">
                        <button className="btn-secondary" onClick={() => setStep('credentials')}>
                            Back
                        </button>
                        <button
                            className="btn-primary flex-1"
                            disabled={totpMutation.isPending}
                            onClick={() => {
                                clear();
                                totpMutation.mutate();
                            }}
                        >
                            {totpMutation.isPending ? 'Submitting…' : 'Submit TOTP'}
                        </button>
                    </div>
                </div>
            )}

            {/* Step: MPIN */}
            {step === 'mpin' && (
                <div className="card p-6 space-y-4">
                    <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                        Step 2 — MPIN validation
                    </h2>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                        Enter your 6-digit Kotak MPIN to activate the trading session.
                    </p>
                    <div>
                        <label className="label">MPIN (6 digits)</label>
                        <input
                            type="password"
                            className="input-base tracking-widest text-lg"
                            placeholder="••••••"
                            maxLength={6}
                            value={mpin}
                            onChange={e => setMpin(e.target.value)}
                        />
                    </div>
                    <div className="flex gap-3">
                        <button className="btn-secondary" onClick={() => setStep('totp')}>Back</button>
                        <button
                            className="btn-primary flex-1"
                            disabled={mpinMutation.isPending || mpin.length !== 6}
                            onClick={() => {
                                clear();
                                mpinMutation.mutate();
                            }}
                        >
                            {mpinMutation.isPending ? 'Activating…' : 'Activate session'}
                        </button>
                    </div>
                </div>
            )}

            {/* Done */}
            {step === 'done' && (
                <div className="card p-8 text-center">
                    <CheckCircle className="w-12 h-12 text-emerald-500 mx-auto mb-4"/>
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
                        Session active!
                    </h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                        Your Kotak Neo trading session is now active. You can enable auto trading from the dashboard.
                    </p>
                    <a href="/" className="btn-primary">Go to dashboard</a>
                </div>
            )}
        </div>
    );
}
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, CheckCircle, Sun, Moon } from 'lucide-react';
import { api } from '../../api/client';
import { useThemeStore } from '../../store/themeStore';

export default function SignupPage() {
    const [form, setForm] = useState({ name: '', email: '', password: '' });
    const [done, setDone] = useState(false);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const { dark, toggle } = useThemeStore();

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await api.post('/auth/signup', form);
            setDone(true);
        } catch (err: any) {
            setError(err.response?.data?.message ?? 'Signup failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex flex-col bg-gray-50 dark:bg-gray-950 transition-colors">
            <div className="flex justify-end p-4">
                <button onClick={toggle}
                        className="p-2 rounded-xl text-gray-500 hover:bg-gray-100
                     dark:hover:bg-gray-800 transition-all">
                    {dark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                </button>
            </div>

            <div className="flex-1 flex items-center justify-center px-4 pb-16">
                <div className="w-full max-w-sm">
                    <div className="text-center mb-8">
                        <div className="inline-flex items-center justify-center w-14 h-14
                            rounded-2xl bg-brand-600 shadow-lg mb-4">
                            <Activity className="w-7 h-7 text-white" />
                        </div>
                        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
                            Create account
                        </h1>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                            Start your Trade-net journey
                        </p>
                    </div>

                    {done ? (
                        <div className="card p-8 text-center shadow-sm">
                            <CheckCircle className="w-12 h-12 text-emerald-500 mx-auto mb-4" />
                            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
                                Account created!
                            </h2>
                            <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                                Your account is pending admin approval. You'll be able to
                                log in once approved.
                            </p>
                            <Link to="/login" className="btn-primary">
                                Go to login
                            </Link>
                        </div>
                    ) : (
                        <div className="card p-6 shadow-sm">
                            <form onSubmit={handleSubmit} className="space-y-4">
                                {error && (
                                    <div className="p-3 rounded-xl bg-red-50 dark:bg-red-900/20
                                  border border-red-200 dark:border-red-800">
                                        <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
                                    </div>
                                )}

                                {[
                                    { key: 'name',     label: 'Full name',  type: 'text',     placeholder: 'John Doe' },
                                    { key: 'email',    label: 'Email',       type: 'email',    placeholder: 'you@example.com' },
                                    { key: 'password', label: 'Password',    type: 'password', placeholder: '8+ characters' },
                                ].map(({ key, label, type, placeholder }) => (
                                    <div key={key}>
                                        <label className="label">{label}</label>
                                        <input
                                            type={type}
                                            className="input-base"
                                            placeholder={placeholder}
                                            value={(form as any)[key]}
                                            onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                                            required
                                        />
                                    </div>
                                ))}

                                <button type="submit" disabled={loading} className="btn-primary w-full">
                                    {loading ? 'Creating account…' : 'Create account'}
                                </button>
                            </form>
                        </div>
                    )}

                    {!done && (
                        <p className="text-center text-sm text-gray-500 dark:text-gray-400 mt-4">
                            Already have an account?{' '}
                            <Link to="/login"
                                  className="text-brand-600 dark:text-brand-400 font-medium hover:underline">
                                Sign in
                            </Link>
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Activity, Eye, EyeOff } from 'lucide-react';
import { useAuthStore } from '../../store/authStore';
import { useThemeStore } from '../../store/themeStore';
import { api } from '../../api/client';
import { Sun, Moon } from 'lucide-react';

export default function LoginPage() {
    const [email,    setEmail]    = useState('');
    const [password, setPassword] = useState('');
    const [show,     setShow]     = useState(false);
    const [error,    setError]    = useState('');
    const [loading,  setLoading]  = useState(false);

    const { setAuth }    = useAuthStore();
    const { dark, toggle } = useThemeStore();
    const navigate         = useNavigate();

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const { data } = await api.post('/auth/login', { email, password });
            setAuth(data.data.token, data.data.user);
            navigate(data.data.user.role === 'admin' ? '/admin' : '/');
        } catch (err: any) {
            setError(err.response?.data?.message ?? 'Login failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex flex-col bg-gray-50 dark:bg-gray-950
                    transition-colors duration-300">

            {/* Theme toggle */}
            <div className="flex justify-end p-4">
                <button
                    onClick={toggle}
                    className="p-2 rounded-xl text-gray-500 hover:text-gray-700
                     dark:hover:text-gray-300 hover:bg-gray-100
                     dark:hover:bg-gray-800 transition-all"
                >
                    {dark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                </button>
            </div>

            <div className="flex-1 flex items-center justify-center px-4 pb-16">
                <div className="w-full max-w-sm">

                    {/* Header */}
                    <div className="text-center mb-8">
                        <div className="inline-flex items-center justify-center w-14 h-14
                            rounded-2xl bg-brand-600 shadow-lg mb-4">
                            <Activity className="w-7 h-7 text-white" />
                        </div>
                        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
                            Welcome back
                        </h1>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                            Sign in to your Trade-net account
                        </p>
                    </div>

                    {/* Form */}
                    <div className="card p-6 shadow-sm">
                        <form onSubmit={handleSubmit} className="space-y-4">
                            {error && (
                                <div className="p-3 rounded-xl bg-red-50 dark:bg-red-900/20
                                border border-red-200 dark:border-red-800">
                                    <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
                                </div>
                            )}

                            <div>
                                <label className="label">Email address</label>
                                <input
                                    type="email"
                                    className="input-base"
                                    placeholder="you@example.com"
                                    value={email}
                                    onChange={e => setEmail(e.target.value)}
                                    required
                                />
                            </div>

                            <div>
                                <label className="label">Password</label>
                                <div className="relative">
                                    <input
                                        type={show ? 'text' : 'password'}
                                        className="input-base pr-10"
                                        placeholder="••••••••"
                                        value={password}
                                        onChange={e => setPassword(e.target.value)}
                                        required
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShow(!show)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2
                               text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                                    >
                                        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                            </div>

                            <button type="submit" disabled={loading} className="btn-primary w-full">
                                {loading ? 'Signing in…' : 'Sign in'}
                            </button>
                        </form>
                    </div>

                    <p className="text-center text-sm text-gray-500 dark:text-gray-400 mt-4">
                        No account?{' '}
                        <Link to="/signup"
                              className="text-brand-600 dark:text-brand-400 font-medium hover:underline">
                            Sign up
                        </Link>
                    </p>
                </div>
            </div>
        </div>
    );
}
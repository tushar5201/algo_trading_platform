import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import {
    LayoutDashboard, TrendingUp, Key, Users,
    BookOpen, LogOut, Sun, Moon, Activity,
} from 'lucide-react';
import { useAuthStore } from '../store/authStore';
import { useThemeStore } from '../store/themeStore';
import clsx from 'clsx';

export default function Layout() {
    const { user, clearAuth } = useAuthStore();
    const { dark, toggle }    = useThemeStore();
    const navigate            = useNavigate();

    const logout = () => { clearAuth(); navigate('/login'); };

    const userLinks = [
        { to: '/',            label: 'Dashboard',   icon: LayoutDashboard },
        { to: '/trades',      label: 'Trades',       icon: TrendingUp },
        { to: '/credentials', label: 'Credentials',  icon: Key },
    ];

    const adminLinks = [
        { to: '/admin',            label: 'Dashboard',   icon: LayoutDashboard },
        { to: '/admin/users',      label: 'Users',        icon: Users },
        { to: '/admin/strategies', label: 'Strategies',   icon: BookOpen },
    ];

    const links = user?.role === 'admin' ? adminLinks : userLinks;

    return (
        <div className="flex h-screen bg-gray-50 dark:bg-gray-950 overflow-hidden">

            {/* Sidebar */}
            <aside className="w-64 flex flex-col border-r border-gray-200 dark:border-gray-800
                        bg-white dark:bg-gray-900 shrink-0">

                {/* Logo */}
                <div className="h-16 flex items-center gap-3 px-6 border-b
                        border-gray-200 dark:border-gray-800">
                    <div className="w-8 h-8 rounded-xl bg-brand-600 flex items-center
                          justify-center shadow-sm">
                        <Activity className="w-4 h-4 text-white" />
                    </div>
                    <span className="font-semibold text-gray-900 dark:text-white tracking-tight">
            Trade-net
          </span>
                </div>

                {/* Navigation */}
                <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
                    {links.map(({ to, label, icon: Icon }) => (
                        <NavLink
                            key={to}
                            to={to}
                            end
                            className={({ isActive }) =>
                                clsx(
                                    'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all',
                                    isActive
                                        ? 'bg-brand-50 dark:bg-brand-900/20 text-brand-700 dark:text-brand-400'
                                        : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-200'
                                )
                            }
                        >
                            <Icon className="w-4 h-4 shrink-0" />
                            {label}
                        </NavLink>
                    ))}
                </nav>

                {/* Bottom */}
                <div className="px-3 py-4 border-t border-gray-200 dark:border-gray-800 space-y-1">
                    {/* Theme toggle */}
                    <button
                        onClick={toggle}
                        className="flex w-full items-center gap-3 px-3 py-2.5 rounded-xl text-sm
                       font-medium text-gray-600 dark:text-gray-400
                       hover:bg-gray-100 dark:hover:bg-gray-800 transition-all"
                    >
                        {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
                        {dark ? 'Light mode' : 'Dark mode'}
                    </button>

                    {/* User info */}
                    <div className="flex items-center gap-3 px-3 py-2.5">
                        <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/30
                            flex items-center justify-center shrink-0">
              <span className="text-xs font-semibold text-brand-700 dark:text-brand-400">
                {user?.name?.[0]?.toUpperCase()}
              </span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                                {user?.name}
                            </p>
                            <p className="text-xs text-gray-500 dark:text-gray-500 truncate">
                                {user?.role}
                            </p>
                        </div>
                        <button
                            onClick={logout}
                            className="text-gray-400 hover:text-red-500 transition-colors"
                        >
                            <LogOut className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <main className="flex-1 overflow-y-auto">
                <Outlet />
            </main>
        </div>
    );
}
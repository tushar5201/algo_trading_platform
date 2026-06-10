import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ThemeState {
    dark:   boolean;
    toggle: () => void;
}

export const useThemeStore = create<ThemeState>()(
    persist(
        (set, get) => ({
            dark: false,
            toggle: () => {
                const next = !get().dark;
                set({ dark: next });
                // v4 reads .dark on <html>
                document.documentElement.classList.toggle('dark', next);
            },
        }),
        { name: 'theme-storage' }
    )
);

// Apply saved theme immediately on load
const raw = localStorage.getItem('theme-storage');
if (raw) {
    try {
        const { state } = JSON.parse(raw);
        if (state?.dark) {
            document.documentElement.classList.add('dark');
        }
    } catch {}
}
import axios from "axios";

export const api = axios.create({
    baseURL: '/api',
    headers: {'Content-Type': 'application/json'},
});

// Attack JWT on every request
api.interceptors.request.use((config) => {
    const raw = localStorage.getItem('auth-storage');
    if(raw) {
        try {
            const {state} = JSON.parse(raw);
            if(state?.token) {
                config.headers.Authorization = `Bearer ${state.token}`;
            }
        } catch {}
    }
    return config;
});

// Auto-logout on 401
api.interceptors.response.use(
    (res) => res,
    (err) => {
        const url: string = err.config?.url ?? '';
        const isLoginAttempt = url.includes('/auth/login') || url.includes('/auth/signup');
        if(err.response?.status === 401 && !isLoginAttempt) {
            localStorage.removeItem('auth-storage');
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
        }
        return Promise.reject(err);
    }
);


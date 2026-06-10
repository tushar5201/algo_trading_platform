import type {AuthState} from "../types";
import {create} from "zustand";
import {persist} from "zustand/middleware";

export const useAuthStore = create<AuthState>()(
    persist(
        (set) => ({
            token: null,
            user: null,
            setAuth: (token, user) => set({token, user}),
            clearAuth: () => set({token: null, user: null}),
        }),
        {name: "auth-storage"}
    )
)
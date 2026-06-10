import {useAuthStore} from "../store/authStore.ts";
import {Navigate, Outlet} from "react-router-dom";

interface Props {role: 'admin' | 'user';}

export default function ProtectedRoute({role}: Props) {
    const {user} = useAuthStore();
    if(!user) return <Navigate to="/login" replace />;
    if(user.role !== role)
        return <Navigate to={user.role === 'admin' ? '/admin' : '/'} replace />;
    return <Outlet />
}
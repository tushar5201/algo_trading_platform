import './App.css'
import {Navigate, Route, Routes} from "react-router-dom";
import {useAuthStore} from "./store/authStore.ts";
import LoginPage from "./pages/auth/LoginPage.tsx";
import SignupPage from "./pages/auth/SignupPage.tsx";
import ProtectedRoute from "./components/ProtectedRoute.tsx";
import Layout from "./components/Layout.tsx";
import DashboardPage from "./pages/user/DashboardPage.tsx";
import CredentialsPage from "./pages/user/CredentialsPage.tsx";
import TradesPage from "./pages/user/TradesPage.tsx";
import AdminDashboard from "./pages/admin/AdminDashboard.tsx";
import UsersPage from "./pages/admin/UserPage.tsx";
import StrategyPage from "./pages/admin/StrategyPage.tsx";

function App() {
    const {user} = useAuthStore();
    return (
        <Routes>
            {/*Public*/}
            <Route path="/login" element={!user ? <LoginPage /> : <Navigate to="/"/>}/>
            <Route path="/signup" element={!user ? <SignupPage /> : <Navigate to="/"/>}/>

            {/*User routes*/}
            <Route element={<ProtectedRoute role="user"/>}>
                <Route element={<Layout />}>
                    <Route path="/" element={<DashboardPage />}/>
                    <Route path="/credentials" element={<CredentialsPage />}/>
                    <Route path="/trades" element={<TradesPage />}/>
                </Route>
            </Route>

            {/*Admin Routes*/}
            <Route element={<ProtectedRoute role="admin" />}>
                <Route element={<Layout />}>
                    <Route path="/admin" element={<AdminDashboard />} />
                    <Route path="/admin/users" element={<UsersPage />} />
                    <Route path="/admin/strategies" element={<StrategyPage />} />
                </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" /> } />
        </Routes>
    )
}

export default App

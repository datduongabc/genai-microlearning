import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./useAuth";

export function ProtectedRoute({ adminOnly = false }: { adminOnly?: boolean }) {
  const { token, user } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  if (adminOnly && !user?.is_admin) return <Navigate to="/courses" replace />;
  return <Outlet />;
}

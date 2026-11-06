import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./useAuth";

// authorOnly: the pages a student account an LMS made cannot use, which is all but its course chat.
export function ProtectedRoute({ adminOnly = false, authorOnly = false }: { adminOnly?: boolean; authorOnly?: boolean }) {
  const { token, user } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  if (authorOnly && user?.can_author === false) return <Navigate to="/learn" replace />;
  if (adminOnly && !user?.is_admin) return <Navigate to="/courses" replace />;
  return <Outlet />;
}

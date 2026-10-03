import { Navigate, Outlet } from "react-router-dom";
import { homeFor, useAuth } from "./useAuth";

// `role` keeps someone out of the other role's pages. It only saves them a screen of errors:
// the server decides what each role may do.
export function ProtectedRoute({ role }: { role?: "admin" | "teacher" }) {
  const { token, user } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  if (role && user?.role !== role) return <Navigate to={homeFor(user?.role)} replace />;
  return <Outlet />;
}

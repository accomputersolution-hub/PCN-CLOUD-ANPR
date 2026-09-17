import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../shared/auth/AuthProvider";
import { Spinner } from "../shared/ui/States";

export function RequireAuth() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking session" />;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export function GuestOnly() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner />;
  if (user) return <Navigate to="/" replace />;
  return <Outlet />;
}

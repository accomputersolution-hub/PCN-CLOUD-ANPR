import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Camera,
  LayoutDashboard,
  LogOut,
  Network,
  Radio,
  Search,
  Settings2,
  Shield,
  Sparkles,
  ScanLine,
} from "lucide-react";
import { clsx } from "clsx";
import { useAuth } from "../shared/auth/AuthProvider";
import { useRealtime } from "../shared/ws/RealtimeProvider";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/events", label: "Events", icon: Radio },
  { to: "/vehicles", label: "Search", icon: Search },
  { to: "/cameras", label: "Cameras", icon: Camera },
  { to: "/manual-anpr", label: "Manual ANPR", icon: ScanLine },
  { to: "/mock", label: "Mock ANPR", icon: Sparkles },
];

export function AppLayout() {
  const { user, logout, hasRole } = useAuth();
  const { connected } = useRealtime();
  const navigate = useNavigate();
  const admin = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");

  return (
    <div className="min-h-dvh bg-slate-100 md:flex">
      <aside className="hidden w-64 shrink-0 flex-col bg-ink-900 text-slate-200 md:flex">
        <div className="px-5 py-6">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-500">PCN Cloud</p>
          <p className="mt-1 text-lg font-semibold text-white">ANPR</p>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                clsx(
                  "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium",
                  isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5",
                )
              }
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
          {admin ? (
            <>
              <NavLink
                to="/sites"
                className={({ isActive }) =>
                  clsx(
                    "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium",
                    isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5",
                  )
                }
              >
                <Settings2 size={18} />
                Sites & gates
              </NavLink>
              <NavLink
                to="/connectivity"
                className={({ isActive }) =>
                  clsx(
                    "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium",
                    isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5",
                  )
                }
              >
                <Network size={18} />
                Connectivity
              </NavLink>
              <NavLink
                to="/reports"
                className={({ isActive }) =>
                  clsx(
                    "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium",
                    isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5",
                  )
                }
              >
                <Shield size={18} />
                Reports
              </NavLink>
            </>
          ) : null}
        </nav>
        <div className="border-t border-white/10 p-4 text-xs">
          <p className="font-medium text-white">{user?.full_name}</p>
          <p className="mt-0.5 text-slate-400">{user?.role.replaceAll("_", " ")}</p>
          <p className={clsx("mt-2", connected ? "text-emerald-400" : "text-slate-500")}>
            {connected ? "Live updates connected" : "Live updates offline"}
          </p>
          <button
            type="button"
            className="mt-3 flex min-h-11 w-full items-center gap-2 rounded-xl px-2 text-left text-slate-300 hover:bg-white/5"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
          >
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex min-h-dvh min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur md:hidden">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-accent-700">PCN Cloud</p>
            <p className="text-sm font-semibold">ANPR</p>
          </div>
          <span className={clsx("text-xs", connected ? "text-emerald-600" : "text-slate-400")}>
            {connected ? "Live" : "Offline"}
          </span>
        </header>
        <main className="flex-1 px-4 py-4 pb-24 md:px-8 md:py-8 md:pb-8">
          <Outlet />
        </main>
        <nav className="safe-bottom fixed inset-x-0 bottom-0 z-20 grid grid-cols-6 border-t border-slate-200 bg-white md:hidden">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                clsx(
                  "flex min-h-14 flex-col items-center justify-center gap-1 text-[11px] font-medium",
                  isActive ? "text-accent-700" : "text-slate-500",
                )
              }
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

import { Outlet } from "react-router-dom";

export function AuthLayout() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-ink-900 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center text-white">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-500">PCN CLOUD</p>
          <h1 className="mt-2 text-3xl font-semibold">PCN Cloud ANPR</h1>
          <p className="mt-2 text-sm text-slate-400">Operations console for sites, gates and vehicle events</p>
        </div>
        <div className="rounded-3xl bg-white p-6 shadow-card">
          <Outlet />
        </div>
      </div>
    </div>
  );
}

import type { ReactNode } from "react";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-slate-500" role="status">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-accent-600" />
      <span className="text-sm">{label}…</span>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-12 text-center">
      <p className="font-semibold text-slate-800">{title}</p>
      {hint ? <p className="mt-1 text-sm text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-8 text-center" role="alert">
      <p className="font-semibold text-red-800">Something went wrong</p>
      <p className="mt-1 text-sm text-red-700 break-words">{message || "An unexpected error occurred"}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 min-h-11 rounded-xl bg-white px-4 text-sm font-semibold text-red-800 shadow-sm"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3 md:mb-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-900 md:text-2xl">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
      </div>
      {actions}
    </div>
  );
}

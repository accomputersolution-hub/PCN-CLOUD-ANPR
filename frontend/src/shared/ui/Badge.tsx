import { clsx } from "clsx";
import type { ReactNode } from "react";

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "green" | "amber" | "red" | "teal";
}) {
  const map = {
    slate: "bg-slate-100 text-slate-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-800",
    red: "bg-red-50 text-red-700",
    teal: "bg-teal-50 text-teal-800",
  };
  return (
    <span className={clsx("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold", map[tone])}>
      {children}
    </span>
  );
}

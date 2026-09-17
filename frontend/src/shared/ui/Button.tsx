import { clsx } from "clsx";
import type { ButtonHTMLAttributes } from "react";

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  const styles = {
    primary: "bg-accent-600 text-white hover:bg-accent-700",
    secondary: "bg-white text-ink-900 border border-slate-200 hover:bg-slate-50",
    ghost: "bg-transparent text-slate-600 hover:bg-slate-100",
    danger: "bg-red-600 text-white hover:bg-red-700",
  } as const;
  return (
    <button
      className={clsx(
        "inline-flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-semibold transition disabled:opacity-50",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}

import { clsx } from "clsx";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

const control =
  "w-full min-h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none ring-accent-500 focus:ring-2";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(control, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(control, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(control, "min-h-24 py-2", props.className)} />;
}

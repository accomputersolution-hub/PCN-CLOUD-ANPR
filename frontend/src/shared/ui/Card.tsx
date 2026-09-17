import { clsx } from "clsx";
import type { HTMLAttributes } from "react";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx("rounded-2xl bg-white p-4 shadow-card md:p-5", className)} {...props} />;
}

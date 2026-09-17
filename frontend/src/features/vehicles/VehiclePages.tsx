import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { VehicleDetail, VehicleItem } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";

export function VehicleSearchPage() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const navigate = useNavigate();
  const results = useQuery({
    queryKey: ["vehicles", term],
    queryFn: () => api<VehicleItem[]>(`/vehicles${term ? `?q=${encodeURIComponent(term)}` : ""}`),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (q.trim()) navigate(`/vehicles/${q.trim().toUpperCase().replace(/[^A-Z0-9]/g, "")}`);
    else setTerm(q);
  }

  return (
    <div>
      <PageHeader title="Vehicle search" subtitle="Look up a plate to see visits, duration and snapshots." />
      <form className="mb-4 flex gap-2" onSubmit={onSubmit}>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="MH12AB1234" />
        <Button type="submit">Search</Button>
      </form>
      {results.isLoading ? <Spinner /> : null}
      {results.isError ? <ErrorState message={results.error.message} onRetry={() => void results.refetch()} /> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {(results.data ?? []).map((v) => (
          <Link key={v.id} to={`/vehicles/${v.plate_normalized}`} className="rounded-2xl bg-white p-4 shadow-card">
            <p className="font-mono text-lg font-semibold">{v.plate_normalized}</p>
            <p className="text-sm text-slate-500">
              {v.total_visits} visits · last seen {format(new Date(v.last_seen), "dd MMM, hh:mm a")}
            </p>
            <Badge tone={v.currently_inside ? "teal" : "slate"}>{v.currently_inside ? "Inside" : "Outside"}</Badge>
          </Link>
        ))}
      </div>
    </div>
  );
}

export function VehicleDetailPage() {
  const { plate = "" } = useParams();
  const query = useQuery({
    queryKey: ["vehicle", plate],
    queryFn: () => api<VehicleDetail>(`/vehicles/${plate}`),
    enabled: Boolean(plate),
  });
  if (query.isLoading) return <Spinner />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  if (!query.data) return <EmptyState title="Vehicle not found" />;
  const { vehicle, visits, events } = query.data;

  return (
    <div>
      <PageHeader
        title={vehicle.plate_normalized}
        subtitle={`First seen ${format(new Date(vehicle.first_seen), "dd MMM yyyy")} · ${vehicle.total_visits} visits`}
        actions={<Badge tone={vehicle.currently_inside ? "teal" : "slate"}>{vehicle.currently_inside ? "Currently inside" : "Outside"}</Badge>}
      />
      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <h2 className="font-semibold">Visit timeline</h2>
          {visits.length === 0 ? <EmptyState title="No visits" /> : null}
          <ol className="mt-4 space-y-4 border-l border-slate-200 pl-4">
            {visits.map((visit) => (
              <li key={visit.id}>
                <p className="text-sm font-semibold">{visit.status.replaceAll("_", " ")}</p>
                <p className="text-sm text-slate-500">
                  {visit.entry_at ? `In ${format(new Date(visit.entry_at), "dd MMM, hh:mm a")}` : "No entry"}
                  {visit.exit_at ? ` → Out ${format(new Date(visit.exit_at), "hh:mm a")}` : ""}
                </p>
                {visit.duration_label ? <p className="text-xs text-slate-500">Duration {visit.duration_label}</p> : null}
              </li>
            ))}
          </ol>
        </Card>
        <Card className="lg:col-span-3">
          <h2 className="font-semibold">Detection history</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {events.map((ev) => (
              <li key={ev.id} className="py-3">
                <div className="flex items-center justify-between">
                  <Badge tone={ev.direction === "ENTRY" ? "teal" : "amber"}>{ev.direction}</Badge>
                  <span className="text-sm text-slate-500">{format(new Date(ev.local_timestamp), "dd MMM, hh:mm:ss a")}</span>
                </div>
                <p className="mt-1 text-sm text-slate-600">
                  {ev.gate_name} · {ev.camera_name} · {Math.round(ev.ocr_confidence * 100)}%
                </p>
                <p className="text-xs text-slate-400">Raw OCR: {ev.raw_ocr_text}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

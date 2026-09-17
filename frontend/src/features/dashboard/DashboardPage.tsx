import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { format } from "date-fns";
import { api } from "../../shared/api/client";
import type { DashboardSummary } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Card } from "../../shared/ui/Card";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";
import { useRealtime } from "../../shared/ws/RealtimeProvider";

export function DashboardPage() {
  const { connected } = useRealtime();
  const query = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<DashboardSummary>("/dashboard/summary"),
  });

  if (query.isLoading) return <Spinner label="Loading dashboard" />;
  if (query.isError) {
    return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  }
  const data = query.data;
  if (!data) return <EmptyState title="No dashboard data" />;

  const tiles = [
    { label: "Entered today", value: data.entries_today },
    { label: "Exited today", value: data.exits_today },
    { label: "Currently inside", value: data.currently_inside },
    { label: "Detections today", value: data.detections_today },
    { label: "Active cameras", value: data.cameras_active },
    { label: "Offline cameras", value: data.cameras_offline },
  ];

  return (
    <div>
      <PageHeader
        title="Today"
        subtitle={`${data.timezone} · ${connected ? "live updates on" : "refreshing on interval"}`}
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {tiles.map((tile) => (
          <Card key={tile.label} className="min-h-[96px]">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{tile.label}</p>
            <p className="mt-2 text-3xl font-semibold text-ink-900">{tile.value}</p>
          </Card>
        ))}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Recent ANPR events</h2>
            <Link to="/events" className="text-sm font-medium text-accent-700">
              View all
            </Link>
          </div>
          {data.recent_events.length === 0 ? (
            <EmptyState title="No events yet" hint="Use Mock ANPR to generate your first detection." />
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.recent_events.map((ev) => (
                <li key={ev.id} className="flex items-center justify-between gap-3 py-3">
                  <div>
                    <Link to={`/vehicles/${ev.plate_normalized}`} className="font-mono text-base font-semibold">
                      {ev.plate_normalized}
                    </Link>
                    <p className="text-sm text-slate-500">
                      {format(new Date(ev.local_timestamp), "hh:mm:ss a")} · {ev.gate_name ?? "Gate"}
                    </p>
                  </div>
                  <div className="text-right">
                    <Badge tone={ev.direction === "ENTRY" ? "teal" : "amber"}>{ev.direction}</Badge>
                    <p className="mt-1 text-xs text-slate-500">Confidence {Math.round(ev.ocr_confidence * 100)}%</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="lg:col-span-2">
          <h2 className="font-semibold">Camera health</h2>
          <p className="mt-3 text-sm text-slate-600">
            {data.cameras_active} online · {data.cameras_offline} not online · {data.cameras_total} total
          </p>
          <Link to="/cameras" className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-accent-700">
            Manage cameras
          </Link>
        </Card>
      </div>
    </div>
  );
}

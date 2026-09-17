import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { CameraItem, EventItem, GateItem, Paginated, SiteItem } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Field, Input, Select } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";

export function EventsPage() {
  const [plate, setPlate] = useState("");
  const [direction, setDirection] = useState("");
  const [siteId, setSiteId] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [gateId, setGateId] = useState("");
  const [applied, setApplied] = useState("");

  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<SiteItem[]>("/sites") });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });
  const gates = useQuery({ queryKey: ["gates"], queryFn: () => api<GateItem[]>("/gates") });
  const events = useQuery({
    queryKey: ["events", applied],
    queryFn: () => api<Paginated<EventItem>>(`/events?${applied}`),
  });

  function apply() {
    const params = new URLSearchParams();
    if (plate) params.set("plate", plate);
    if (direction) params.set("direction", direction);
    if (siteId) params.set("site_id", siteId);
    if (cameraId) params.set("camera_id", cameraId);
    if (gateId) params.set("gate_id", gateId);
    setApplied(params.toString());
  }

  return (
    <div>
      <PageHeader title="Events" subtitle="Search detections by plate, site, gate, camera and direction." />
      <div className="mb-4 grid gap-3 rounded-2xl bg-white p-4 shadow-card md:grid-cols-3 xl:grid-cols-6">
        <Field label="Plate">
          <Input value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="MH12AB1234" />
        </Field>
        <Field label="Direction">
          <Select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">Any</option>
            <option value="ENTRY">ENTRY</option>
            <option value="EXIT">EXIT</option>
          </Select>
        </Field>
        <Field label="Site">
          <Select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
            <option value="">All sites</option>
            {(sites.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Gate">
          <Select value={gateId} onChange={(e) => setGateId(e.target.value)}>
            <option value="">All gates</option>
            {(gates.data ?? []).map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Camera">
          <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
            <option value="">All cameras</option>
            {(cameras.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex items-end">
          <Button type="button" className="w-full" onClick={apply}>
            Search
          </Button>
        </div>
      </div>

      {events.isLoading ? <Spinner /> : null}
      {events.isError ? <ErrorState message={events.error.message} onRetry={() => void events.refetch()} /> : null}
      {events.data && events.data.items.length === 0 ? <EmptyState title="No matching events" /> : null}

      <div className="space-y-3 md:hidden">
        {(events.data?.items ?? []).map((ev) => (
          <Link key={ev.id} to={`/vehicles/${ev.plate_normalized}`} className="block rounded-2xl bg-white p-4 shadow-card">
            <p className="font-mono text-lg font-semibold">{ev.plate_normalized}</p>
            <p className="text-sm text-slate-500">
              {format(new Date(ev.local_timestamp), "dd MMM, hh:mm a")} · {ev.gate_name}
            </p>
            <div className="mt-2 flex gap-2">
              <Badge tone={ev.direction === "ENTRY" ? "teal" : "amber"}>{ev.direction}</Badge>
              <Badge>{Math.round(ev.ocr_confidence * 100)}%</Badge>
            </div>
          </Link>
        ))}
      </div>

      <div className="hidden overflow-hidden rounded-2xl bg-white shadow-card md:block">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Plate</th>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Direction</th>
              <th className="px-4 py-3">Gate</th>
              <th className="px-4 py-3">Camera</th>
              <th className="px-4 py-3">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {(events.data?.items ?? []).map((ev) => (
              <tr key={ev.id} className="border-t border-slate-100">
                <td className="px-4 py-3 font-mono font-semibold">
                  <Link to={`/vehicles/${ev.plate_normalized}`}>{ev.plate_normalized}</Link>
                </td>
                <td className="px-4 py-3">{format(new Date(ev.local_timestamp), "dd MMM yyyy, hh:mm:ss a")}</td>
                <td className="px-4 py-3">
                  <Badge tone={ev.direction === "ENTRY" ? "teal" : "amber"}>{ev.direction}</Badge>
                </td>
                <td className="px-4 py-3">{ev.gate_name}</td>
                <td className="px-4 py-3">{ev.camera_name}</td>
                <td className="px-4 py-3">{Math.round(ev.ocr_confidence * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { CameraItem, EventItem, GateItem, Paginated, RegistryMatch, SiteItem } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Field, Input, Select } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";

function registryStatus(match: RegistryMatch | null | undefined): "active" | "inactive" | "unknown" {
  if (!match) return "unknown";
  if (match.registry_status === "active" || match.registry_status === "inactive") {
    return match.registry_status;
  }
  if (match.known && match.active === false) return "inactive";
  if (match.known && match.active !== false) return "active";
  return "unknown";
}

function RegistryCell({ match }: { match: RegistryMatch | null | undefined }) {
  const status = registryStatus(match);
  if (status === "unknown") {
    return (
      <div className="text-slate-500">
        <p className="font-medium text-slate-600">Unknown</p>
        <p className="text-xs">—</p>
      </div>
    );
  }
  const name = match?.person_name?.trim() || "—";
  const category = (match?.category || match?.status || "—").toString();
  const unit = match?.flat_room_unit?.trim() || "—";
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-slate-900">{name}</span>
        {status === "inactive" ? <Badge tone="red">INACTIVE</Badge> : null}
      </div>
      <p className="text-xs capitalize text-slate-500">
        {category}
        {unit !== "—" ? ` · ${unit}` : ""}
      </p>
    </div>
  );
}

export function EventsPage() {
  const [plate, setPlate] = useState("");
  const [personName, setPersonName] = useState("");
  const [flatUnit, setFlatUnit] = useState("");
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
    if (personName) params.set("person_name", personName);
    if (flatUnit) params.set("flat_room_unit", flatUnit);
    if (direction) params.set("direction", direction);
    if (siteId) params.set("site_id", siteId);
    if (cameraId) params.set("camera_id", cameraId);
    if (gateId) params.set("gate_id", gateId);
    setApplied(params.toString());
  }

  return (
    <div>
      <PageHeader
        title="Events"
        subtitle="Search detections by plate, registry name/unit, site, gate, camera and direction."
      />
      <div className="mb-4 grid gap-3 rounded-2xl bg-white p-4 shadow-card md:grid-cols-3 xl:grid-cols-4">
        <Field label="Plate">
          <Input value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="MH12AB1234" />
        </Field>
        <Field label="Name">
          <Input value={personName} onChange={(e) => setPersonName(e.target.value)} placeholder="Resident name" />
        </Field>
        <Field label="Unit / flat">
          <Input value={flatUnit} onChange={(e) => setFlatUnit(e.target.value)} placeholder="B-204" />
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
            <div className="mt-2">
              <RegistryCell match={ev.registry_match} />
            </div>
            <p className="mt-2 text-sm text-slate-500">
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
              <th className="px-4 py-3">Registry</th>
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
                <td className="px-4 py-3">
                  <RegistryCell match={ev.registry_match} />
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

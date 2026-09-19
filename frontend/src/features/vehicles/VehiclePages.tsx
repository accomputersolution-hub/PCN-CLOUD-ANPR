import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, api } from "../../shared/api/client";
import type {
  SiteItem,
  VehicleDetail,
  VehicleItem,
  VehicleRegistryCategory,
  VehicleRegistryItem,
} from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthProvider";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";

const CATEGORIES: VehicleRegistryCategory[] = ["resident", "guest", "staff", "vendor"];

function categoryTone(cat: string): "teal" | "amber" | "green" | "slate" {
  if (cat === "resident") return "teal";
  if (cat === "guest") return "amber";
  if (cat === "staff") return "green";
  return "slate";
}

export function VehicleSearchPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER", "SECURITY_GUARD");
  const canDisable = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "sightings" ? "sightings" : "registry";

  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<SiteItem[]>("/sites") });
  const [siteId, setSiteId] = useState("");
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({
    plate: "",
    category: "resident" as VehicleRegistryCategory,
    person_name: "",
    mobile_number: "",
    flat_room_unit: "",
    notes: "",
  });

  useEffect(() => {
    if (!siteId && sites.data?.[0]) setSiteId(sites.data[0].id);
  }, [siteId, sites.data]);

  // Prefill from Manual ANPR "Register" link
  useEffect(() => {
    const plate = searchParams.get("plate");
    const site = searchParams.get("site_id");
    if (site) setSiteId(site);
    if (plate) {
      setForm((f) => ({ ...f, plate, category: "guest" }));
      setShowForm(true);
      setSearchParams({ tab: "registry" }, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const registry = useQuery({
    queryKey: ["vehicle-registry", siteId, term],
    queryFn: () => {
      const params = new URLSearchParams();
      if (term) params.set("q", term);
      const qs = params.toString();
      return api<VehicleRegistryItem[]>(
        `/sites/${siteId}/vehicle-registry${qs ? `?${qs}` : ""}`,
      );
    },
    enabled: Boolean(siteId) && tab === "registry",
  });

  const sightings = useQuery({
    queryKey: ["vehicles", term],
    queryFn: () => api<VehicleItem[]>(`/vehicles${term ? `?q=${encodeURIComponent(term)}` : ""}`),
    enabled: tab === "sightings",
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!siteId) throw new Error("Select a site");
      if (editId) {
        return api<VehicleRegistryItem>(`/sites/${siteId}/vehicle-registry/${editId}`, {
          method: "PATCH",
          body: JSON.stringify({
            category: form.category,
            person_name: form.person_name,
            mobile_number: form.mobile_number || null,
            flat_room_unit: form.flat_room_unit || null,
            notes: form.notes || null,
          }),
        });
      }
      return api<VehicleRegistryItem>(`/sites/${siteId}/vehicle-registry`, {
        method: "POST",
        body: JSON.stringify({
          plate: form.plate,
          category: form.category,
          person_name: form.person_name,
          mobile_number: form.mobile_number || null,
          flat_room_unit: form.flat_room_unit || null,
          notes: form.notes || null,
        }),
      });
    },
    onSuccess: () => {
      toast.success(editId ? "Registration updated" : "Vehicle registered");
      setShowForm(false);
      setEditId(null);
      setForm({
        plate: "",
        category: "resident",
        person_name: "",
        mobile_number: "",
        flat_room_unit: "",
        notes: "",
      });
      void queryClient.invalidateQueries({ queryKey: ["vehicle-registry"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  function onSearch(e: FormEvent) {
    e.preventDefault();
    setTerm(q.trim());
  }

  function startEdit(row: VehicleRegistryItem) {
    setEditId(row.id);
    setForm({
      plate: row.plate_normalized,
      category: (row.category as VehicleRegistryCategory) || "resident",
      person_name: row.person_name || "",
      mobile_number: row.mobile_number || "",
      flat_room_unit: row.flat_room_unit || "",
      notes: row.notes || "",
    });
    setShowForm(true);
  }

  return (
    <div>
      <PageHeader
        title="Vehicles"
        subtitle="Site vehicle registry (residents, guests, staff, vendors) and ANPR sighting history."
        actions={
          canWrite && tab === "registry" ? (
            <Button
              type="button"
              onClick={() => {
                setEditId(null);
                setForm({
                  plate: "",
                  category: hasRole("SECURITY_GUARD") && !hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER")
                    ? "guest"
                    : "resident",
                  person_name: "",
                  mobile_number: "",
                  flat_room_unit: "",
                  notes: "",
                });
                setShowForm(true);
              }}
            >
              Add vehicle
            </Button>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <Button
          type="button"
          variant={tab === "registry" ? "primary" : "secondary"}
          onClick={() => setSearchParams({ tab: "registry" })}
        >
          Registry
        </Button>
        <Button
          type="button"
          variant={tab === "sightings" ? "primary" : "secondary"}
          onClick={() => setSearchParams({ tab: "sightings" })}
        >
          Sightings
        </Button>
      </div>

      {tab === "registry" ? (
        <>
          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <Field label="Site">
              <Select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
                {(sites.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <form className="flex items-end gap-2 md:col-span-2" onSubmit={onSearch}>
              <Field label="Search plate / name / flat">
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="MH12AB1234 or B-204" />
              </Field>
              <Button type="submit">Search</Button>
            </form>
          </div>

          {showForm && canWrite ? (
            <Card className="mb-4">
              <h2 className="font-semibold">{editId ? "Edit registration" : "Register vehicle"}</h2>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Plate">
                  <Input
                    value={form.plate}
                    disabled={Boolean(editId)}
                    onChange={(e) => setForm({ ...form, plate: e.target.value.toUpperCase() })}
                    placeholder="MH12AB1234"
                  />
                </Field>
                <Field label="Category">
                  <Select
                    value={form.category}
                    onChange={(e) => setForm({ ...form, category: e.target.value as VehicleRegistryCategory })}
                  >
                    {CATEGORIES.filter((c) =>
                      hasRole("SECURITY_GUARD") &&
                      !hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER")
                        ? c === "guest"
                        : true,
                    ).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Person name">
                  <Input
                    value={form.person_name}
                    onChange={(e) => setForm({ ...form, person_name: e.target.value })}
                  />
                </Field>
                <Field label="Flat / room / unit">
                  <Input
                    value={form.flat_room_unit}
                    onChange={(e) => setForm({ ...form, flat_room_unit: e.target.value })}
                  />
                </Field>
                <Field label="Mobile (optional)">
                  <Input
                    value={form.mobile_number}
                    onChange={(e) => setForm({ ...form, mobile_number: e.target.value })}
                  />
                </Field>
                <Field label="Notes (optional)">
                  <Input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </Field>
              </div>
              <div className="mt-3 flex gap-2">
                <Button type="button" onClick={() => void save.mutateAsync()} disabled={save.isPending}>
                  {save.isPending ? "Saving…" : "Save"}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
              </div>
            </Card>
          ) : null}

          {registry.isLoading ? <Spinner /> : null}
          {registry.isError ? (
            <ErrorState message={registry.error.message} onRetry={() => void registry.refetch()} />
          ) : null}
          {!registry.isLoading && !(registry.data?.length) ? (
            <EmptyState title="No registered vehicles" hint="Add a resident, guest, staff, or vendor for this site." />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {(registry.data ?? []).map((row) => (
                <Card key={row.id}>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-mono text-lg font-semibold">{row.plate_normalized}</p>
                      <p className="text-sm text-slate-600">{row.person_name || "—"}</p>
                      {row.flat_room_unit ? (
                        <p className="text-xs text-slate-500">Unit {row.flat_room_unit}</p>
                      ) : null}
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <Badge tone={categoryTone(String(row.category))}>{row.category}</Badge>
                      <Badge tone={row.active ? "green" : "red"}>{row.active ? "Active" : "Inactive"}</Badge>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {canWrite ? (
                      <Button type="button" variant="secondary" onClick={() => startEdit(row)}>
                        Edit
                      </Button>
                    ) : null}
                    {canDisable && row.active ? (
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={async () => {
                          try {
                            await api(`/sites/${siteId}/vehicle-registry/${row.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({ active: false }),
                            });
                            toast.success("Vehicle disabled");
                            void queryClient.invalidateQueries({ queryKey: ["vehicle-registry"] });
                          } catch (e) {
                            toast.error(e instanceof Error ? e.message : "Disable failed");
                          }
                        }}
                      >
                        Disable
                      </Button>
                    ) : null}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </>
      ) : (
        <SightingsPanel term={term} q={q} setQ={setQ} onSearch={onSearch} sightings={sightings} />
      )}
    </div>
  );
}

function SightingsPanel({
  term,
  q,
  setQ,
  onSearch,
  sightings,
}: {
  term: string;
  q: string;
  setQ: (v: string) => void;
  onSearch: (e: FormEvent) => void;
  sightings: ReturnType<typeof useQuery<VehicleItem[]>>;
}) {
  const navigate = useNavigate();
  return (
    <>
      <form className="mb-4 flex gap-2" onSubmit={onSearch}>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="MH12AB1234" />
        <Button
          type="submit"
          onClick={() => {
            const compact = q.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
            if (compact) navigate(`/vehicles/${compact}`);
          }}
        >
          Search
        </Button>
      </form>
      {sightings.isLoading ? <Spinner /> : null}
      {sightings.isError ? (
        <ErrorState message={sightings.error.message} onRetry={() => void sightings.refetch()} />
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">
        {(sightings.data ?? []).map((v) => (
          <Link key={v.id} to={`/vehicles/${v.plate_normalized}`} className="rounded-2xl bg-white p-4 shadow-card">
            <p className="font-mono text-lg font-semibold">{v.plate_normalized}</p>
            <p className="text-sm text-slate-500">
              {v.total_visits} visits · last seen {format(new Date(v.last_seen), "dd MMM, hh:mm a")}
            </p>
            <Badge tone={v.currently_inside ? "teal" : "slate"}>
              {v.currently_inside ? "Inside" : "Outside"}
            </Badge>
          </Link>
        ))}
      </div>
      {!sightings.isLoading && term && !(sightings.data?.length) ? (
        <EmptyState title="No sightings" hint="Confirm a Manual/Mock ANPR detection first." />
      ) : null}
    </>
  );
}

export function VehicleDetailPage() {
  const { plate = "" } = useParams();
  const query = useQuery({
    queryKey: ["vehicle", plate],
    queryFn: () => api<VehicleDetail>(`/vehicles/${plate}`),
    enabled: Boolean(plate),
    retry: (count, err) => {
      if (err instanceof ApiError && err.status === 404) return false;
      return count < 1;
    },
  });
  if (query.isLoading) return <Spinner />;
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <EmptyState
          title="Vehicle not found"
          hint={`No vehicle record for ${plate.toUpperCase()}. Confirm a Manual/Mock ANPR detection first, or check Events for that plate.`}
        />
      );
    }
    return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  }
  if (!query.data) {
    return (
      <EmptyState
        title="Vehicle not found"
        hint={`No vehicle record for ${plate.toUpperCase()}. Confirm a Manual/Mock ANPR detection first, or check Events for that plate.`}
      />
    );
  }
  const { vehicle, visits, events } = query.data;

  return (
    <div>
      <PageHeader
        title={vehicle.plate_normalized}
        subtitle={`First seen ${format(new Date(vehicle.first_seen), "dd MMM yyyy")} · ${vehicle.total_visits} visits`}
        actions={
          <Badge tone={vehicle.currently_inside ? "teal" : "slate"}>
            {vehicle.currently_inside ? "Currently inside" : "Outside"}
          </Badge>
        }
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
                {visit.duration_label ? (
                  <p className="text-xs text-slate-500">Duration {visit.duration_label}</p>
                ) : null}
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
                  <span className="text-sm text-slate-500">
                    {format(new Date(ev.local_timestamp), "dd MMM, hh:mm:ss a")}
                  </span>
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

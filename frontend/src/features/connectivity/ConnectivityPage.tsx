import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type { GatewayItem, NvrItem, SiteConnectivity, SiteItem } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";
import { useAuth } from "../../shared/auth/AuthProvider";

function formatTs(value: string | null) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

const healthTone: Record<string, "green" | "amber" | "red" | "slate"> = {
  HEALTHY: "green",
  UNKNOWN: "slate",
  DEGRADED: "amber",
  OFFLINE: "red",
  DISABLED: "slate",
  REVOKED: "red",
};

const vpnTone: Record<string, "green" | "amber" | "red" | "slate"> = {
  CONNECTED: "green",
  CONNECTING: "amber",
  DISCONNECTED: "red",
  ERROR: "red",
  UNKNOWN: "slate",
};

export function ConnectivityPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");
  const queryClient = useQueryClient();
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<SiteItem[]>("/sites") });
  const gateways = useQuery({ queryKey: ["gateways"], queryFn: () => api<GatewayItem[]>("/gateways") });
  const nvrs = useQuery({ queryKey: ["nvrs"], queryFn: () => api<NvrItem[]>("/nvrs") });
  const [siteId, setSiteId] = useState("");
  const selectedSite = siteId || sites.data?.[0]?.id || "";
  const connectivity = useQuery({
    queryKey: ["connectivity", selectedSite],
    queryFn: () => api<SiteConnectivity>(`/sites/${selectedSite}/connectivity`),
    enabled: Boolean(selectedSite),
  });

  if (sites.isLoading || gateways.isLoading) return <Spinner label="Loading connectivity" />;
  if (sites.isError) return <ErrorState message={sites.error.message} onRetry={() => void sites.refetch()} />;

  const siteList = sites.data ?? [];
  const siteGateways = (gateways.data ?? []).filter((g) => g.site_id === selectedSite);
  const siteNvrs = (nvrs.data ?? []).filter((n) => n.site_id === selectedSite);

  return (
    <div>
      <PageHeader
        title="Connectivity"
        subtitle="Existing VPN routers and PCN Cloud Gateways. Device keys and VPN private material are never shown after issue."
      />
      <div className="mb-4 max-w-sm">
        <Field label="Site">
          <Select value={selectedSite} onChange={(e) => setSiteId(e.target.value)}>
            {siteList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {connectivity.data ? <ConnectivitySummary data={connectivity.data} nvrCount={siteNvrs.length} /> : null}

      {canWrite && selectedSite ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <ConnectivityForm
            siteId={selectedSite}
            gateways={siteGateways}
            current={connectivity.data}
            onSaved={() => {
              void queryClient.invalidateQueries({ queryKey: ["connectivity"] });
              void queryClient.invalidateQueries({ queryKey: ["sites"] });
            }}
          />
          <GatewayForm
            siteId={selectedSite}
            onCreated={(key) => {
              toast.success(`Gateway enrolled. Copy the device key now — it is shown only once.\n${key}`);
              void queryClient.invalidateQueries({ queryKey: ["gateways"] });
              void queryClient.invalidateQueries({ queryKey: ["connectivity"] });
            }}
          />
          <NvrForm
            siteId={selectedSite}
            gateways={siteGateways}
            onSaved={() => {
              void queryClient.invalidateQueries({ queryKey: ["nvrs"] });
              void queryClient.invalidateQueries({ queryKey: ["connectivity"] });
            }}
          />
        </div>
      ) : null}

      <h2 className="mt-8 text-sm font-semibold uppercase tracking-wide text-slate-500">Gateways</h2>
      {!siteGateways.length ? (
        <div className="mt-3">
          <EmptyState title="No gateways for this site" hint="Add an existing VPN router or a PCN Cloud Gateway." />
        </div>
      ) : (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {siteGateways.map((gw) => (
            <GatewayCard
              key={gw.id}
              gateway={gw}
              canWrite={canWrite}
              onChanged={() => {
                void queryClient.invalidateQueries({ queryKey: ["gateways"] });
                void queryClient.invalidateQueries({ queryKey: ["connectivity"] });
              }}
            />
          ))}
        </div>
      )}

      <h2 className="mt-8 text-sm font-semibold uppercase tracking-wide text-slate-500">NVRs</h2>
      {!siteNvrs.length ? (
        <p className="mt-2 text-sm text-slate-500">No NVRs registered. Cameras can still use a direct RTSP URL.</p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm text-slate-700">
          {siteNvrs.map((n) => (
            <li key={n.id}>
              {n.name} · {n.vendor || "GENERIC"} · {n.host || "host not set"} · {n.camera_count} cameras
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ConnectivitySummary({ data, nvrCount }: { data: SiteConnectivity; nvrCount: number }) {
  const mode =
    data.connectivity_mode === "PCN_CLOUD_GATEWAY" ? "PCN Cloud Gateway" : "Existing VPN Router";
  const anpr = data.anpr_deployment_mode === "CLOUD_VIA_GATEWAY" ? "Cloud via gateway" : "Local Edge Agent";
  return (
    <Card>
      <p className="font-semibold">{data.site_name}</p>
      <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-slate-400">Connectivity mode</dt>
          <dd>{mode}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">ANPR deployment</dt>
          <dd>{anpr}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Cameras on this path</dt>
          <dd>{data.camera_count}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">NVRs</dt>
          <dd>{data.nvr_count || nvrCount}</dd>
        </div>
      </dl>
    </Card>
  );
}

function ConnectivityForm({
  siteId,
  gateways,
  current,
  onSaved,
}: {
  siteId: string;
  gateways: GatewayItem[];
  current?: SiteConnectivity;
  onSaved: () => void;
}) {
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api(`/sites/${siteId}/connectivity`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("Connectivity updated");
      onSaved();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Card>
      <h2 className="font-semibold">Site path</h2>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          mutation.mutate({
            connectivity_mode: f.get("connectivity_mode"),
            anpr_deployment_mode: f.get("anpr_deployment_mode"),
            primary_gateway_id: f.get("primary_gateway_id") || null,
          });
        }}
      >
        <Field label="Connectivity mode">
          <Select name="connectivity_mode" defaultValue={current?.connectivity_mode ?? "EXISTING_VPN_ROUTER"}>
            <option value="EXISTING_VPN_ROUTER">Existing VPN Router</option>
            <option value="PCN_CLOUD_GATEWAY">PCN Cloud Gateway</option>
          </Select>
        </Field>
        <Field label="ANPR processing">
          <Select name="anpr_deployment_mode" defaultValue={current?.anpr_deployment_mode ?? "LOCAL_EDGE_AGENT"}>
            <option value="LOCAL_EDGE_AGENT">Local Edge Agent at site</option>
            <option value="CLOUD_VIA_GATEWAY">Cloud via gateway (future)</option>
          </Select>
        </Field>
        <Field label="Primary gateway">
          <Select name="primary_gateway_id" defaultValue={current?.primary_gateway_id ?? ""}>
            <option value="">None</option>
            {gateways.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" disabled={mutation.isPending}>
          Save path
        </Button>
      </form>
    </Card>
  );
}

function GatewayForm({ siteId, onCreated }: { siteId: string; onCreated: (deviceKey: string) => void }) {
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ device_key: string }>("/gateways", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (res) => onCreated(res.device_key),
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Card>
      <h2 className="font-semibold">Enroll gateway</h2>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          mutation.mutate({
            site_id: siteId,
            name: f.get("name"),
            device_type: f.get("device_type"),
            vendor: f.get("vendor") || "GENERIC",
            model: f.get("model") || "",
          });
        }}
      >
        <Field label="Name">
          <Input name="name" required placeholder="Lobby VPN / PCN Gateway" />
        </Field>
        <Field label="Type">
          <Select name="device_type" defaultValue="PCN_CLOUD_GATEWAY">
            <option value="EXISTING_VPN_ROUTER">Existing VPN Router</option>
            <option value="PCN_CLOUD_GATEWAY">PCN Cloud Gateway</option>
          </Select>
        </Field>
        <Field label="Vendor">
          <Input name="vendor" placeholder="MikroTik, TP-Link, GENERIC" />
        </Field>
        <Field label="Model">
          <Input name="model" placeholder="Optional, e.g. ER605" />
        </Field>
        <Button type="submit" disabled={mutation.isPending}>
          Enroll
        </Button>
      </form>
    </Card>
  );
}

function NvrForm({
  siteId,
  gateways,
  onSaved,
}: {
  siteId: string;
  gateways: GatewayItem[];
  onSaved: () => void;
}) {
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/nvrs", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("NVR saved");
      onSaved();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Card>
      <h2 className="font-semibold">Register NVR</h2>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          mutation.mutate({
            site_id: siteId,
            name: f.get("name"),
            vendor: f.get("vendor") || "GENERIC",
            host: f.get("host") || "",
            channel_count: Number(f.get("channel_count") || 0),
            gateway_id: f.get("gateway_id") || null,
          });
        }}
      >
        <Field label="Name">
          <Input name="name" required placeholder="Lobby NVR" />
        </Field>
        <Field label="LAN host">
          <Input name="host" placeholder="192.168.1.10" />
        </Field>
        <Field label="Vendor">
          <Input name="vendor" placeholder="Hikvision / Dahua / GENERIC" />
        </Field>
        <Field label="Via gateway">
          <Select name="gateway_id">
            <option value="">Site default</option>
            {gateways.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" disabled={mutation.isPending}>
          Save NVR
        </Button>
      </form>
    </Card>
  );
}

function GatewayCard({
  gateway,
  canWrite,
  onChanged,
}: {
  gateway: GatewayItem;
  canWrite: boolean;
  onChanged: () => void;
}) {
  const online = Boolean(gateway.last_seen) && gateway.health_status === "HEALTHY";
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{gateway.name}</p>
          <p className="text-sm text-slate-500">
            {gateway.device_type === "PCN_CLOUD_GATEWAY" ? "PCN Cloud Gateway" : "Existing VPN Router"}
            {gateway.vendor ? ` · ${gateway.vendor}` : ""}
            {gateway.model ? ` ${gateway.model}` : ""}
          </p>
        </div>
        <Badge tone={online ? "green" : healthTone[gateway.health_status] ?? "slate"}>
          {online ? "Online" : gateway.health_status}
        </Badge>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
        <dt className="text-slate-400">VPN</dt>
        <dd>
          <Badge tone={vpnTone[gateway.vpn_status] ?? "slate"}>{gateway.vpn_status}</Badge>
        </dd>
        <dt className="text-slate-400">Last heartbeat</dt>
        <dd>{formatTs(gateway.last_seen)}</dd>
        <dt className="text-slate-400">Cameras / NVRs</dt>
        <dd>
          {gateway.camera_count} / {gateway.nvr_count}
        </dd>
        <dt className="text-slate-400">Provisioning</dt>
        <dd>{gateway.provisioning_status}</dd>
      </dl>
      {canWrite ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={async () => {
              try {
                const res = await api<{ device_key: string }>(`/gateways/${gateway.id}/provision`, { method: "POST" });
                toast.success(`New device key (shown once):\n${res.device_key}`);
                onChanged();
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Provision failed");
              }
            }}
          >
            Rotate key
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={async () => {
              try {
                await api(`/gateways/${gateway.id}/revoke`, { method: "POST" });
                toast.success("Gateway revoked");
                onChanged();
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Revoke failed");
              }
            }}
          >
            Revoke
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

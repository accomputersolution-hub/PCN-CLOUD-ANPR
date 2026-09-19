import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type { CameraItem, GateItem, GatewayItem, NvrItem, SiteItem } from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { EmptyState, ErrorState, PageHeader, Spinner } from "../../shared/ui/States";
import { useAuth } from "../../shared/auth/AuthProvider";

const statusTone = {
  ONLINE: "green",
  OFFLINE: "red",
  ERROR: "red",
  UNKNOWN: "slate",
  CONNECTING: "amber",
} as const;

function formatTs(value: string | null) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

type TestResult = {
  ok: boolean;
  message: string;
  resolution?: string | null;
  fps?: number | null;
  first_frame_received?: boolean;
  probe?: string;
};

export function CamerasPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");
  const canTest = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");
  const canCalibrate = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER", "SECURITY_GUARD");
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<SiteItem[]>("/sites") });
  const gates = useQuery({ queryKey: ["gates"], queryFn: () => api<GateItem[]>("/gates") });
  const nvrs = useQuery({ queryKey: ["nvrs"], queryFn: () => api<NvrItem[]>("/nvrs") });
  const gateways = useQuery({ queryKey: ["gateways"], queryFn: () => api<GatewayItem[]>("/gateways") });

  if (cameras.isLoading) return <Spinner label="Loading cameras" />;
  if (cameras.isError) return <ErrorState message={cameras.error.message} onRetry={() => void cameras.refetch()} />;

  return (
    <div>
      <PageHeader
        title="Cameras"
        subtitle="RTSP credentials are stored encrypted and never shown in the UI. Live capture runs on the Edge Agent, not in the browser."
        actions={
          canWrite ? (
            <Button type="button" onClick={() => setOpen(true)}>
              Add camera
            </Button>
          ) : null
        }
      />
      {open && canWrite ? (
        <CameraForm
          sites={sites.data ?? []}
          gates={gates.data ?? []}
          nvrs={nvrs.data ?? []}
          gateways={gateways.data ?? []}
          onClose={() => setOpen(false)}
          onSaved={() => {
            setOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["cameras"] });
          }}
        />
      ) : null}
      {!cameras.data?.length ? (
        <EmptyState title="No cameras" hint="Add an RTSP camera or use demo cameras from seed data." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {cameras.data.map((cam) => (
            <Card key={cam.id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-semibold">{cam.name}</p>
                  <p className="text-sm text-slate-500">
                    {cam.site_name ?? "—"} · {cam.gate_name ?? "—"} · {cam.direction}
                  </p>
                </div>
                <Badge tone={statusTone[cam.status] ?? "slate"}>{cam.status}</Badge>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
                <dt className="text-slate-400">RTSP configured</dt>
                <dd>{cam.rtsp_configured ? "YES" : "NO"}</dd>
                <dt className="text-slate-400">Streaming</dt>
                <dd>{cam.streaming ? "YES" : "NO"}</dd>
                <dt className="text-slate-400">Last frame</dt>
                <dd>{formatTs(cam.last_frame_at)}</dd>
                <dt className="text-slate-400">FPS</dt>
                <dd>{cam.fps != null ? cam.fps.toFixed(1) : "—"}</dd>
                <dt className="text-slate-400">ANPR Zone</dt>
                <dd>{cam.anpr_roi?.enabled ? "ON" : "OFF"}</dd>
                <dt className="text-slate-400">Last error</dt>
                <dd className={cam.connection_error ? "text-red-600" : ""}>
                  {cam.connection_error || "—"}
                </dd>
              </dl>
              <div className="mt-4 flex flex-wrap gap-2">
                {canCalibrate ? (
                  <Link
                    to={`/cameras/${cam.id}/calibrate`}
                    className="inline-flex items-center rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Calibrate ANPR
                  </Link>
                ) : null}
                {canWrite && cam.anpr_roi?.enabled ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        await api(`/cameras/${cam.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ anpr_roi: null }),
                        });
                        toast.success("ANPR Zone cleared");
                        void queryClient.invalidateQueries({ queryKey: ["cameras"] });
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : "Clear ROI failed");
                      }
                    }}
                  >
                    Clear ROI
                  </Button>
                ) : null}
                {canTest ? (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={async () => {
                      try {
                        const res = await api<TestResult>(`/cameras/${cam.id}/test-rtsp`, { method: "POST" });
                        if (res.ok) {
                          const detail = [
                            res.message,
                            res.resolution ? `Resolution: ${res.resolution}` : null,
                            res.fps != null ? `FPS: ${res.fps}` : null,
                            `First frame received: ${res.first_frame_received ? "YES" : "NO"}`,
                          ]
                            .filter(Boolean)
                            .join("\n");
                          toast.success(detail);
                        } else {
                          toast.error(res.message);
                        }
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : "RTSP test failed");
                      }
                      void queryClient.invalidateQueries({ queryKey: ["cameras"] });
                    }}
                  >
                    Test RTSP
                  </Button>
                ) : null}
                {canWrite ? (
                  <>
                    <Button
                      type="button"
                      onClick={async () => {
                        try {
                          await api(`/edge/cameras/${cam.id}/start`, { method: "POST" });
                          toast.success("Start requested — Edge Agent will begin capture");
                          void queryClient.invalidateQueries({ queryKey: ["cameras"] });
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Start failed");
                        }
                      }}
                      disabled={!cam.rtsp_configured || !cam.enabled || cam.streaming}
                    >
                      Start
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={async () => {
                        try {
                          await api(`/edge/cameras/${cam.id}/stop`, { method: "POST" });
                          toast.success("Stop requested");
                          void queryClient.invalidateQueries({ queryKey: ["cameras"] });
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Stop failed");
                        }
                      }}
                      disabled={!cam.streaming}
                    >
                      Stop
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={async () => {
                        await api(`/cameras/${cam.id}/enable`, {
                          method: "POST",
                          body: JSON.stringify({ enabled: !cam.enabled }),
                        });
                        void queryClient.invalidateQueries({ queryKey: ["cameras"] });
                      }}
                    >
                      {cam.enabled ? "Disable" : "Enable"}
                    </Button>
                  </>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function CameraForm({
  sites,
  gates,
  nvrs,
  gateways,
  onClose,
  onSaved,
}: {
  sites: SiteItem[];
  gates: GateItem[];
  nvrs: NvrItem[];
  gateways: GatewayItem[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [siteId, setSiteId] = useState(sites[0]?.id ?? "");
  const siteGates = gates.filter((g) => g.site_id === siteId);
  const siteNvrs = nvrs.filter((n) => n.site_id === siteId);
  const siteGateways = gateways.filter((g) => g.site_id === siteId);
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api("/cameras", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("Camera saved");
      onSaved();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Card className="mb-4">
      <form
        className="grid gap-3 md:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          mutation.mutate({
            site_id: form.get("site_id"),
            gate_id: form.get("gate_id"),
            name: form.get("name"),
            camera_code: form.get("camera_code"),
            direction: form.get("direction"),
            rtsp_url: form.get("rtsp_url") || null,
            onvif_ip: form.get("onvif_ip") || null,
            username: form.get("username") || null,
            password: form.get("password") || null,
            stream_type: "RTSP",
            resolution: form.get("resolution") || "1920x1080",
            source_type: form.get("source_type") || "RTSP",
            nvr_id: form.get("nvr_id") || null,
            channel: form.get("channel") || null,
            gateway_id: form.get("gateway_id") || null,
            anpr_enabled: form.get("anpr_enabled") === "on",
          });
        }}
      >
        <Field label="Name">
          <Input name="name" required />
        </Field>
        <Field label="Camera ID">
          <Input name="camera_code" required />
        </Field>
        <Field label="Site">
          <Select name="site_id" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Gate">
          <Select name="gate_id" required>
            {siteGates.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Direction">
          <Select name="direction" defaultValue="ENTRY">
            <option>ENTRY</option>
            <option>EXIT</option>
            <option>BOTH</option>
          </Select>
        </Field>
        <Field label="Resolution">
          <Input name="resolution" defaultValue="1920x1080" />
        </Field>
        <Field label="Source">
          <Select name="source_type" defaultValue="RTSP">
            <option value="RTSP">RTSP endpoint</option>
            <option value="NVR_CHANNEL">NVR channel</option>
            <option value="IP_CAMERA">Direct IP camera</option>
            <option value="ONVIF">ONVIF</option>
          </Select>
        </Field>
        <Field label="NVR">
          <Select name="nvr_id">
            <option value="">None</option>
            {siteNvrs.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="NVR channel">
          <Input name="channel" placeholder="101" />
        </Field>
        <Field label="Connectivity path">
          <Select name="gateway_id">
            <option value="">Site default</option>
            {siteGateways.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </Select>
        </Field>
        <label className="flex min-h-11 items-center gap-2 text-sm text-slate-700 md:col-span-2">
          <input name="anpr_enabled" type="checkbox" defaultChecked className="h-4 w-4" />
          ANPR enabled
        </label>
        <Field label="RTSP URL">
          <Input name="rtsp_url" placeholder="rtsp://192.168.1.64:554/Streaming/Channels/101" />
        </Field>
        <Field label="ONVIF IP">
          <Input name="onvif_ip" />
        </Field>
        <Field label="Username">
          <Input name="username" autoComplete="off" />
        </Field>
        <Field label="Password">
          <Input name="password" type="password" autoComplete="new-password" />
        </Field>
        <div className="md:col-span-2 flex gap-2">
          <Button type="submit" disabled={mutation.isPending}>
            Save
          </Button>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

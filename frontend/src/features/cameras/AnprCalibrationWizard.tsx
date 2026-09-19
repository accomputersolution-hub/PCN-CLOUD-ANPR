import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type {
  AnprCalibrationStored,
  CalibrationSummary,
  CameraCalibrateResult,
  CameraItem,
} from "../../shared/api/types";
import { Badge } from "../../shared/ui/Badge";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Select } from "../../shared/ui/Field";
import { PageHeader, Spinner } from "../../shared/ui/States";

function statusTone(status: string): "green" | "amber" | "red" | "slate" {
  if (status === "GREEN") return "green";
  if (status === "YELLOW") return "amber";
  if (status === "RED") return "red";
  return "slate";
}

function pct(n: number | undefined) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

function SummaryCard({ title, summary }: { title: string; summary: CalibrationSummary | null | undefined }) {
  if (!summary) {
    return (
      <Card>
        <p className="text-sm font-medium text-slate-700">{title}</p>
        <p className="mt-2 text-sm text-slate-500">No previous result</p>
      </Card>
    );
  }
  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-slate-700">{title}</p>
        <Badge tone={statusTone(summary.status)}>{summary.status}</Badge>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
        <dt className="text-slate-400">Score</dt>
        <dd>{pct(summary.overall_score)}</dd>
        <dt className="text-slate-400">Plate px</dt>
        <dd>
          {summary.plate_width_px != null
            ? `${Math.round(summary.plate_width_px)}×${Math.round(summary.plate_height_px ?? 0)}`
            : "—"}
        </dd>
        <dt className="text-slate-400">OCR</dt>
        <dd>
          {summary.plate_text || "—"}
          {summary.ocr_confidence != null ? ` (${pct(summary.ocr_confidence)})` : ""}
        </dd>
        <dt className="text-slate-400">ms</dt>
        <dd>{summary.processing_ms ?? "—"}</dd>
      </dl>
      {summary.reasons?.length ? (
        <ul className="mt-2 list-disc pl-4 text-xs text-slate-600">
          {summary.reasons.slice(0, 4).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function OverlayPreview({
  previewUrl,
  report,
}: {
  previewUrl: string;
  report: CameraCalibrateResult;
}) {
  const frame = (report.metrics?.frame_wh as number[] | undefined) ?? [];
  const fw = Number(frame[0]) || 1;
  const fh = Number(frame[1]) || 1;
  const vb = report.overlays?.vehicle_bbox ?? [];
  const pb = report.overlays?.plate_bbox ?? [];
  const roi = report.overlays?.roi;

  const boxStyle = (bbox: number[], color: string) => {
    if (bbox.length < 4) return null;
    const x1 = Number(bbox[0]);
    const y1 = Number(bbox[1]);
    const x2 = Number(bbox[2]);
    const y2 = Number(bbox[3]);
    return {
      left: `${(x1 / fw) * 100}%`,
      top: `${(y1 / fh) * 100}%`,
      width: `${((x2 - x1) / fw) * 100}%`,
      height: `${((y2 - y1) / fh) * 100}%`,
      borderColor: color,
    } as const;
  };
  const vStyle = boxStyle(vb, "#22c55e");
  const pStyle = boxStyle(pb, "#f59e0b");

  return (
    <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-950">
      <img src={previewUrl} alt="Calibration frame" className="block max-h-[420px] w-full object-contain" />
      {roi?.enabled ? (
        <div
          className="pointer-events-none absolute border-2 border-sky-400/90 bg-sky-400/10"
          style={{
            left: `${roi.x * 100}%`,
            top: `${roi.y * 100}%`,
            width: `${roi.w * 100}%`,
            height: `${roi.h * 100}%`,
          }}
        />
      ) : null}
      {vStyle ? (
        <div className="pointer-events-none absolute border-2" style={vStyle} title="Vehicle" />
      ) : null}
      {pStyle ? (
        <div className="pointer-events-none absolute border-2" style={pStyle} title="Plate" />
      ) : null}
    </div>
  );
}

export function AnprCalibrationWizard() {
  const { cameraId: routeCameraId } = useParams<{ cameraId: string }>();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });

  const [cameraId, setCameraId] = useState(routeCameraId ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [report, setReport] = useState<CameraCalibrateResult | null>(null);

  useEffect(() => {
    if (routeCameraId) setCameraId(routeCameraId);
  }, [routeCameraId]);

  useEffect(() => {
    if (!cameraId && cameras.data?.[0]) setCameraId(cameras.data[0].id);
  }, [cameraId, cameras.data]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const selected = useMemo(
    () => (cameras.data ?? []).find((c) => c.id === cameraId) ?? null,
    [cameras.data, cameraId],
  );

  const storedQuery = useQuery({
    queryKey: ["camera-calibration", cameraId],
    queryFn: () => api<AnprCalibrationStored | null>(`/cameras/${cameraId}/calibration`),
    enabled: Boolean(cameraId),
  });

  const calibrate = useMutation({
    mutationFn: async () => {
      if (!cameraId) throw new Error("Select a camera");
      if (!file) throw new Error("Upload a test frame first");
      const fd = new FormData();
      fd.append("file", file);
      return api<CameraCalibrateResult>(`/cameras/${cameraId}/calibrate`, {
        method: "POST",
        body: fd,
      });
    },
    onSuccess: (data) => {
      setReport(data);
      void queryClient.invalidateQueries({ queryKey: ["cameras"] });
      void queryClient.invalidateQueries({ queryKey: ["camera-calibration", cameraId] });
      toast.success(`Calibration ${data.status}`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Calibration failed"),
  });

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(f);
    setPreviewUrl(f ? URL.createObjectURL(f) : null);
    setReport(null);
  };

  if (cameras.isLoading) return <Spinner label="Loading cameras" />;

  const latestStored = report?.anpr_calibration.latest ?? storedQuery.data?.latest;
  const previousStored = report?.previous ?? storedQuery.data?.previous;

  return (
    <div>
      <PageHeader
        title="ANPR Calibration Wizard"
        subtitle="Upload a gate test frame — no live RTSP stream required. Live capture is optional when the camera is reachable. Scores use engineering targets — not a guaranteed accuracy claim."
        actions={
          <Link to="/cameras" className="text-sm text-slate-600 underline">
            Back to cameras
          </Link>
        }
      />

      <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <span className="font-semibold text-slate-700">Mode A (active):</span> uploaded / phone-captured
        test frame.{" "}
        <span className="font-semibold text-slate-700">Mode B:</span> live camera frame when RTSP is
        available (not required).
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Field label="Camera">
            <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              {(cameras.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.anpr_roi?.enabled ? " · zone ON" : ""}
                </option>
              ))}
            </Select>
          </Field>
          {selected ? (
            <p className="mt-1 text-xs text-slate-500">
              {selected.site_name ?? "—"} · {selected.gate_name ?? "—"} · ROI{" "}
              {selected.anpr_roi?.enabled ? "ON" : "OFF"}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={onFile}
            />
            <Button type="button" variant="secondary" onClick={() => fileRef.current?.click()}>
              Upload / capture frame
            </Button>
            <Button
              type="button"
              onClick={() => void calibrate.mutateAsync()}
              disabled={!file || !cameraId || calibrate.isPending}
            >
              {calibrate.isPending ? "Running…" : "Run calibration"}
            </Button>
          </div>
          {file ? <p className="mt-2 text-xs text-slate-500">{file.name}</p> : null}
        </Card>

        <div className="grid gap-3 sm:grid-cols-2">
          <SummaryCard title="Latest" summary={latestStored} />
          <SummaryCard title="Previous" summary={previousStored} />
        </div>
      </div>

      {previewUrl ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <p className="mb-2 text-sm font-medium text-slate-700">Frame overlay</p>
            {report ? (
              <OverlayPreview previewUrl={previewUrl} report={report} />
            ) : (
              <img
                src={previewUrl}
                alt="Preview"
                className="max-h-[420px] w-full rounded-lg border border-slate-200 object-contain"
              />
            )}
            <p className="mt-2 text-xs text-slate-500">
              Green = vehicle · Amber = plate · Cyan = ANPR Zone (if configured)
            </p>
          </Card>

          {report ? (
            <Card>
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-slate-700">Result</p>
                <Badge tone={statusTone(report.status)}>{report.status}</Badge>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Overall {pct(report.overall_score)} · {Number(report.metrics.processing_ms ?? 0)} ms
              </p>

              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                {Object.entries(report.component_scores).map(([k, v]) => (
                  <div key={k} className="rounded border border-slate-100 px-2 py-1">
                    <div className="text-slate-400">{k.replace(/_/g, " ")}</div>
                    <div className="font-medium text-slate-700">{pct(v)}</div>
                  </div>
                ))}
              </div>

              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
                <dt className="text-slate-400">Plate size</dt>
                <dd>
                  {report.metrics.plate_width_px != null
                    ? `${Math.round(Number(report.metrics.plate_width_px))}×${Math.round(
                        Number(report.metrics.plate_height_px ?? 0),
                      )} px`
                    : "—"}
                </dd>
                <dt className="text-slate-400">Brightness</dt>
                <dd>{report.metrics.brightness != null ? String(report.metrics.brightness) : "—"}</dd>
                <dt className="text-slate-400">Sharpness</dt>
                <dd>{report.metrics.sharpness != null ? String(report.metrics.sharpness) : "—"}</dd>
                <dt className="text-slate-400">OCR plate</dt>
                <dd>{String(report.metrics.plate_text || "—")}</dd>
              </dl>

              {report.reasons.length ? (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Findings</p>
                  <ul className="mt-1 list-disc pl-4 text-sm text-slate-700">
                    {report.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {report.guidance.length ? (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Tips</p>
                  <ul className="mt-1 list-disc pl-4 text-sm text-slate-700">
                    {report.guidance.map((g) => (
                      <li key={g}>{g}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {report.targets?.note ? (
                <p className="mt-3 text-xs text-slate-400">{String(report.targets.note)}</p>
              ) : null}
            </Card>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

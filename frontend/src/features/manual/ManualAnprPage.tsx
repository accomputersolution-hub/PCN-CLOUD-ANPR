import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type {
  AnprRoi,
  CameraItem,
  EventItem,
  ManualAnprAnalyzeResult,
  ManualAnprDetection,
} from "../../shared/api/types";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { PageHeader, Spinner } from "../../shared/ui/States";

type Step = "capture" | "review" | "done";

type NormRect = { x: number; y: number; w: number; h: number };

function clamp01(n: number) {
  return Math.max(0, Math.min(1, n));
}

function normalizeDrag(a: { x: number; y: number }, b: { x: number; y: number }): NormRect {
  const x1 = Math.min(a.x, b.x);
  const y1 = Math.min(a.y, b.y);
  const x2 = Math.max(a.x, b.x);
  const y2 = Math.max(a.y, b.y);
  return {
    x: clamp01(x1),
    y: clamp01(y1),
    w: clamp01(x2 - x1),
    h: clamp01(y2 - y1),
  };
}

export function ManualAnprPage() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const previewWrapRef = useRef<HTMLDivElement>(null);

  const [cameraId, setCameraId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("capture");
  const [analysis, setAnalysis] = useState<ManualAnprAnalyzeResult | null>(null);
  const [plate, setPlate] = useState("");
  const [selectedDetectionIdx, setSelectedDetectionIdx] = useState(0);
  const [direction, setDirection] = useState<"ENTRY" | "EXIT">("ENTRY");
  const [created, setCreated] = useState<EventItem | null>(null);
  const [zoneEdit, setZoneEdit] = useState(false);
  const [draftRoi, setDraftRoi] = useState<NormRect | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);

  const selectedCamera = useMemo(
    () => (cameras.data ?? []).find((c) => c.id === cameraId) ?? null,
    [cameras.data, cameraId],
  );

  const savedRoi = selectedCamera?.anpr_roi?.enabled ? selectedCamera.anpr_roi : null;
  const displayRoi = draftRoi ?? (savedRoi ? { x: savedRoi.x, y: savedRoi.y, w: savedRoi.w, h: savedRoi.h } : null);

  useEffect(() => {
    const first = cameras.data?.[0];
    if (!cameraId && first) setCameraId(first.id);
  }, [cameraId, cameras.data]);

  useEffect(() => {
    if (!selectedCamera) return;
    if (selectedCamera.direction === "ENTRY" || selectedCamera.direction === "EXIT") {
      setDirection(selectedCamera.direction);
    }
    const r = selectedCamera.anpr_roi;
    if (r?.enabled) {
      setDraftRoi({ x: r.x, y: r.y, w: r.w, h: r.h });
    } else {
      setDraftRoi(null);
    }
  }, [selectedCamera]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const selectedDetection = analysis?.detections?.[selectedDetectionIdx] ?? null;

  const plateCropUrl = useMemo(() => {
    const b64 =
      selectedDetection?.plate_crop_jpeg_base64 ?? analysis?.plate_crop_jpeg_base64 ?? null;
    if (!b64) return null;
    return `data:image/jpeg;base64,${b64}`;
  }, [analysis?.plate_crop_jpeg_base64, selectedDetection?.plate_crop_jpeg_base64]);

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const next = e.target.files?.[0] ?? null;
    if (previewUrl) {
      try {
        URL.revokeObjectURL(previewUrl);
      } catch {
        /* jsdom */
      }
    }
    setFile(next);
    let url: string | null = null;
    if (next && typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
      url = URL.createObjectURL(next);
    }
    setPreviewUrl(url);
    setStep("capture");
    setAnalysis(null);
    setCreated(null);
  }

  const analyze = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose an image first");
      if (!cameraId) throw new Error("Select a camera / site association");
      const data = new FormData();
      data.set("camera_id", cameraId);
      data.set("file", file);
      return api<ManualAnprAnalyzeResult>("/manual-anpr/analyze", { method: "POST", body: data });
    },
    onSuccess: (res) => {
      setAnalysis(res);
      const dets = res.detections ?? [];
      const primaryIdx = Math.max(
        0,
        dets.findIndex((d) => d.is_primary) >= 0 ? dets.findIndex((d) => d.is_primary) : 0,
      );
      const chosen = dets[primaryIdx] ?? dets[0];
      setSelectedDetectionIdx(chosen ? primaryIdx : 0);
      setPlate(chosen?.plate || res.detected_plate || res.normalized_plate || res.raw_ocr || "");
      setStep("review");
      const n = res.detection_count ?? dets.length;
      toast.success(
        n > 1
          ? `${n} plates detected — select one to confirm`
          : res.plate_detected
            ? "Plate detected — confirm to create event"
            : "No plate detected — you can still edit and confirm",
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const saveRoi = useMutation({
    mutationFn: async (roi: AnprRoi | null) => {
      if (!cameraId) throw new Error("Select a camera");
      return api<CameraItem>(`/cameras/${cameraId}`, {
        method: "PATCH",
        body: JSON.stringify({ anpr_roi: roi }),
      });
    },
    onSuccess: (cam) => {
      void queryClient.invalidateQueries({ queryKey: ["cameras"] });
      if (cam.anpr_roi?.enabled) {
        setDraftRoi({ x: cam.anpr_roi.x, y: cam.anpr_roi.y, w: cam.anpr_roi.w, h: cam.anpr_roi.h });
        toast.success("ANPR Zone saved for this camera");
      } else {
        setDraftRoi(null);
        toast.success("ANPR Zone cleared");
      }
      setZoneEdit(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const confirm = useMutation({
    mutationFn: async () => {
      if (!analysis) throw new Error("Run ANPR first");
      return api<EventItem>("/manual-anpr/confirm", {
        method: "POST",
        body: JSON.stringify({
          capture_id: analysis.capture_id,
          plate_text: plate.trim(),
          direction,
          ocr_confidence: selectedDetection?.ocr_confidence ?? analysis.ocr_confidence,
          plate_confidence: selectedDetection?.plate_confidence ?? analysis.plate_confidence,
          combined_confidence:
            selectedDetection?.combined_confidence ??
            selectedDetection?.confidence ??
            analysis.combined_confidence,
        }),
      });
    },
    onSuccess: (ev) => {
      setCreated(ev);
      setStep("done");
      toast.success(`Event ${ev.plate_normalized} (${ev.direction}) created`);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["events"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  function selectDetection(idx: number, det: ManualAnprDetection) {
    setSelectedDetectionIdx(idx);
    setPlate(det.plate || "");
  }

  async function cancelReview() {
    if (analysis?.capture_id) {
      const data = new FormData();
      data.set("capture_id", analysis.capture_id);
      try {
        await api("/manual-anpr/cancel", { method: "POST", body: data });
      } catch {
        /* ignore */
      }
    }
    setAnalysis(null);
    setStep("capture");
    toast.message("Cancelled — no event created");
  }

  function resetAll() {
    if (previewUrl) {
      try {
        URL.revokeObjectURL(previewUrl);
      } catch {
        /* jsdom */
      }
    }
    setFile(null);
    setPreviewUrl(null);
    setAnalysis(null);
    setCreated(null);
    setPlate("");
    setStep("capture");
    if (fileRef.current) fileRef.current.value = "";
  }

  const pointerToNorm = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const el = previewWrapRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    const x = (e.clientX - rect.left) / Math.max(rect.width, 1);
    const y = (e.clientY - rect.top) / Math.max(rect.height, 1);
    return { x: clamp01(x), y: clamp01(y) };
  }, []);

  function onZonePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!zoneEdit) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = pointerToNorm(e);
    dragStart.current = p;
    setDraftRoi({ x: p.x, y: p.y, w: 0.01, h: 0.01 });
  }

  function onZonePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!zoneEdit || !dragStart.current) return;
    setDraftRoi(normalizeDrag(dragStart.current, pointerToNorm(e)));
  }

  function onZonePointerUp() {
    dragStart.current = null;
  }

  if (cameras.isLoading) return <Spinner />;

  return (
    <div>
      <PageHeader
        title="Manual ANPR"
        subtitle="Upload or capture a vehicle photo. OCR runs on the server — confirm before creating an ENTRY/EXIT event."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="font-semibold">1. Capture</h2>
          <div className="mt-4 space-y-3">
            <Field label="Camera (site association)">
              <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)} disabled={step !== "capture"}>
                {(cameras.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.direction}
                    {c.anpr_roi?.enabled ? " · zone ON" : ""}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Photo">
              <Input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/bmp,image/*"
                capture="environment"
                onChange={onFileChange}
                disabled={step !== "capture"}
              />
            </Field>
            {previewUrl ? (
              <div
                ref={previewWrapRef}
                className={`relative max-h-72 w-full overflow-hidden rounded-xl bg-slate-50 ${
                  zoneEdit ? "cursor-crosshair ring-2 ring-emerald-500" : ""
                }`}
                onPointerDown={onZonePointerDown}
                onPointerMove={onZonePointerMove}
                onPointerUp={onZonePointerUp}
                onPointerCancel={onZonePointerUp}
              >
                <img
                  src={previewUrl}
                  alt="Preview"
                  className="max-h-72 w-full object-contain"
                  draggable={false}
                />
                {displayRoi && displayRoi.w > 0.005 && displayRoi.h > 0.005 ? (
                  <div
                    className="pointer-events-none absolute border-2 border-emerald-400 bg-emerald-400/15"
                    style={{
                      left: `${displayRoi.x * 100}%`,
                      top: `${displayRoi.y * 100}%`,
                      width: `${displayRoi.w * 100}%`,
                      height: `${displayRoi.h * 100}%`,
                    }}
                  />
                ) : null}
              </div>
            ) : null}

            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              <p className="font-medium text-slate-800">ANPR Zone (gate ROI)</p>
              <p className="mt-1 text-xs text-slate-600">
                {savedRoi
                  ? "Zone enabled — only vehicles intersecting this rectangle get plate/OCR."
                  : "No zone — full-frame behavior (legacy)."}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!previewUrl || step !== "capture"}
                  onClick={() => setZoneEdit((v) => !v)}
                >
                  {zoneEdit ? "Stop drawing" : "Draw zone"}
                </Button>
                <Button
                  type="button"
                  disabled={!draftRoi || !cameraId || saveRoi.isPending || draftRoi.w < 0.02 || draftRoi.h < 0.02}
                  onClick={() =>
                    saveRoi.mutate({
                      enabled: true,
                      x: draftRoi!.x,
                      y: draftRoi!.y,
                      w: draftRoi!.w,
                      h: draftRoi!.h,
                    })
                  }
                >
                  Save zone
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={!cameraId || (!savedRoi && !draftRoi) || saveRoi.isPending}
                  onClick={() => saveRoi.mutate(null)}
                >
                  Clear ROI
                </Button>
              </div>
              {zoneEdit ? (
                <p className="mt-2 text-xs text-emerald-700">Drag on the preview to set the gate rectangle, then Save.</p>
              ) : null}
            </div>

            <Button type="button" disabled={!file || analyze.isPending || step !== "capture"} onClick={() => analyze.mutate()}>
              {analyze.isPending ? "Running ANPR…" : "Run ANPR"}
            </Button>
          </div>
        </Card>

        <Card>
          <h2 className="font-semibold">2. Confirm</h2>
          {step === "capture" && !analysis ? (
            <p className="mt-4 text-sm text-slate-600">Run ANPR to review the detected plate. No event is created until you confirm.</p>
          ) : null}

          {analysis && step === "review" ? (
            <div className="mt-4 space-y-3">
              {analysis.anpr_roi_enabled ? (
                <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
                  ANPR Zone filter ON — YOLO vehicles {analysis.total_yolo_vehicles ?? "?"} · in zone{" "}
                  {analysis.roi_vehicles ?? "?"} · ignored {analysis.ignored_outside_roi ?? "?"}
                </p>
              ) : (
                <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                  ANPR Zone filter OFF (full frame)
                </p>
              )}

              {(() => {
                const m = analysis.registry_match;
                const plate =
                  selectedDetection?.plate || analysis.normalized_plate || analysis.detected_plate || "";
                if (!plate && !m) return null;
                if (m?.known) {
                  return (
                    <div className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-3 text-sm text-teal-950">
                      <p className="text-xs font-semibold uppercase tracking-wide text-teal-700">Known</p>
                      <p className="mt-1 font-mono text-lg font-semibold">{m.plate_normalized || plate}</p>
                      <p className="mt-1">
                        Status: <span className="font-semibold capitalize">{m.status}</span>
                      </p>
                      {m.person_name ? <p>Name: {m.person_name}</p> : null}
                      {m.flat_room_unit ? <p>Flat: {m.flat_room_unit}</p> : null}
                    </div>
                  );
                }
                return (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-950">
                    <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Unknown</p>
                    <p className="mt-1 font-mono text-lg font-semibold">{plate || "—"}</p>
                    <p className="mt-1">Status: Unknown</p>
                    {plate && analysis.site_id ? (
                      <Link
                        to={`/vehicles?tab=registry&site_id=${encodeURIComponent(analysis.site_id)}&plate=${encodeURIComponent(plate)}`}
                        className="mt-2 inline-flex text-sm font-semibold text-amber-900 underline"
                      >
                        Register Vehicle
                      </Link>
                    ) : null}
                  </div>
                );
              })()}

              {plateCropUrl ? (
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Plate crop</p>
                  <img
                    key={`plate-crop-${selectedDetectionIdx}`}
                    src={plateCropUrl}
                    alt="Plate crop"
                    className="mt-1 max-h-28 rounded-lg border border-slate-200 bg-white object-contain"
                  />
                </div>
              ) : null}
              {(analysis.detections?.length ?? 0) > 0 ? (
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    Detections ({analysis.detection_count ?? analysis.detections?.length ?? 0})
                  </p>
                  <ul className="mt-2 space-y-1">
                    {(analysis.detections ?? []).map((det, idx) => {
                      const active = idx === selectedDetectionIdx;
                      return (
                        <li key={`${det.plate}-${idx}`}>
                          <button
                            type="button"
                            onClick={() => selectDetection(idx, det)}
                            className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm ${
                              active
                                ? "border-emerald-500 bg-emerald-50 text-emerald-900"
                                : "border-slate-200 bg-white text-slate-800 hover:border-slate-300"
                            }`}
                          >
                            <span className="font-mono font-semibold tracking-wide">{det.plate}</span>
                            <span className="text-xs text-slate-500">
                              {det.is_primary ? "primary · " : ""}
                              conf {(det.combined_confidence || det.confidence || 0).toFixed(2)}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <dt className="text-slate-500">OCR confidence</dt>
                  <dd className="font-medium">
                    {(selectedDetection?.ocr_confidence ?? analysis.ocr_confidence).toFixed(4)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Plate confidence</dt>
                  <dd className="font-medium">
                    {(selectedDetection?.plate_confidence ?? analysis.plate_confidence).toFixed(4)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Combined</dt>
                  <dd className="font-medium">
                    {(
                      selectedDetection?.combined_confidence ??
                      selectedDetection?.confidence ??
                      analysis.combined_confidence
                    ).toFixed(4)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Indian pattern</dt>
                  <dd className="font-medium">
                    {(selectedDetection?.matches_indian_pattern ?? analysis.matches_indian_pattern)
                      ? "Yes"
                      : "No"}
                  </dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-slate-500">Plate detector</dt>
                  <dd className="font-medium">
                    mode={analysis.plate_detector_mode ?? "opencv"} · used=
                    {analysis.plate_detector_used ?? analysis.plate_detector_mode ?? "opencv"}
                  </dd>
                  {analysis.ai_detector_note ? (
                    <p className="mt-1 text-xs text-amber-700">{analysis.ai_detector_note}</p>
                  ) : null}
                </div>
                <div className="col-span-2">
                  <dt className="text-slate-500">Processing</dt>
                  <dd className="font-medium">{analysis.processing_ms} ms</dd>
                </div>
              </dl>
              <Field label="Detected plate (editable)">
                <Input value={plate} onChange={(e) => setPlate(e.target.value)} required />
              </Field>
              <Field label="Direction">
                <Select value={direction} onChange={(e) => setDirection(e.target.value as "ENTRY" | "EXIT")}>
                  <option value="ENTRY">ENTRY</option>
                  <option value="EXIT">EXIT</option>
                </Select>
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={!plate.trim() || confirm.isPending} onClick={() => confirm.mutate()}>
                  {confirm.isPending ? "Creating…" : "Confirm event"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => void cancelReview()}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}

          {step === "done" && created ? (
            <div className="mt-4 space-y-2 text-sm">
              <p className="font-semibold text-emerald-700">Event created</p>
              <p>
                {created.plate_normalized} · {created.direction} · source {created.source_type}
              </p>
              {created.operator_user_id ? <p className="text-slate-500">Operator: {created.operator_user_id}</p> : null}
              <Button type="button" onClick={resetAll}>
                Capture another
              </Button>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

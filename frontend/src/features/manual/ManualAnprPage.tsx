import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type { CameraItem, EventItem, ManualAnprAnalyzeResult } from "../../shared/api/types";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { PageHeader, Spinner } from "../../shared/ui/States";

type Step = "capture" | "review" | "done";

export function ManualAnprPage() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const [cameraId, setCameraId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("capture");
  const [analysis, setAnalysis] = useState<ManualAnprAnalyzeResult | null>(null);
  const [plate, setPlate] = useState("");
  const [direction, setDirection] = useState<"ENTRY" | "EXIT">("ENTRY");
  const [created, setCreated] = useState<EventItem | null>(null);

  useEffect(() => {
    const first = cameras.data?.[0];
    if (!cameraId && first) setCameraId(first.id);
  }, [cameraId, cameras.data]);

  useEffect(() => {
    const selected = (cameras.data ?? []).find((c) => c.id === cameraId);
    if (!selected) return;
    if (selected.direction === "ENTRY" || selected.direction === "EXIT") {
      setDirection(selected.direction);
    }
  }, [cameraId, cameras.data]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const plateCropUrl = useMemo(() => {
    if (!analysis?.plate_crop_jpeg_base64) return null;
    return `data:image/jpeg;base64,${analysis.plate_crop_jpeg_base64}`;
  }, [analysis]);

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
      setPlate(res.detected_plate || res.normalized_plate || res.raw_ocr || "");
      setStep("review");
      toast.success(res.plate_detected ? "Plate detected — confirm to create event" : "No plate detected — you can still edit and confirm");
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
          ocr_confidence: analysis.ocr_confidence,
          plate_confidence: analysis.plate_confidence,
          combined_confidence: analysis.combined_confidence,
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
              <img src={previewUrl} alt="Preview" className="max-h-64 w-full rounded-xl object-contain bg-slate-50" />
            ) : null}
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
              {plateCropUrl ? (
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Plate crop</p>
                  <img src={plateCropUrl} alt="Plate crop" className="mt-1 max-h-28 rounded-lg border border-slate-200 bg-white object-contain" />
                </div>
              ) : null}
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <dt className="text-slate-500">OCR confidence</dt>
                  <dd className="font-medium">{analysis.ocr_confidence.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Plate confidence</dt>
                  <dd className="font-medium">{analysis.plate_confidence.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Combined</dt>
                  <dd className="font-medium">{analysis.combined_confidence.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Indian pattern</dt>
                  <dd className="font-medium">{analysis.matches_indian_pattern ? "Yes" : "No"}</dd>
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

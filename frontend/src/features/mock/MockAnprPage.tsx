import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type { CameraItem, EventItem } from "../../shared/api/types";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { PageHeader, Spinner } from "../../shared/ui/States";

export function MockAnprPage() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api<CameraItem[]>("/cameras") });
  const queryClient = useQueryClient();
  const [plate, setPlate] = useState("MH12AB1234");
  const [cameraId, setCameraId] = useState("");
  const [direction, setDirection] = useState("ENTRY");

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

  const create = useMutation({
    mutationFn: () =>
      api<EventItem>("/mock/events", {
        method: "POST",
        body: JSON.stringify({
          camera_id: cameraId || cameras.data?.[0]?.id,
          plate_text: plate,
          direction,
        }),
      }),
    onSuccess: (ev) => {
      toast.success(`Event ${ev.plate_normalized} (${ev.direction})`);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["events"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    if (!data.get("camera_id")) data.set("camera_id", cameras.data?.[0]?.id ?? "");
    try {
      await api("/mock/upload", { method: "POST", body: data });
      toast.success("Snapshot event created");
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Upload failed");
    }
  }

  if (cameras.isLoading) return <Spinner />;

  return (
    <div>
      <PageHeader
        title="Mock ANPR"
        subtitle="Generate events without a live camera. The browser never runs ANPR inference."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="font-semibold">Manual plate</h2>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="Plate">
              <Input value={plate} onChange={(e) => setPlate(e.target.value)} required />
            </Field>
            <Field label="Camera">
              <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
                {(cameras.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.direction})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Direction">
              <Select value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option>ENTRY</option>
                <option>EXIT</option>
              </Select>
            </Field>
            <Button type="submit" disabled={create.isPending}>
              Create event
            </Button>
          </form>
        </Card>
        <Card>
          <h2 className="font-semibold">Upload image or video frame</h2>
          <form className="mt-4 space-y-3" onSubmit={upload}>
            <Field label="Plate text">
              <Input name="plate_text" defaultValue="MH14CD5678" required />
            </Field>
            <Field label="Camera">
              <Select name="camera_id">
                {(cameras.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="File">
              <Input name="file" type="file" accept="image/*,video/*" required />
            </Field>
            <Button type="submit">Upload and create event</Button>
          </form>
        </Card>
      </div>
    </div>
  );
}

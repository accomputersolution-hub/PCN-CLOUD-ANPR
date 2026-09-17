import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type { ReportResponse } from "../../shared/api/types";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { PageHeader, Spinner } from "../../shared/ui/States";
import { useState } from "react";

export function ReportsPage() {
  const [kind, setKind] = useState("daily");
  const query = useQuery({
    queryKey: ["reports", kind],
    queryFn: () => api<ReportResponse>(`/reports/summary?kind=${kind}`),
  });

  async function exportCsv() {
    const token = localStorage.getItem("pcn_access");
    const res = await fetch(`/api/v1/reports/export?kind=${kind}`, {
      headers: { Authorization: `Bearer ${token ?? ""}` },
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `pcn-${kind}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <PageHeader
        title="Reports"
        subtitle="CSV now. PDF/Excel exporters can plug into the same backend later."
        actions={
          <div className="flex gap-2">
            <select
              className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="daily">Daily</option>
              <option value="camera">Camera-wise</option>
              <option value="gate">Gate-wise</option>
              <option value="inside">Currently inside</option>
            </select>
            <Button type="button" variant="secondary" onClick={() => void exportCsv()}>
              Export CSV
            </Button>
          </div>
        }
      />
      {query.isLoading ? <Spinner /> : null}
      <Card>
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="py-2">Label</th>
              <th>Entries</th>
              <th>Exits</th>
              <th>Detections</th>
            </tr>
          </thead>
          <tbody>
            {(query.data?.rows ?? []).map((row) => (
              <tr key={row.label} className="border-t border-slate-100">
                <td className="py-2 font-medium">{row.label}</td>
                <td>{row.entries}</td>
                <td>{row.exits}</td>
                <td>{row.detections}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

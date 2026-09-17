import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../shared/api/client";
import type { GateItem, OrganizationItem, SiteItem } from "../../shared/api/types";
import { Button } from "../../shared/ui/Button";
import { Card } from "../../shared/ui/Card";
import { Field, Input, Select } from "../../shared/ui/Field";
import { PageHeader, Spinner } from "../../shared/ui/States";
import { useAuth } from "../../shared/auth/AuthProvider";

export function SitesPage() {
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => api<OrganizationItem[]>("/organizations") });
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<SiteItem[]>("/sites") });
  const gates = useQuery({ queryKey: ["gates"], queryFn: () => api<GateItem[]>("/gates") });
  const canWrite = hasRole("SUPER_ADMIN", "ORG_ADMIN", "SITE_MANAGER");

  const createSite = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/sites", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("Site created");
      void queryClient.invalidateQueries({ queryKey: ["sites"] });
    },
  });
  const createGate = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/gates", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success("Gate created");
      void queryClient.invalidateQueries({ queryKey: ["gates"] });
    },
  });

  if (sites.isLoading) return <Spinner />;

  return (
    <div>
      <PageHeader title="Sites & gates" subtitle="Default timezone is Asia/Kolkata and is configurable per site." />
      <div className="grid gap-4 lg:grid-cols-2">
        {(sites.data ?? []).map((site) => (
          <Card key={site.id}>
            <p className="font-semibold">{site.name}</p>
            <p className="text-sm text-slate-500">{site.address}</p>
            <p className="mt-2 text-xs text-slate-500">Timezone {site.timezone}</p>
            <ul className="mt-3 space-y-1 text-sm">
              {(gates.data ?? [])
                .filter((g) => g.site_id === site.id)
                .map((g) => (
                  <li key={g.id}>
                    {g.name} · {g.mode}
                  </li>
                ))}
            </ul>
          </Card>
        ))}
      </div>
      {canWrite ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Card>
            <h2 className="font-semibold">Add site</h2>
            <form
              className="mt-3 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                createSite.mutate({
                  organization_id: f.get("organization_id") || undefined,
                  name: f.get("name"),
                  address: f.get("address"),
                  timezone: f.get("timezone") || "Asia/Kolkata",
                });
              }}
            >
              {hasRole("SUPER_ADMIN") ? (
                <Field label="Organization">
                  <Select name="organization_id">
                    {(orgs.data ?? []).map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
              <Field label="Name">
                <Input name="name" required />
              </Field>
              <Field label="Address">
                <Input name="address" />
              </Field>
              <Field label="Timezone">
                <Input name="timezone" defaultValue="Asia/Kolkata" />
              </Field>
              <Button type="submit">Create site</Button>
            </form>
          </Card>
          <Card>
            <h2 className="font-semibold">Add gate</h2>
            <form
              className="mt-3 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                createGate.mutate({
                  site_id: f.get("site_id"),
                  name: f.get("name"),
                  mode: f.get("mode"),
                });
              }}
            >
              <Field label="Site">
                <Select name="site_id">
                  {(sites.data ?? []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Name">
                <Input name="name" required />
              </Field>
              <Field label="Mode">
                <Select name="mode" defaultValue="MIXED">
                  <option>ENTRY</option>
                  <option>EXIT</option>
                  <option>MIXED</option>
                </Select>
              </Field>
              <Button type="submit">Create gate</Button>
            </form>
          </Card>
        </div>
      ) : null}
    </div>
  );
}

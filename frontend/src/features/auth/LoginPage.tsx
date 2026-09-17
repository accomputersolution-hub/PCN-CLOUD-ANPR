import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "../../shared/auth/AuthProvider";
import { Button } from "../../shared/ui/Button";
import { Field, Input } from "../../shared/ui/Field";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@pcncloud.in");
  const [password, setPassword] = useState("ChangeMe@12345");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await login(email, password);
      toast.success("Signed in");
      navigate("/");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      <div>
        <h2 className="text-xl font-semibold text-ink-900">Sign in to ANPR</h2>
        <p className="mt-1 text-sm text-slate-500">
          The browser is for operations only. ANPR processing runs on the edge, not in this app.
        </p>
      </div>
      <Field label="Email">
        <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" />
      </Field>
      <Field label="Password">
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
        />
      </Field>
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
      <p className="text-xs leading-5 text-slate-500">
        Demo: <code>admin@pcncloud.in</code> / <code>ChangeMe@12345</code>
        <br />
        Also: orgadmin, manager, guard, viewer @pcncloud.in
      </p>
    </form>
  );
}

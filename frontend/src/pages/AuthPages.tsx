import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Field, Input, Notice } from "../components/ui";
import { safeNextPath, useAuth } from "../lib/auth";
import { ApiError, friendlyMessage } from "../lib/errors";

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-7 shadow-sm">
        <p className="mb-1 text-sm font-medium text-brand-600">Multi-Tenant RAG</p>
        <h1 className="mb-5 text-xl font-semibold">{title}</h1>
        {children}
      </div>
    </div>
  );
}

export function LoginPage() {
  const { status, login, expired } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = safeNextPath(params.get("next"));

  if (status === "signed-in") return <Navigate to={next} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(next, { replace: true });
    } catch (failure) {
      // 422 covers a password too short to be valid. Neither case says which part was wrong.
      setError(failure instanceof ApiError && (failure.status === 401 || failure.status === 422) ? "Incorrect email or password." : friendlyMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Sign in">
      {expired && (
        <div className="mb-4">
          <Notice tone="info">Your session has ended. Sign in to continue where you left off.</Notice>
        </div>
      )}
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email">
          <Input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" busy={busy} className="w-full">
          Sign in
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-600">
        New here?{" "}
        <Link to="/register" className="font-medium text-brand-600 hover:underline">
          Create an account
        </Link>
      </p>
    </AuthCard>
  );
}

export function RegisterPage() {
  const { status, register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "signed-in") return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(name.trim(), email.trim(), password);
      navigate("/", { replace: true });
    } catch (failure) {
      setError(friendlyMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Create an account">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Full name">
          <Input autoComplete="name" required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Email">
          <Input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
        <Field label="Password" hint="At least 8 characters.">
          <Input type="password" autoComplete="new-password" required minLength={8} maxLength={72} value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" busy={busy} className="w-full">
          Create account
        </Button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-600">
        Already registered?{" "}
        <Link to="/login" className="font-medium text-brand-600 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthCard>
  );
}

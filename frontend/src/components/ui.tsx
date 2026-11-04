import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, SearchX, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { ApiError, friendlyMessage, NO_ACCESS } from "../lib/errors";

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700",
  secondary: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
  danger: "border border-red-200 bg-white text-red-700 hover:bg-red-50",
  ghost: "text-slate-600 hover:bg-slate-100",
};

export function Button({ variant = "primary", busy, className = "", children, disabled, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; busy?: boolean }) {
  return (
    <button
      type="button"
      {...props}
      disabled={disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_STYLES[variant]} ${className}`}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

/** Shows an action the user can't take, with the reason next to it. */
export function DisabledAction({ label, reason }: { label: string; reason: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button variant="secondary" disabled>
        {label}
      </Button>
      <span className="text-xs text-slate-500">{reason}</span>
    </span>
  );
}

const CONTROL = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 disabled:bg-slate-100 disabled:text-slate-500";

export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
export const Textarea = (props: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} className={`${CONTROL} ${props.className ?? ""}`} />;

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageTitle({ children, description, actions }: { children: ReactNode; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{children}</h1>
        {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

const BADGE_STYLES = {
  neutral: "bg-slate-100 text-slate-700",
  info: "bg-brand-100 text-brand-700",
  success: "bg-emerald-100 text-emerald-800",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-red-100 text-red-800",
};

export type BadgeTone = keyof typeof BADGE_STYLES;

export const Badge = ({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) => <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${BADGE_STYLES[tone]}`}>{children}</span>;

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-slate-600">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function Notice({ tone, children }: { tone: "info" | "error" | "success" | "warning"; children: ReactNode }) {
  const styles = {
    info: ["border-brand-100 bg-brand-50 text-brand-700", Info],
    error: ["border-red-200 bg-red-50 text-red-800", AlertTriangle],
    success: ["border-emerald-200 bg-emerald-50 text-emerald-800", CheckCircle2],
    warning: ["border-amber-200 bg-amber-50 text-amber-800", AlertTriangle],
  } as const;
  const [style, Icon] = styles[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${style}`}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function EmptyState({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
      <p>{children}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function NotFoundPanel({ backTo, backLabel = "Go back" }: { backTo?: string; backLabel?: string }) {
  return (
    <div className="mx-auto mt-10 max-w-md rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
      <SearchX className="mx-auto mb-3 size-8 text-slate-400" aria-hidden />
      <h1 className="text-lg font-semibold">{NO_ACCESS}</h1>
      {backTo && (
        <Link to={backTo} className="mt-4 inline-block text-sm font-medium text-brand-600 hover:underline">
          {backLabel}
        </Link>
      )}
    </div>
  );
}

/** A failed load: "not found" for 403 and 404, and a friendly message with a retry for anything else. */
export function LoadError({ error, retry, backTo }: { error: unknown; retry?: () => void; backTo?: string }) {
  if (error instanceof ApiError && error.isNoAccess) return <NotFoundPanel backTo={backTo} />;
  return (
    <Notice tone="error">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>{friendlyMessage(error)}</span>
        {retry && (
          <Button variant="secondary" onClick={retry}>
            <RefreshCw className="size-4" aria-hidden />
            Try again
          </Button>
        )}
      </div>
    </Notice>
  );
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-900/40" />
        <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[92vw] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg bg-white p-6 shadow-xl">
          <div className="mb-4 flex items-start justify-between gap-4">
            <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
            <Dialog.Close aria-label="Close" className="rounded p-1 text-slate-500 hover:bg-slate-100">
              <X className="size-4" aria-hidden />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel, busy, error, onConfirm, onClose }: { open: boolean; title: string; body: ReactNode; confirmLabel: string; busy?: boolean; error?: unknown; onConfirm: () => void; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="space-y-4 text-sm text-slate-700">
        <div>{body}</div>
        {error != null && <Notice tone="error">{friendlyMessage(error)}</Notice>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

const ToastContext = createContext<(message: string) => void>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; message: string }[]>([]);
  const nextId = useRef(0);
  const show = useCallback((message: string) => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 5000);
  }, []);
  const value = useMemo(() => show, [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-50 space-y-2">
        {toasts.map((toast) => (
          <div key={toast.id} role="status" className="flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm text-white shadow-lg">
            <CheckCircle2 className="size-4 text-emerald-400" aria-hidden />
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

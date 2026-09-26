import type { ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-[var(--radius-card)] border border-hairline bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-start gap-4 border-b border-hairline px-5 py-3.5">
          <div className="min-w-0 flex-1">
            {title && <h2 className="text-[13px] font-semibold tracking-wide">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[12px] text-ink-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

type ButtonTone = "primary" | "default" | "quiet" | "danger";

export function Button({
  tone = "default",
  children,
  className,
  ...rest
}: { tone?: ButtonTone } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const tones: Record<ButtonTone, string> = {
    primary:
      "bg-accent text-on-accent hover:bg-[color-mix(in_srgb,var(--color-accent)_88%,white)] font-semibold",
    default: "bg-raised text-ink border border-hairline-strong hover:border-ink-faint",
    quiet: "text-ink-muted hover:text-ink hover:bg-raised",
    danger:
      "text-bad border border-[color-mix(in_srgb,var(--color-bad)_40%,transparent)] hover:bg-[color-mix(in_srgb,var(--color-bad)_12%,transparent)]",
  };
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-[13px]",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        tones[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

const BADGE_TONES = {
  ok: "text-ok border-[color-mix(in_srgb,var(--color-ok)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-ok)_10%,transparent)]",
  warn: "text-warn border-[color-mix(in_srgb,var(--color-warn)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-warn)_10%,transparent)]",
  bad: "text-bad border-[color-mix(in_srgb,var(--color-bad)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-bad)_10%,transparent)]",
  neutral: "text-ink-muted border-hairline-strong bg-raised",
} as const;

export type BadgeTone = keyof typeof BADGE_TONES;

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium",
        BADGE_TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

/** A label and a value on one line — the shape most of this application is. */
export function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-4 py-1">
      <dt className="w-44 shrink-0 text-[12px] text-ink-muted">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Working"
      className={cx(
        "inline-block size-3.5 animate-spin rounded-full border-2 border-hairline-strong border-t-accent",
        className,
      )}
    />
  );
}

/**
 * Progress with no percentage.
 *
 * Providers rarely report a real fraction, and spec §18 forbids inventing one.
 * Without a number this is an indeterminate sweep; with one it fills honestly.
 */
export function Progress({ fraction }: { fraction: number | null }) {
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-raised">
      {fraction === null ? (
        <div className="h-full w-1/3 animate-[sweep_1.4s_ease-in-out_infinite] rounded-full bg-accent-dim" />
      ) : (
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300"
          style={{ width: `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%` }}
        />
      )}
      <style>{`@keyframes sweep{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}`}</style>
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 py-12 text-center">
      <p className="text-[13px] text-ink-muted">{title}</p>
      {hint && <p className="max-w-sm text-[12px] text-ink-faint">{hint}</p>}
    </div>
  );
}

/** A failure, with the technical detail folded away rather than thrown out. */
export function Failure({
  message,
  technical,
  suggestions = [],
}: {
  message: string;
  technical?: string | null;
  suggestions?: string[];
}) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[color-mix(in_srgb,var(--color-bad)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-bad)_8%,transparent)] px-4 py-3">
      <p className="text-[13px] text-bad">{message}</p>
      {suggestions.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[12px] text-ink-muted">
          {suggestions.map((s) => (
            <li key={s}>— {s}</li>
          ))}
        </ul>
      )}
      {technical && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-ink-faint">Technical detail</summary>
          <pre className="selectable mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-ground px-2.5 py-2 font-mono text-[11px] text-ink-muted">
            {technical}
          </pre>
        </details>
      )}
    </div>
  );
}

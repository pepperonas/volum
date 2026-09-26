import { useCallback, useState } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Button, Failure, Spinner, cx } from "./components/ui";
import {
  CubeIcon,
  GenerateIcon,
  LibraryIcon,
  ModelsIcon,
  SettingsIcon,
  SystemIcon,
} from "./components/icons";
import { useEngineBoot } from "./lib/hooks";
import Generate from "./screens/Generate";
import Library from "./screens/Library";
import Models from "./screens/Models";
import Result from "./screens/Result";
import Settings from "./screens/Settings";
import System from "./screens/System";

export type Route =
  | { name: "generate" }
  | { name: "library" }
  | { name: "models" }
  | { name: "system" }
  | { name: "settings" }
  | { name: "job"; id: string };

export type Navigate = (route: Route) => void;

const NAV = [
  { name: "generate", label: "Generate", Icon: GenerateIcon },
  { name: "library", label: "Library", Icon: LibraryIcon },
  { name: "models", label: "Models", Icon: ModelsIcon },
  { name: "system", label: "System", Icon: SystemIcon },
  { name: "settings", label: "Settings", Icon: SettingsIcon },
] as const;

export default function App() {
  const boot = useEngineBoot();
  const [route, setRoute] = useState<Route>({ name: "generate" });
  const navigate = useCallback<Navigate>((next) => setRoute(next), []);

  if (boot.state === "failed") {
    return (
      <Starting>
        <div className="w-[34rem] max-w-full">
          <Failure
            message={boot.error.message}
            technical={boot.error.technical}
            suggestions={boot.error.suggestions}
          />
          <div className="mt-4 flex justify-center">
            <Button tone="primary" onClick={boot.retry}>
              Try again
            </Button>
          </div>
        </div>
      </Starting>
    );
  }

  if (boot.state === "loading" || !boot.value) {
    return (
      <Starting>
        <div className="flex items-center gap-3 text-[13px] text-ink-muted">
          <Spinner />
          Starting the engine…
        </div>
        <p className="mt-2 text-[12px] text-ink-faint">
          Everything runs on this machine. The first start can take a moment.
        </p>
      </Starting>
    );
  }

  return (
    <div className="flex h-full">
      <nav className="title-gap flex w-52 shrink-0 flex-col border-r border-hairline bg-surface">
        <div className="flex items-center gap-2 px-4 pb-4">
          <CubeIcon className="text-[20px] text-accent" />
          <span className="text-[15px] font-semibold tracking-tight">VOLUM</span>
        </div>
        <ul className="flex-1 space-y-0.5 px-2">
          {NAV.map(({ name, label, Icon }) => {
            // The result of a job belongs to the library it came from.
            const active = route.name === name || (name === "library" && route.name === "job");
            return (
              <li key={name}>
                <button
                  type="button"
                  onClick={() => navigate({ name })}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                    active
                      ? "bg-raised text-ink"
                      : "text-ink-muted hover:bg-raised/60 hover:text-ink",
                  )}
                >
                  <Icon className={cx("text-[17px]", active && "text-accent")} />
                  {label}
                </button>
              </li>
            );
          })}
        </ul>
        <footer className="px-4 py-3 text-[11px] text-ink-faint">
          <p className="num">engine {boot.value.version}</p>
          <p>Local only. Nothing leaves this machine.</p>
        </footer>
      </nav>

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="title-gap mx-auto max-w-5xl px-8 pb-16">
          {/* Keyed on the route so leaving a crashed screen clears the crash;
              a desktop window has no reload to fall back on. */}
          <ErrorBoundary resetKey={`${route.name}:${route.name === "job" ? route.id : ""}`}>
            {route.name === "generate" && <Generate navigate={navigate} />}
            {route.name === "library" && <Library navigate={navigate} />}
            {route.name === "models" && <Models />}
            {route.name === "system" && <System />}
            {route.name === "settings" && <Settings />}
            {route.name === "job" && <Result jobId={route.id} navigate={navigate} />}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}

function Starting({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <CubeIcon className="mb-5 text-[40px] text-accent-dim" />
      {children}
    </div>
  );
}

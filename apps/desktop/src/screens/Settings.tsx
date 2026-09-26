import { useCallback, useState } from "react";
import { api } from "../lib/engine";
import { asEngineError, useAsync } from "../lib/hooks";
import { Badge, Button, Card, Failure, Row, Spinner } from "../components/ui";

export default function Settings() {
  const settings = useAsync("settings", () => api.settings());
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<ReturnType<typeof asEngineError> | null>(null);
  // What the user just clicked, until the engine confirms it. Derived from the
  // saved value otherwise — the checkbox should not need an effect to learn
  // what the server already told us.
  const [pendingMarginal, setPendingMarginal] = useState<boolean | null>(null);

  const save = useCallback(
    async (patch: Parameters<typeof api.updateSettings>[0]) => {
      setFailure(null);
      setSaving(true);
      setSaved(false);
      try {
        await api.updateSettings(patch);
        setSaved(true);
        setToken("");
      } catch (error) {
        setFailure(asEngineError(error));
        // The click did not take: stop showing it as though it had.
        setPendingMarginal(null);
      } finally {
        setSaving(false);
        settings.reload();
      }
    },
    [settings],
  );

  if (!settings.value) {
    return (
      <Screen>
        {settings.state === "failed" ? (
          <Failure message={settings.error.message} technical={settings.error.technical} />
        ) : (
          <div className="flex items-center gap-2 py-16 text-[13px] text-ink-muted">
            <Spinner /> Loading…
          </div>
        )}
      </Screen>
    );
  }

  return (
    <Screen>
      {failure && (
        <div className="mb-5">
          <Failure
            message={failure.message}
            technical={failure.technical}
            suggestions={failure.suggestions}
          />
        </div>
      )}

      <div className="space-y-5">
        <Card
          title="Where VOLUM keeps things"
          subtitle="Models, jobs and exports all live here."
        >
          <dl>
            <Row label="Data directory">
              <span className="selectable num break-all text-[12px]">
                {settings.value.data_dir}
              </span>
            </Row>
            <Row label="Location">
              {settings.value.data_dir_is_default ? (
                <span className="text-ink-muted">the platform default</span>
              ) : (
                <Badge tone="warn">moved</Badge>
              )}
            </Row>
          </dl>
          <p className="mt-3 border-t border-hairline pt-3 text-[12px] text-ink-faint">
            A single model with its environment can be several gigabytes. Moving this directory to
            another volume is done by setting <span className="num">VOLUM_DATA_DIR</span> before
            starting VOLUM; changing it from inside the window is not built yet, because a move
            that leaves models behind is worse than no move at all.
          </p>
        </Card>

        <Card
          title="Hugging Face access"
          subtitle="Only ever used for gated model downloads that you start."
        >
          <div className="flex items-center gap-3">
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={
                settings.value.hugging_face_token_set ? "a token is stored" : "hf_…"
              }
              autoComplete="off"
              spellCheck={false}
              className="num flex-1 rounded-md border border-hairline-strong bg-ground px-2.5 py-1.5 text-[13px] selectable"
            />
            <Button
              tone="primary"
              disabled={saving || token.trim() === ""}
              onClick={() => void save({ hugging_face_token: token.trim() })}
            >
              Save
            </Button>
            {settings.value.hugging_face_token_set && (
              <Button
                tone="quiet"
                disabled={saving}
                onClick={() => void save({ clear_hugging_face_token: true })}
              >
                Remove
              </Button>
            )}
          </div>
          <p className="mt-3 text-[12px] text-ink-faint">
            Some models — DINOv3, which TRELLIS.2 depends on — require an account and accepted
            terms before their weights can be downloaded. The token is stored on this machine and
            sent only to the model host, only while downloading.
          </p>
        </Card>

        <Card title="Models this machine cannot comfortably run">
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              checked={pendingMarginal ?? settings.value.allow_marginal_models}
              disabled={saving}
              onChange={(event) => {
                setPendingMarginal(event.target.checked);
                void save({ allow_marginal_models: event.target.checked });
              }}
              className="mt-0.5 accent-[var(--color-accent)]"
            />
            <span>
              <span className="text-[13px]">Let me install them anyway</span>
              <span className="block text-[12px] text-ink-faint">
                A model whose estimated peak memory is above this machine’s will swap heavily or
                fail outright. Off by default: the honest default is not to attempt it.
              </span>
            </span>
          </label>
        </Card>

        <Card title="Privacy">
          <ul className="space-y-1 text-[13px] text-ink-muted">
            <li>No telemetry, no analytics, no crash reporting.</li>
            <li>No account, no login, no licence check.</li>
            <li>No remote inference — models run on this hardware or not at all.</li>
            <li>The network is used only to download model weights you ask for.</li>
          </ul>
          <p className="mt-3 border-t border-hairline pt-3 text-[12px] text-ink-faint">
            The engine listens on <span className="num">127.0.0.1</span> only, on a port chosen by
            the operating system, and answers nothing without the session token this window was
            given at start-up.
          </p>
        </Card>
      </div>

      {saved && !saving && (
        <p className="mt-4 text-right text-[12px] text-ok">Saved.</p>
      )}
    </Screen>
  );
}

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="py-6">
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      </header>
      {children}
    </>
  );
}

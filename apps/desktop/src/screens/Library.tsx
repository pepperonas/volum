import { useCallback, useState } from "react";
import type { Navigate } from "../App";
import { api } from "../lib/engine";
import { duration, elapsed, jobLabel, since } from "../lib/format";
import { asEngineError, useAsync } from "../lib/hooks";
import { Badge, Button, Card, Empty, Failure, Spinner, type BadgeTone } from "../components/ui";
import { TrashIcon } from "../components/icons";
import { isTerminal, type JobRecord, type JobStatus } from "../lib/types";

const STATUS_TONE: Partial<Record<JobStatus, BadgeTone>> = {
  completed: "ok",
  failed: "bad",
  cancelled: "neutral",
};

/**
 * Past work.
 *
 * Called a library rather than "projects" on purpose: a job directory already
 * holds everything the specification asks a project to hold — the record, the
 * staged inputs with their hashes, the artifacts, the quality report and the
 * asset metadata — and a separate Projects screen over the same data would be
 * a second name for one thing.
 */
export default function Library({ navigate }: { navigate: Navigate }) {
  const jobs = useAsync("jobs", () => api.jobs(100));
  const [failure, setFailure] = useState<ReturnType<typeof asEngineError> | null>(null);

  const remove = useCallback(
    async (job: JobRecord) => {
      setFailure(null);
      try {
        await api.deleteJob(job.id);
      } catch (error) {
        setFailure(asEngineError(error));
      } finally {
        jobs.reload();
      }
    },
    [jobs],
  );

  return (
    <>
      <header className="flex items-end justify-between gap-4 py-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Library</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Everything generated on this machine. Each entry is a folder you can open, copy or
            delete.
          </p>
        </div>
        <Button tone="quiet" onClick={jobs.reload}>
          Refresh
        </Button>
      </header>

      {failure && (
        <div className="mb-5">
          <Failure message={failure.message} technical={failure.technical} />
        </div>
      )}

      {jobs.state === "failed" && (
        <Failure
          message={jobs.error.message}
          technical={jobs.error.technical}
          suggestions={jobs.error.suggestions}
        />
      )}

      {!jobs.value && jobs.state === "loading" && (
        <div className="flex items-center gap-2 py-16 text-[13px] text-ink-muted">
          <Spinner /> Reading the library…
        </div>
      )}

      {jobs.value?.length === 0 && (
        <Card>
          <Empty
            title="Nothing here yet."
            hint="Generated models and their quality reports will appear here."
          />
        </Card>
      )}

      {jobs.value && jobs.value.length > 0 && (
        <Card className="overflow-hidden" >
          <ul className="divide-y divide-hairline">
            {jobs.value.map((job) => (
              <li key={job.id} className="flex items-center gap-4 py-2.5 first:pt-0 last:pb-0">
                <button
                  type="button"
                  onClick={() => navigate({ name: "job", id: job.id })}
                  className="min-w-0 flex-1 text-left"
                  disabled={job.status !== "completed"}
                >
                  <span className="flex items-center gap-2.5">
                    <span className="truncate text-[13px]">{jobLabel(job)}</span>
                    <Badge tone={STATUS_TONE[job.status] ?? "warn"}>{job.status}</Badge>
                    {job.target_size_mm !== null && (
                      <span className="num text-[11px] text-ink-faint">
                        {job.target_size_mm} mm
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-[12px] text-ink-faint">
                    <span className="num">{job.model_id}</span>
                    {job.runtime && <span className="num"> · {job.runtime}</span>}
                    {" · "}
                    {since(job.created_at)}
                    {isTerminal(job.status) && (
                      <>
                        {" · "}
                        <span className="num">
                          {duration(elapsed(job.started_at, job.finished_at))}
                        </span>
                      </>
                    )}
                  </span>
                </button>

                <Button
                  tone="quiet"
                  className="px-1.5 py-1"
                  aria-label="Delete this job"
                  disabled={!isTerminal(job.status)}
                  title={isTerminal(job.status) ? "Delete" : "Cancel it first"}
                  onClick={() => void remove(job)}
                >
                  <TrashIcon className="text-[15px]" />
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

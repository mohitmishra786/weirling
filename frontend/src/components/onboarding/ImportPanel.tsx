"use client";
import { useState } from "react";
import { api } from "../../lib/api";
import type { ImportResult } from "../../lib/types";

export type ImportKind = "feeds" | "saved";

/** Which import a file is: OPML lists feeds; HTML (Pocket) and CSV are saved articles. */
export function kindOf(fileName: string): ImportKind {
  const name = fileName.toLowerCase();
  return name.endsWith(".opml") || name.endsWith(".xml") ? "feeds" : "saved";
}

export function progressLabel(done: number, total: number): string {
  return `Importing ${done.toLocaleString("en-US")} / ${total.toLocaleString("en-US")} feeds…`;
}

/** Poll a background import until it finishes; returns feeds created (#202). */
async function waitForJob(id: string, onProgress: (label: string) => void): Promise<number> {
  for (;;) {
    const job = await api.sources.importJob(id);
    onProgress(progressLabel(job.done, job.total));
    if (job.status === "done") return job.created;
    if (job.status === "failed") throw new Error(job.error || "Import stopped part-way");
    await new Promise((r) => setTimeout(r, 1000));
  }
}

export function summarize(feeds: number, saved: number): string {
  const parts = [];
  if (feeds) parts.push(`${feeds} feed${feeds === 1 ? "" : "s"}`);
  if (saved) parts.push(`${saved} saved article${saved === 1 ? "" : "s"}`);
  return parts.length ? `Imported ${parts.join(", ")}.` : "Nothing new to import.";
}

// Drag-and-drop import (#150): Feedly/Inoreader OPML, Pocket HTML export, CSV.
// Each file is sent to the matching endpoint; per-row problems are listed,
// never fatal.
export function ImportPanel({ onImported }: { onImported?: (feeds: number, saved: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [dragging, setDragging] = useState(false);
  const [totals, setTotals] = useState({ feeds: 0, saved: 0 });
  const [errors, setErrors] = useState<{ file: string; url: string; error: string }[]>([]);
  const [failed, setFailed] = useState<string[]>([]);

  const handle = async (files: FileList | File[]) => {
    setBusy(true);
    let feeds = 0;
    let saved = 0;
    for (const file of Array.from(files)) {
      try {
        const kind = kindOf(file.name);
        const result: ImportResult =
          kind === "feeds" ? await api.sources.importFeeds(file) : await api.sources.importSaved(file);
        if (kind === "feeds") {
          // Large files import in the background; follow the job (#202).
          feeds += result.queued && result.job_id ? await waitForJob(result.job_id, setProgress) : result.created;
        } else {
          saved += result.created;
        }
        setErrors((prev) => [
          ...prev,
          ...result.errors.map((e) => ({ file: file.name, url: e.url, error: e.error })),
        ]);
      } catch (e) {
        setFailed((prev) => [...prev, `${file.name}: ${e instanceof Error ? e.message : "failed"}`]);
      }
    }
    setTotals((t) => ({ feeds: t.feeds + feeds, saved: t.saved + saved }));
    setProgress("");
    setBusy(false);
    onImported?.(feeds, saved);
  };

  return (
    <div>
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) void handle(e.dataTransfer.files);
        }}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 text-center text-sm ${
          dragging ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border-strong)] hover:border-[var(--accent)]"
        }`}
      >
        <span className="font-medium">
          {busy ? progress || "Importing…" : "Drop OPML, Pocket or CSV files here, or click to choose"}
        </span>
        <input
          type="file"
          multiple
          accept=".opml,.xml,.html,.htm,.csv"
          className="sr-only"
          disabled={busy}
          onChange={(e) => e.target.files && void handle(e.target.files)}
        />
      </label>
      <details className="mt-2 text-xs text-stone-500 dark:text-stone-400">
        <summary>How to export</summary>
        <ul className="mt-1 list-disc space-y-1 pl-5">
          <li>Feedly: Settings → Organize Sources → Export OPML.</li>
          <li>Inoreader: Preferences → Import/Export → Export as OPML.</li>
          <li>Pocket: the ril_export.html file from your Pocket data export.</li>
          <li>Instapaper, Raindrop, Readwise: their CSV export, or any CSV with url,title,tags columns.</li>
        </ul>
      </details>
      {(totals.feeds > 0 || totals.saved > 0) && (
        <p role="status" className="mt-3 text-sm font-medium">
          {summarize(totals.feeds, totals.saved)}
        </p>
      )}
      {failed.map((f) => (
        <p key={f} className="mt-1 text-sm text-red-600 dark:text-red-300">
          {f}
        </p>
      ))}
      {errors.length > 0 && (
        <details className="mt-2 text-xs text-amber-800 dark:text-amber-200">
          <summary>{errors.length} row{errors.length === 1 ? "" : "s"} skipped</summary>
          <ul className="mt-1 space-y-0.5">
            {errors.slice(0, 50).map((e, i) => (
              <li key={`${e.file}-${i}`}>
                {e.file}: {e.url || "(empty)"}, {e.error}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

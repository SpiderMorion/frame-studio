import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";

import "./App.css";

const models = [
  ["qwen-image-2512", "Qwen-Image-2512"],
  ["flux2-klein-4b", "FLUX.2 Klein 4B"],
  ["z-image-turbo", "Z-Image-Turbo"],
] as const;
const modelLabels = Object.fromEntries(models) as Record<string, string>;
const algorithms = [
  ["good-display-floyd-v1", "Good Display Floyd–Steinberg"],
  ["none", "Nearest palette"],
  ["floyd-steinberg", "Floyd–Steinberg"],
  ["atkinson", "Atkinson"],
  ["sierra-lite", "Sierra Lite"],
  ["jarvis-judice-ninke", "Jarvis–Judice–Ninke"],
  ["bayer-4x4", "Bayer 4×4"],
  ["bayer-8x8", "Bayer 8×8"],
] as const;

type ProcessingSettings = {
  brightness: number;
  contrast: number;
  saturation: number;
  gamma: number;
  sharpness: number;
  dithering: string;
  dithering_strength: number;
};

type PendingPreview = {
  draftId: string;
  settings: ProcessingSettings;
  signature: string;
};

function RangeControl({ label, value, onChange, min = -100, max = 100, step = 1 }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number }) {
  const id = `adjustment-${label.toLowerCase()}`;
  return (
    <div className="range-control">
      <span><label htmlFor={id}><b>{label}</b></label><output htmlFor={id}>{value}</output></span>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}

type ScheduleData = {
  enabled: boolean;
  mode: "interval" | "custom";
  interval_minutes: number;
  anchor_time: string;
  custom_times: string[];
  quiet_period: { enabled: boolean; start: string; end: string };
  next_runs?: string[];
};

type ScheduleResponse = Omit<ScheduleData, "quiet_period"> & {
  quiet_start: string | null;
  quiet_end: string | null;
};

const defaultSchedule: ScheduleData = {
  enabled: false,
  mode: "interval",
  interval_minutes: 60,
  anchor_time: "08:00",
  custom_times: ["08:00"],
  quiet_period: { enabled: false, start: "22:00", end: "07:00" },
};

function SchedulePage() {
  const [schedule, setSchedule] = useState(defaultSchedule);
  const [state, setState] = useState<"loading" | "ready" | "dirty" | "saving" | "saved" | "error">("loading");

  useEffect(() => {
    let active = true;
    fetch("/api/v1/schedule")
      .then((response) => {
        if (!response.ok) throw new Error("schedule load failed");
        return response.json() as Promise<ScheduleResponse>;
      })
      .then((data) => { if (active) {
        const { quiet_start, quiet_end, ...settings } = data;
        setSchedule({
          ...defaultSchedule,
          ...settings,
          quiet_period: {
            enabled: quiet_start !== null && quiet_end !== null,
            start: quiet_start ?? defaultSchedule.quiet_period.start,
            end: quiet_end ?? defaultSchedule.quiet_period.end,
          },
        });
        setState("ready");
      } })
      .catch(() => { if (active) setState("error"); });
    return () => { active = false; };
  }, []);

  function changeSchedule(next: ScheduleData) {
    setSchedule(next);
    setState("dirty");
  }

  async function save() {
    setState("saving");
    const payload = {
      enabled: schedule.enabled,
      mode: schedule.mode,
      interval_minutes: schedule.interval_minutes,
      anchor_time: schedule.anchor_time,
      custom_times: schedule.custom_times,
      quiet_start: schedule.quiet_period.enabled ? schedule.quiet_period.start : null,
      quiet_end: schedule.quiet_period.enabled ? schedule.quiet_period.end : null,
    };
    try {
      const response = await fetch("/api/v1/schedule", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("schedule save failed");
      setState("saved");
    } catch {
      setState("error");
    }
  }

  if (state === "loading") return <section className="content-page"><div className="panel state-card">Loading schedule…</div></section>;
  if (state === "error") return <section className="content-page"><div className="panel state-card error">Unable to load or save the schedule.</div></section>;

  return <section className="content-page schedule-console">
    <header className="schedule-overview panel">
      <div>
        <span className="section-kicker">AUTOMATION</span>
        <h2>Display schedule</h2>
        <p>Control when a new frame is generated and placed on the display.</p>
      </div>
      <label className="schedule-master">
        <span><strong>{schedule.enabled ? "Automation on" : "Automation off"}</strong><small>{schedule.enabled ? "Future display events are active" : "No automatic display events"}</small></span>
        <input aria-label="Schedule enabled" type="checkbox" checked={schedule.enabled} onChange={(event) => changeSchedule({ ...schedule, enabled: event.target.checked })} />
      </label>
    </header>

    <div className="schedule-grid">
      <div className="schedule-controls">
        <article className="panel schedule-card cadence-card">
          <div className="schedule-card-heading">
            <div><span className="section-kicker">CADENCE</span><h2>Display cadence</h2></div>
            <span className="schedule-summary">{schedule.mode === "interval" ? `Every ${schedule.interval_minutes} min` : `${schedule.custom_times.length} times / day`}</span>
          </div>
          <div className="cadence-mode" role="group" aria-label="Cadence mode">
            <button aria-label="Every interval" className={schedule.mode === "interval" ? "active" : ""} type="button" onClick={() => changeSchedule({ ...schedule, mode: "interval" })}><strong>Every interval</strong><span>Repeat from an anchor time</span></button>
            <button aria-label="Specific times" className={schedule.mode === "custom" ? "active" : ""} type="button" onClick={() => changeSchedule({ ...schedule, mode: "custom" })}><strong>Specific times</strong><span>Choose exact display slots</span></button>
          </div>
          {schedule.mode === "interval" ? <div className="field-grid cadence-fields">
            <label className="field">Repeat every<input aria-label="Interval minutes" type="number" min="30" value={schedule.interval_minutes} onChange={(event) => changeSchedule({ ...schedule, interval_minutes: Number(event.target.value) })} /><small>minutes</small></label>
            <label className="field">Start counting at<input aria-label="Anchor time" type="time" value={schedule.anchor_time} onChange={(event) => changeSchedule({ ...schedule, anchor_time: event.target.value })} /><small>Europe / Moscow</small></label>
          </div> : <div className="custom-times time-chips">
            {schedule.custom_times.map((time, index) => <div className="time-row" key={`${index}-${time}`}>
              <label className="field">Display time<input aria-label={`Custom time ${index + 1}`} type="time" value={time} onChange={(event) => changeSchedule({ ...schedule, custom_times: schedule.custom_times.map((item, itemIndex) => itemIndex === index ? event.target.value : item) })} /></label>
              <button className="icon-button" type="button" aria-label={`Remove ${time}`} onClick={() => changeSchedule({ ...schedule, custom_times: schedule.custom_times.filter((_, itemIndex) => itemIndex !== index) })}>×</button>
            </div>)}
            <button className="add-time" aria-label="Add time" type="button" onClick={() => changeSchedule({ ...schedule, custom_times: [...schedule.custom_times, "12:00"] })}>+ Add display time</button>
          </div>}
          <div className="lead-time"><i>−15</i><span><strong>Generation starts 15 minutes before each display</strong><small>Qwen rendering and Spectra 6 conversion happen before the selected slot.</small></span></div>
        </article>

        <article className="panel schedule-card quiet-card">
          <div className="schedule-card-heading">
            <div><span className="section-kicker">GUARDRAIL</span><h2>Quiet window</h2></div>
            <label className="inline-switch"><input aria-label="Quiet period enabled" type="checkbox" checked={schedule.quiet_period.enabled} onChange={(event) => changeSchedule({ ...schedule, quiet_period: { ...schedule.quiet_period, enabled: event.target.checked } })} /><span>{schedule.quiet_period.enabled ? "Enabled" : "Disabled"}</span></label>
          </div>
          <p>Skip display events during this window. Overnight ranges are handled automatically.</p>
          {schedule.quiet_period.enabled && <div className="field-grid quiet-fields">
            <label className="field">Starts<input aria-label="Quiet period starts" type="time" value={schedule.quiet_period.start} onChange={(event) => changeSchedule({ ...schedule, quiet_period: { ...schedule.quiet_period, start: event.target.value } })} /></label>
            <span className="quiet-arrow">→</span>
            <label className="field">Ends<input aria-label="Quiet period ends" type="time" value={schedule.quiet_period.end} onChange={(event) => changeSchedule({ ...schedule, quiet_period: { ...schedule.quiet_period, end: event.target.value } })} /></label>
            <span className="overnight-badge">Overnight</span>
          </div>}
        </article>
      </div>

      <aside className="panel timeline-panel">
        <div className="schedule-card-heading"><div><span className="section-kicker">UPCOMING</span><h2>48-hour timeline</h2></div><span className={`automation-dot ${schedule.enabled ? "active" : ""}`} /></div>
        {schedule.next_runs?.length ? <ol className="event-timeline">{schedule.next_runs.map((run) => {
          const display = new Date(run);
          const generation = new Date(display.getTime() - 15 * 60 * 1000);
          const format = (date: Date) => date.toLocaleString("en-GB", { timeZone: "Europe/Moscow", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
          return <li key={run}>
            <div className="timeline-date"><time dateTime={run}>{format(display)}</time><span>MSK</span></div>
            <div className="timeline-events"><span className="generation-event"><i />Generate <time dateTime={generation.toISOString()}>{format(generation).split(", ")[1]}</time></span><span className="display-event"><i />Display <time dateTime={run}>{format(display).split(", ")[1]}</time></span></div>
          </li>;
        })}</ol> : <div className="timeline-empty"><strong>No upcoming events</strong><span>Enable automation and save a cadence to populate the timeline.</span></div>}
      </aside>
    </div>

    <div className="schedule-save-bar">
      <div><span className={`save-indicator ${state}`} /><strong>{state === "saved" ? "Schedule saved" : state === "dirty" ? "Unsaved changes" : "Schedule is up to date"}</strong><small>{state === "saved" ? "The scheduler will use these settings." : state === "dirty" ? "Review the timeline, then apply the new configuration." : "Change any setting to enable saving."}</small></div>
      <button className="primary save-schedule" type="button" onClick={save} disabled={state !== "dirty"}>{state === "saving" ? "Saving…" : "Save schedule"}</button>
    </div>
  </section>;
}

type Generation = {
  id: string;
  status: string;
  planned_display_at: string;
  provider: string;
  model: string;
  generation_duration_seconds?: number;
  render_duration_seconds?: number;
  preview_url?: string;
  frame_id?: string | null;
  prompt_snapshot?: { scene_id?: string; scene_family?: string } | null;
  source?: {
    id: string;
    url: string;
    download_url: string;
    media_type: string;
    byte_length: number;
    sha256: string;
    width?: number | null;
    height?: number | null;
  } | null;
  error_detail?: string;
};

type PublishOperation = {
  generation: number;
  controller: AbortController;
  timer: number | null;
  onCancel: (operation: PublishOperation) => void;
};

type PublishCoordinator = {
  active: boolean;
  start: (onCancel: (operation: PublishOperation) => void) => PublishOperation;
  isActive: (operation: PublishOperation) => boolean;
  delay: (operation: PublishOperation) => Promise<void>;
  finish: (operation: PublishOperation) => void;
  cancel: () => void;
};

function usePublishCoordinator(): PublishCoordinator {
  const [active, setActive] = useState(false);
  const generationRef = useRef(0);
  const operationRef = useRef<PublishOperation | null>(null);

  const cancel = useCallback(() => {
    const operation = operationRef.current;
    operationRef.current = null;
    generationRef.current += 1;
    if (operation) {
      operation.controller.abort();
      if (operation.timer !== null) window.clearTimeout(operation.timer);
      operation.onCancel(operation);
    }
    setActive(false);
  }, []);

  useEffect(() => cancel, [cancel]);

  const start = useCallback((onCancel: (operation: PublishOperation) => void) => {
    const previous = operationRef.current;
    if (previous) {
      previous.controller.abort();
      if (previous.timer !== null) window.clearTimeout(previous.timer);
      previous.onCancel(previous);
    }
    const operation = {
      generation: ++generationRef.current,
      controller: new AbortController(),
      timer: null,
      onCancel,
    };
    operationRef.current = operation;
    setActive(true);
    return operation;
  }, []);

  const isActive = useCallback((operation: PublishOperation) => (
    operationRef.current?.generation === operation.generation
    && !operation.controller.signal.aborted
  ), []);

  const delay = useCallback((operation: PublishOperation) => new Promise<void>((resolve, reject) => {
    const abort = () => {
      if (operation.timer !== null) window.clearTimeout(operation.timer);
      operation.timer = null;
      reject(new DOMException("Aborted", "AbortError"));
    };
    operation.controller.signal.addEventListener("abort", abort, { once: true });
    operation.timer = window.setTimeout(() => {
      operation.timer = null;
      operation.controller.signal.removeEventListener("abort", abort);
      resolve();
    }, 2000);
  }), []);

  const finish = useCallback((operation: PublishOperation) => {
    if (operationRef.current?.generation === operation.generation) {
      operationRef.current = null;
      setActive(false);
    }
  }, []);

  return { active, start, isActive, delay, finish, cancel };
}

function duration(seconds?: number) {
  if (seconds === undefined || seconds === null) return "—";
  return `${seconds.toFixed(Number.isInteger(seconds) ? 0 : 1)}s`;
}

function HistoryPage({ publishCoordinator }: { publishCoordinator: PublishCoordinator }) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [sourceDialog, setSourceDialog] = useState<Generation | null>(null);
  const [publishStates, setPublishStates] = useState<Record<string, "publishing" | "waiting" | "confirmed" | "error">>({});
  const sourceOpenerRef = useRef<HTMLElement | null>(null);
  const sourceDialogRef = useRef<HTMLDivElement | null>(null);
  const sourceCloseRef = useRef<HTMLButtonElement | null>(null);
  const publishOperationRef = useRef<PublishOperation | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/v1/generations")
      .then((response) => {
        if (!response.ok) throw new Error("history load failed");
        return response.json() as Promise<Generation[] | { items: Generation[] }>;
      })
      .then((data) => { if (active) { setGenerations(Array.isArray(data) ? data : data.items); setState("ready"); } })
      .catch(() => { if (active) setState("error"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!sourceDialog) return;
    const previousOverflow = document.body.style.overflow;
    const background = Array.from(
      document.querySelectorAll<HTMLElement>(".topbar, .history-labels, .history-list"),
    );
    background.forEach((element) => { element.inert = true; });
    document.body.style.overflow = "hidden";
    sourceCloseRef.current?.focus();
    const closeOnEscapeOrTrapFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSourceDialog(null);
        return;
      }
      if (event.key !== "Tab") return;
      const actions = Array.from(
        sourceDialogRef.current?.querySelectorAll<HTMLElement>("button, a[href]") ?? [],
      ).filter((element) => !element.hasAttribute("disabled"));
      if (!actions.length) return;
      const first = actions[0];
      const last = actions[actions.length - 1];
      const active = document.activeElement;
      if (!sourceDialogRef.current?.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", closeOnEscapeOrTrapFocus);
    return () => {
      document.removeEventListener("keydown", closeOnEscapeOrTrapFocus);
      background.forEach((element) => { element.inert = false; });
      document.body.style.overflow = previousOverflow;
      sourceOpenerRef.current?.focus();
    };
  }, [sourceDialog]);

  async function publishGeneration(generation: Generation) {
    if (!generation.frame_id || publishCoordinator.active) return;
    const operation = publishCoordinator.start((cancelled) => {
      if (publishOperationRef.current?.generation !== cancelled.generation) return;
      publishOperationRef.current = null;
      setPublishStates((current) => {
        const next = { ...current };
        delete next[generation.id];
        return next;
      });
    });
    publishOperationRef.current = operation;
    setPublishStates((current) => ({ ...current, [generation.id]: "publishing" }));
    try {
      const response = await fetch(`/api/v1/generations/${generation.id}/publish`, {
        method: "POST",
        signal: operation.controller.signal,
      });
      if (!response.ok) throw new Error(`publish failed: ${response.status}`);
      const published = await response.json() as {
        frame_id: string;
        already_current: boolean;
        activated_at?: string | null;
      };
      if (!publishCoordinator.isActive(operation)) return;
      setPublishStates((current) => ({ ...current, [generation.id]: "waiting" }));
      for (let attempt = 0; attempt < 45; attempt += 1) {
        const statusResponse = await fetch("/api/v1/device/status", {
          signal: operation.controller.signal,
        });
        if (!statusResponse.ok) throw new Error(`device status failed: ${statusResponse.status}`);
        const device = await statusResponse.json() as {
          last_frame_id: string | null;
          is_current: boolean;
          reported_at?: string | null;
        };
        if (!publishCoordinator.isActive(operation)) return;
        const reportConfirmsActivation = Boolean(
          published.activated_at
          && device.reported_at
          && new Date(device.reported_at).getTime() >= new Date(published.activated_at).getTime(),
        );
        if (reportConfirmsActivation && device.is_current && device.last_frame_id === published.frame_id) {
          setPublishStates((current) => ({ ...current, [generation.id]: "confirmed" }));
          return;
        }
        if (attempt < 44) await publishCoordinator.delay(operation);
      }
      if (publishCoordinator.isActive(operation)) {
        setPublishStates((current) => ({ ...current, [generation.id]: "error" }));
      }
    } catch {
      if (publishCoordinator.isActive(operation)) {
        setPublishStates((current) => ({ ...current, [generation.id]: "error" }));
      }
    } finally {
      if (publishOperationRef.current?.generation === operation.generation) {
        publishOperationRef.current = null;
      }
      publishCoordinator.finish(operation);
    }
  }

  if (state === "loading") return <section className="content-page"><div className="panel state-card">Loading generation history…</div></section>;
  if (state === "error") return <section className="content-page"><div className="panel state-card error">Unable to load generation history.</div></section>;
  if (!generations.length) return <section className="content-page"><div className="panel state-card"><strong>No generations yet</strong><span>Completed and failed runs will appear here.</span></div></section>;

  return <section className="content-page history-page">
    <div className="history-labels"><span>Preview</span><span>Run</span><span>Timing</span><span>Result</span></div>
    <div className="history-list">{generations.map((generation) => <article className="panel history-row" key={generation.id}>
      <div className="history-preview">{generation.source ? <button className="source-thumbnail" type="button" aria-label={`Open original source for ${generation.id}`} onClick={(event) => { sourceOpenerRef.current = event.currentTarget; setSourceDialog(generation); }}><img className="source-original" src={generation.source.url} alt={`Model source for ${generation.id}`} /></button> : generation.preview_url ? <img className="spectra-preview" src={generation.preview_url} alt={`Preview for ${generation.id}`} /> : <div className="preview-placeholder">No preview</div>}</div>
      <div className="history-identity"><span className={`status-pill ${generation.status}`}>{generation.status}</span><strong>{modelLabels[generation.model] ?? generation.model}</strong><small>{generation.provider}</small>{generation.prompt_snapshot?.scene_id && <small className="scene-meta">{generation.prompt_snapshot.scene_id}</small>}{generation.prompt_snapshot?.scene_family && <small className="scene-meta">{generation.prompt_snapshot.scene_family}</small>}<code>{generation.id}</code></div>
      <dl className="history-metrics">
        <div><dt>Planned display</dt><dd><time dateTime={generation.planned_display_at}>{new Date(generation.planned_display_at).toLocaleString("en-GB", { timeZone: "Europe/Moscow", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })} MSK</time></dd></div>
        <div><dt>Generation</dt><dd>{duration(generation.generation_duration_seconds)}</dd></div>
        <div><dt>Processing</dt><dd>{duration(generation.render_duration_seconds)}</dd></div>
      </dl>
      <div className="history-result">{generation.error_detail ? <p className="error-message">{generation.error_detail}</p> : <p className="success-message">Frame ready for display</p>}{generation.frame_id && <div className="history-publish"><button className="secondary" type="button" disabled={publishCoordinator.active} onClick={() => void publishGeneration(generation)}>Send to frame now</button>{publishStates[generation.id] === "publishing" && <span>Publishing…</span>}{publishStates[generation.id] === "waiting" && <span>Waiting for frame…</span>}{publishStates[generation.id] === "confirmed" && <span className="confirmed">Shown on frame</span>}{publishStates[generation.id] === "error" && <span className="error">Send failed</span>}</div>}</div>
    </article>)}</div>
    {sourceDialog?.source && <div className="source-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSourceDialog(null); }}>
      <div className="source-modal" ref={sourceDialogRef} role="dialog" aria-modal="true" aria-label="Original model source">
        <header><strong>Original model source</strong><button ref={sourceCloseRef} type="button" aria-label="Close original source" onClick={() => setSourceDialog(null)}>×</button></header>
        <img className="source-original" src={sourceDialog.source.url} alt={`Original model source for ${sourceDialog.id}`} />
        <footer><span>{sourceDialog.source.width ?? "?"} × {sourceDialog.source.height ?? "?"}</span><a href={sourceDialog.source.download_url} download>Download original</a></footer>
      </div>
    </div>}
  </section>;
}

export default function App() {
  const [page, setPage] = useState<"studio" | "schedule" | "history">("studio");
  const publishCoordinator = usePublishCoordinator();
  const [brightness, setBrightness] = useState(0);
  const [contrast, setContrast] = useState(0);
  const [saturation, setSaturation] = useState(0);
  const [gamma, setGamma] = useState(1);
  const [sharpness, setSharpness] = useState(0);
  const [ditheringStrength, setDitheringStrength] = useState(1);
  const [dithering, setDithering] = useState("good-display-floyd-v1");
  const [presetName, setPresetName] = useState("Default Spectra 6");
  const [useAsDefault, setUseAsDefault] = useState(true);
  const [presetState, setPresetState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [model, setModel] = useState("qwen-image-2512");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [uploadState, setUploadState] = useState<"idle" | "uploading" | "error">("idle");
  const [sourceUploadActive, setSourceUploadActive] = useState(false);
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "waiting" | "confirmed" | "error">("idle");
  const publishOperationRef = useRef<PublishOperation | null>(null);
  const renderedSettingsRef = useRef<string | null>(null);
  const desiredSettingsRef = useRef<string | null>(null);
  const draftIdRef = useRef<string | null>(null);
  const draftRevisionRef = useRef(0);
  const previewInFlightRef = useRef(false);
  const pendingPreviewRef = useRef<PendingPreview | null>(null);
  const uploadRequestRef = useRef(0);
  const sourceUploadActiveRef = useRef(false);

  function processingSettings() {
    return {
      brightness,
      contrast,
      saturation,
      gamma,
      sharpness,
      dithering,
      dithering_strength: ditheringStrength,
    };
  }

  async function pumpPreview() {
    if (previewInFlightRef.current) return;
    const pending = pendingPreviewRef.current;
    if (!pending) return;
    pendingPreviewRef.current = null;
    previewInFlightRef.current = true;
    const revision = draftRevisionRef.current + 1;
    draftRevisionRef.current = revision;
    setUploadState("uploading");
    let completedLatest = false;
    let failed = false;
    try {
      const response = await fetch(`/api/v1/drafts/${pending.draftId}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...pending.settings, revision }),
      });
      if (response.status === 409) {
        const conflict = await response.json() as {
          detail?: { current_revision?: number; requested_revision?: number };
        };
        draftRevisionRef.current = Math.max(
          draftRevisionRef.current,
          conflict.detail?.current_revision ?? 0,
          conflict.detail?.requested_revision ?? 0,
        );
        if (
          draftIdRef.current === pending.draftId
          && desiredSettingsRef.current === pending.signature
        ) pendingPreviewRef.current = pending;
        return;
      }
      if (!response.ok) {
        throw new Error(`preview failed: ${response.status}`);
      }
      const result = await response.json() as { revision: number; preview_url: string };
      const stillLatest = (
        draftIdRef.current === pending.draftId
        && desiredSettingsRef.current === pending.signature
        && pendingPreviewRef.current === null
      );
      if (stillLatest) {
        renderedSettingsRef.current = pending.signature;
        setPreviewUrl(result.preview_url);
        setPublishState("idle");
        completedLatest = true;
      }
    } catch {
      failed = (
        draftIdRef.current === pending.draftId
        && desiredSettingsRef.current === pending.signature
        && !sourceUploadActiveRef.current
      );
      if (failed) {
        setUploadState("error");
      }
    } finally {
      previewInFlightRef.current = false;
      if (pendingPreviewRef.current) {
        void pumpPreview();
      } else if (
        !failed
        && (completedLatest || desiredSettingsRef.current === renderedSettingsRef.current)
        && draftIdRef.current === pending.draftId
        && !sourceUploadActiveRef.current
      ) {
        setUploadState("idle");
      }
    }
  }

  useEffect(() => {
    if (!draftId) return;
    const settings = processingSettings();
    const signature = JSON.stringify(settings);
    desiredSettingsRef.current = signature;
    if (signature === renderedSettingsRef.current) {
      pendingPreviewRef.current = null;
      return;
    }
    pendingPreviewRef.current = { draftId, settings, signature };
    const timer = window.setTimeout(() => { void pumpPreview(); }, 300);
    return () => { window.clearTimeout(timer); };
  }, [draftId, brightness, contrast, saturation, gamma, sharpness, dithering, ditheringStrength]);

  async function discardDraft(id: string) {
    try {
      await fetch(`/api/v1/drafts/${id}`, { method: "DELETE" });
    } catch {
      // The server-side TTL and hard limit remain the cleanup backstop.
    }
  }

  async function convertSource(file: File) {
    const requestId = uploadRequestRef.current + 1;
    uploadRequestRef.current = requestId;
    sourceUploadActiveRef.current = true;
    setSourceUploadActive(true);
    const previousDraftId = draftIdRef.current;
    const settings = processingSettings();
    const signature = JSON.stringify(settings);
    const form = new FormData();
    form.append("source", file);
    form.append("dithering", dithering);
    form.append("brightness", String(brightness));
    form.append("contrast", String(contrast));
    form.append("saturation", String(saturation));
    form.append("gamma", String(gamma));
    form.append("sharpness", String(sharpness));
    form.append("dithering_strength", String(ditheringStrength));
    setUploadState("uploading");
    pendingPreviewRef.current = null;
    try {
      const response = await fetch("/api/v1/drafts", { method: "POST", body: form });
      if (!response.ok) throw new Error(`draft creation failed: ${response.status}`);
      const draft = await response.json() as {
        draft_id: string;
        revision: number;
        preview_url: string;
      };
      if (uploadRequestRef.current !== requestId) {
        void discardDraft(draft.draft_id);
        return;
      }
      renderedSettingsRef.current = signature;
      desiredSettingsRef.current = signature;
      draftIdRef.current = draft.draft_id;
      draftRevisionRef.current = draft.revision;
      setDraftId(draft.draft_id);
      setPublishState("idle");
      setPreviewUrl(draft.preview_url);
      sourceUploadActiveRef.current = false;
      setSourceUploadActive(false);
      setUploadState("idle");
      if (previousDraftId && previousDraftId !== draft.draft_id) {
        void discardDraft(previousDraftId);
      }
    } catch {
      if (uploadRequestRef.current === requestId) {
        sourceUploadActiveRef.current = false;
        setSourceUploadActive(false);
        setUploadState("error");
      }
    }
  }

  function uploadSource(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void convertSource(file);
  }

  function dropSource(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (file) void convertSource(file);
  }

  async function publishNow() {
    if (
      !draftId
      || publishCoordinator.active
      || sourceUploadActiveRef.current
      || previewInFlightRef.current
      || pendingPreviewRef.current
    ) return;
    const operation = publishCoordinator.start((cancelled) => {
      if (publishOperationRef.current?.generation !== cancelled.generation) return;
      publishOperationRef.current = null;
      setPublishState("idle");
    });
    publishOperationRef.current = operation;
    setPublishState("publishing");
    try {
      const response = await fetch(`/api/v1/drafts/${draftId}/publish`, {
        method: "POST",
        signal: operation.controller.signal,
      });
      if (!response.ok) throw new Error(`publish failed: ${response.status}`);
      const published = await response.json() as { frame_id: string };
      if (!publishCoordinator.isActive(operation)) return;
      const publishedFrameId = published.frame_id;
      setPublishState("waiting");
      for (let attempt = 0; attempt < 45; attempt += 1) {
        const statusResponse = await fetch("/api/v1/device/status", {
          signal: operation.controller.signal,
        });
        if (!statusResponse.ok) throw new Error(`device status failed: ${statusResponse.status}`);
        const device = await statusResponse.json() as {
          last_frame_id: string | null;
          is_current: boolean;
        };
        if (!publishCoordinator.isActive(operation)) return;
        if (device.is_current && device.last_frame_id === publishedFrameId) {
          setPublishState("confirmed");
          return;
        }
        if (attempt < 44) await publishCoordinator.delay(operation);
      }
    } catch {
      if (publishCoordinator.isActive(operation)) setPublishState("error");
    } finally {
      if (publishOperationRef.current?.generation === operation.generation) {
        publishOperationRef.current = null;
      }
      publishCoordinator.finish(operation);
    }
  }

  async function savePreset() {
    setPresetState("saving");
    try {
      const response = await fetch("/api/v1/presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: presetName,
          make_default: useAsDefault,
          settings: {
            brightness,
            contrast,
            saturation,
            gamma,
            sharpness,
            dithering,
            dithering_strength: ditheringStrength,
          },
        }),
      });
      if (!response.ok) throw new Error(`preset save failed: ${response.status}`);
      setPresetState("saved");
    } catch {
      setPresetState("error");
    }
  }

  function resetAdjustments() {
    setBrightness(0);
    setContrast(0);
    setSaturation(0);
    setGamma(1);
    setSharpness(0);
    setDitheringStrength(1);
    setDithering("good-display-floyd-v1");
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">E-INK WEATHER FRAME</span>
          <h1>{page === "studio" ? "Frame Studio" : page === "schedule" ? "Generation schedule" : "Generation history"}</h1>
        </div>
        <nav className="primary-nav" aria-label="Primary">
          {(["studio", "schedule", "history"] as const).map((item) => <button key={item} className={page === item ? "active" : ""} type="button" onClick={() => { publishCoordinator.cancel(); setPage(item); }}>{item[0].toUpperCase() + item.slice(1)}</button>)}
        </nav>
        <div className="status"><i /> Backend ready</div>
      </header>

      {page === "studio" ? <section className="workspace">
        <aside className="panel settings-panel">
          <div className="panel-heading"><span>01</span><h2>Source & generator</h2></div>
          <div className="local-provider"><span>Local provider</span><strong>Spark / ComfyUI</strong></div>
          <label className="field">Generator<select aria-label="Generator" value={model} onChange={(event) => setModel(event.target.value)}>{models.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="upload-field" aria-label="Source image drop zone" onDragOver={(event) => event.preventDefault()} onDrop={dropSource}>Source image<input aria-label="Source image" type="file" accept="image/*" onChange={uploadSource} /><span>{uploadState === "uploading" ? "Processing…" : "Drop an image or browse"}</span><small>{uploadState === "error" ? "Conversion failed" : "PNG, JPEG, WEBP · processed locally"}</small></label>

          <div className="panel-heading section-heading"><span>02</span><h2>Processing</h2></div>
          <label className="field">Dithering algorithm<select aria-label="Dithering algorithm" value={dithering} onChange={(event) => setDithering(event.target.value)}>{algorithms.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <RangeControl label="Brightness" value={brightness} onChange={setBrightness} />
          <RangeControl label="Contrast" value={contrast} onChange={setContrast} />
          <RangeControl label="Saturation" value={saturation} onChange={setSaturation} />
          <RangeControl label="Gamma" value={gamma} onChange={setGamma} min={0.1} max={5} step={0.1} />
          <RangeControl label="Sharpness" value={sharpness} onChange={setSharpness} />
          <RangeControl label="Dithering strength" value={ditheringStrength} onChange={setDitheringStrength} min={0} max={1} step={0.05} />
        </aside>

        <section className="preview-panel">
          <div className="preview-toolbar"><span>1600 × 1200</span><span>Preview · Spectra 6</span><button type="button">Before / after</button></div>
          <div className="preview-stage">
            {previewUrl ? <img className="preview-image" src={previewUrl} alt="Spectra 6 preview" /> : <div className="empty-preview"><div className="frame-icon" /><strong>No source selected</strong><span>Upload an image to inspect the six-color conversion.</span></div>}
          </div>
          <div className="palette" aria-label="Spectra 6 palette">{["#000", "#fff", "#f5df19", "#d72b24", "#2350a5", "#2e8d49"].map((color) => <i key={color} style={{ background: color }} />)}</div>
          <div className="preview-actions">
            <button className="primary publish-now" type="button" onClick={() => void publishNow()} disabled={!draftId || sourceUploadActive || uploadState !== "idle" || publishCoordinator.active}>
              {publishState === "publishing" ? "Publishing…" : publishState === "waiting" ? "Waiting for controller…" : "Send to frame now"}
            </button>
            {publishState === "confirmed" && <span className="publish-status confirmed">Controller confirmed</span>}
            {publishState === "error" && <span className="publish-status error">Publish or confirmation failed</span>}
          </div>
        </section>

        <aside className="panel preset-panel">
          <div className="panel-heading"><span>03</span><h2>Preset</h2></div>
          <label className="field">Preset name<input value={presetName} onChange={(event) => setPresetName(event.target.value)} /></label>
          <label className="check"><input type="checkbox" checked={useAsDefault} onChange={(event) => setUseAsDefault(event.target.checked)} /> Use for future generations</label>
          <dl><div><dt>Palette</dt><dd>6 colors</dd></div><div><dt>Packed frame</dt><dd>960,000 bytes</dd></div><div><dt>Nibble order</dt><dd>High → low</dd></div></dl>
          <button className="primary" type="button" onClick={savePreset} disabled={!presetName.trim() || presetState === "saving"}>{presetState === "saving" ? "Saving…" : "Save preset"}</button>
          <button className="secondary" type="button" onClick={resetAdjustments}>Reset adjustments</button>
          {presetState === "saved" && <small className="preset-status">Preset saved</small>}
          {presetState === "error" && <small className="preset-status error">Preset save failed</small>}
        </aside>
      </section> : page === "schedule" ? <SchedulePage /> : <HistoryPage publishCoordinator={publishCoordinator} />}
    </main>
  );
}

import { useEffect, useState } from "react"
import { ArrowRight, FolderOpen, MusicNote, MusicNotes, Plugs, Warning, Waveform, X } from "@phosphor-icons/react"
import { motion } from "motion/react"
import type { ConversionDirection } from "../conversion"
import {
  artifactLabel,
  destinationForDirection,
  destinationsForSource,
  directionForRoute,
  FORMAT_META,
  isProToolsSource,
  sourceForDirection,
} from "../conversion"
import type { PreviewData, PreviewSettingsError } from "../hooks/useAppState"
import DAWMark from "./DAWMark"
import SignalPath from "./SignalPath"

interface ProjectPreviewProps {
  direction: ConversionDirection
  sourcePath: string
  preview: PreviewData | null
  outputDir: string | null
  tempo: number
  smpteStart: string
  keepUnwarped: string
  timelinePath: string | null
  settingsError: PreviewSettingsError | null
  onDirectionChange: (direction: ConversionDirection) => void
  onTempoChange: (tempo: number) => void
  onSmpteStartChange: (smpteStart: string) => void
  onKeepUnwarpedChange: (keepUnwarped: string) => void
  onSelectTimelineJson: () => void
  onClearTimelinePath: () => void
  onSelectOutputDir: () => void
  onConvert: () => void
  loading: boolean
}

const SPRING = { type: "spring" as const, stiffness: 300, damping: 26 }

function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path
}

export default function ProjectPreview({
  direction,
  sourcePath,
  preview,
  outputDir,
  tempo,
  smpteStart,
  keepUnwarped,
  timelinePath,
  settingsError,
  onDirectionChange,
  onTempoChange,
  onSmpteStartChange,
  onKeepUnwarpedChange,
  onSelectTimelineJson,
  onClearTimelinePath,
  onSelectOutputDir,
  onConvert,
  loading,
}: ProjectPreviewProps) {
  const [tempoDraft, setTempoDraft] = useState(String(tempo))
  const [smpteDraft, setSmpteDraft] = useState(smpteStart)
  const [keepUnwarpedDraft, setKeepUnwarpedDraft] = useState(keepUnwarped)
  const source = sourceForDirection(direction)
  const destination = destinationForDirection(direction)
  const projectName = preview?.projectName || basename(sourcePath).replace(/\.(logicx|als|ptx|pts|ptf)$/i, "")
  const showTempoField = isProToolsSource(direction)
  const showSmpteField = source === "logic"
  const fieldError = (field: NonNullable<PreviewSettingsError["field"]>) =>
    settingsError?.field === field ? settingsError.message : null
  const tempoError = fieldError("tempo")
  const smpteError = fieldError("smpteStart")
  const keepUnwarpedError = fieldError("keepUnwarped")
  const timelineError = fieldError("timelinePath")
  const generalSettingsError = settingsError && settingsError.field === null ? settingsError.message : null

  useEffect(() => setTempoDraft(String(tempo)), [tempo])
  useEffect(() => setSmpteDraft(smpteStart), [smpteStart])
  useEffect(() => setKeepUnwarpedDraft(keepUnwarped), [keepUnwarped])

  const commitTempo = () => {
    const parsed = Number(tempoDraft)
    if (!tempoDraft.trim() || !Number.isFinite(parsed)) {
      setTempoDraft(String(tempo))
      return
    }
    const normalized = Math.min(999, Math.max(20, parsed))
    setTempoDraft(String(normalized))
    if (normalized !== tempo) onTempoChange(normalized)
  }

  const commitSmpteStart = () => {
    const trimmed = smpteDraft.trim()
    if (!trimmed) {
      setSmpteDraft(smpteStart)
      return
    }
    setSmpteDraft(trimmed)
    if (trimmed !== smpteStart) onSmpteStartChange(trimmed)
  }

  const commitKeepUnwarped = () => {
    const trimmed = keepUnwarpedDraft.trim()
    setKeepUnwarpedDraft(trimmed)
    if (trimmed !== keepUnwarped) onKeepUnwarpedChange(trimmed)
  }

  const hasMidi = (preview?.midiTracks ?? 0) > 0
  const cards = preview
    ? [
        hasMidi
          ? { label: "Audio tracks", value: preview.tracks, icon: MusicNote }
          : { label: "Tracks", value: preview.tracks, icon: MusicNote },
        hasMidi ? { label: "MIDI tracks", value: preview.midiTracks ?? 0, icon: MusicNotes } : null,
        preview.clips !== undefined
          ? { label: hasMidi ? "Audio clips" : "Clips", value: preview.clips, icon: Waveform }
          : { label: "Audio files", value: preview.audioFiles, icon: Waveform },
        hasMidi || (preview.midiNotes ?? 0) > 0
          ? { label: "MIDI notes", value: preview.midiNotes ?? 0, icon: MusicNotes }
          : preview.plugins !== undefined
            ? { label: "Plugins", value: preview.plugins, icon: Plugs }
            : { label: "Audio files", value: preview.audioFiles, icon: Waveform },
      ].filter((card): card is { label: string; value: number; icon: typeof MusicNote } => card !== null)
    : []

  return (
    <div className="flex-1 overflow-y-auto px-8 pb-10 pt-4">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={SPRING}
        className="mx-auto max-w-3xl space-y-5"
      >
        <header className="flex items-end justify-between gap-6">
          <div className="min-w-0">
            <p className="mb-1 text-[11px] uppercase tracking-[0.16em] text-text-tertiary">Session preview</p>
            <h1 className="truncate text-xl font-semibold tracking-[-0.02em]">{projectName}</h1>
            <p className="mt-1 truncate font-mono text-[11px] text-text-secondary">{basename(sourcePath)}</p>
          </div>
          <span className="shrink-0 rounded-full border border-border bg-surface px-3 py-1.5 text-[11px] text-text-secondary">
            {FORMAT_META[source].extension} detected
          </span>
        </header>

        <section className="route-console rounded-2xl border border-border bg-surface p-5">
          <SignalPath direction={direction} />

          {generalSettingsError && (
            <p role="alert" className="mt-3 text-[11px] text-error">
              {generalSettingsError}
            </p>
          )}

          <div className={`mt-5 grid gap-4 ${showTempoField || showSmpteField ? "grid-cols-[1fr_220px]" : "grid-cols-1"}`}>
            <div>
              <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-text-tertiary">
                Destination
              </div>
              <div className="grid grid-cols-2 gap-2 rounded-xl border border-border bg-bg p-1.5" role="radiogroup">
                {destinationsForSource(source).map((format) => {
                  const optionDirection = directionForRoute(source, format)
                  const selected = destination === format
                  return (
                    <button
                      key={format}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => onDirectionChange(optionDirection)}
                      className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                        selected
                          ? "bg-surface-hover text-text-primary shadow-[inset_0_0_0_1px_rgba(196,134,142,0.45)]"
                          : "text-text-secondary hover:bg-surface/70 hover:text-text-primary"
                      }`}
                    >
                      <DAWMark format={format} size={21} className={selected ? "text-rose" : "text-stone"} />
                      <span>
                        <span className="block text-[13px] font-medium">{FORMAT_META[format].shortName}</span>
                        <span className="block text-[11px] text-text-tertiary">{artifactLabel(optionDirection)}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            {showTempoField && (
              <div>
                <label htmlFor="conversion-tempo" className="mb-2 block text-[11px] font-medium uppercase tracking-[0.14em] text-text-tertiary">
                  Conversion tempo
                </label>
                <div className="flex items-center rounded-xl border border-border bg-bg px-3 focus-within:border-rose/60">
                  <input
                    id="conversion-tempo"
                    type="number"
                    min={20}
                    max={999}
                    step={0.5}
                    value={tempoDraft}
                    onChange={(event) => setTempoDraft(event.target.value)}
                    onBlur={commitTempo}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur()
                    }}
                    className="min-w-0 flex-1 bg-transparent py-2.5 font-mono text-[15px] text-text-primary outline-none"
                  />
                  <span className="text-[11px] text-text-tertiary">BPM</span>
                </div>
                {tempoError && (
                  <p role="alert" className="mt-1.5 text-[11px] text-error">
                    {tempoError}
                  </p>
                )}
              </div>
            )}

            {showSmpteField && (
              <div>
                <label htmlFor="smpte-start" className="mb-2 block text-[11px] font-medium uppercase tracking-[0.14em] text-text-tertiary">
                  SMPTE start
                </label>
                <div className="flex items-center rounded-xl border border-border bg-bg px-3 focus-within:border-rose/60">
                  <input
                    id="smpte-start"
                    type="text"
                    value={smpteDraft}
                    onChange={(event) => setSmpteDraft(event.target.value)}
                    onBlur={commitSmpteStart}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur()
                    }}
                    className="min-w-0 flex-1 bg-transparent py-2.5 font-mono text-[15px] text-text-primary outline-none"
                  />
                </div>
                {smpteError && (
                  <p role="alert" className="mt-1.5 text-[11px] text-error">
                    {smpteError}
                  </p>
                )}
              </div>
            )}
          </div>

          {showTempoField && (
            <p className="mt-3 text-[11px] leading-relaxed text-text-secondary">
              Pro Tools sessions don't expose their tempo to the parser yet - clips are placed using this BPM.
            </p>
          )}

          {showSmpteField && (
            <p className="mt-3 text-[11px] leading-relaxed text-text-secondary">
              Bar 1 plays at this SMPTE time; use auto for one-hour-per-song setups
            </p>
          )}

          {direction === "logic2ableton" && (
            <div className="mt-5 grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="keep-unwarped" className="mb-2 block text-[11px] font-medium uppercase tracking-[0.14em] text-text-tertiary">
                  Keep unwarped
                </label>
                <div className="flex items-center rounded-xl border border-border bg-bg px-3 focus-within:border-rose/60">
                  <input
                    id="keep-unwarped"
                    type="text"
                    value={keepUnwarpedDraft}
                    onChange={(event) => setKeepUnwarpedDraft(event.target.value)}
                    onBlur={commitKeepUnwarped}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur()
                    }}
                    placeholder="LTC*, Pilot*"
                    className="min-w-0 flex-1 bg-transparent py-2.5 font-mono text-[13px] text-text-primary outline-none"
                  />
                </div>
                {keepUnwarpedError ? (
                  <p role="alert" className="mt-1.5 text-[11px] text-error">
                    {keepUnwarpedError}
                  </p>
                ) : (
                  <p className="mt-1.5 text-[11px] leading-relaxed text-text-tertiary">
                    Matching tracks won't stretch when the Live tempo changes.
                  </p>
                )}
              </div>

              <div>
                <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-text-tertiary">
                  Timeline JSON
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={onSelectTimelineJson}
                    className="min-w-0 flex-1 truncate rounded-xl border border-border bg-bg px-3 py-2.5 text-left font-mono text-[13px] text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                  >
                    {timelinePath ? basename(timelinePath) : "Choose a timeline JSON file"}
                  </button>
                  {timelinePath && (
                    <button
                      type="button"
                      onClick={onClearTimelinePath}
                      aria-label="Clear timeline JSON"
                      className="shrink-0 rounded-lg border border-border bg-bg p-2.5 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                    >
                      <X size={15} />
                    </button>
                  )}
                </div>
                {timelineError && (
                  <p role="alert" className="mt-1.5 text-[11px] text-error">
                    {timelineError}
                  </p>
                )}
              </div>
            </div>
          )}
        </section>

        {loading ? (
          <div className="flex min-h-52 items-center justify-center rounded-2xl border border-border bg-surface/55">
            <div className="flex items-center gap-3 text-[13px] text-text-secondary">
              <motion.span
                className="size-2 rounded-full bg-rose"
                animate={{ scale: [0.7, 1], opacity: [0.45, 1] }}
                transition={{ ...SPRING, repeat: Infinity, repeatType: "mirror" }}
              />
              Analyzing this route…
            </div>
          </div>
        ) : !preview ? (
          <div className="flex min-h-52 items-center justify-center rounded-2xl border border-border bg-surface/55 px-8 text-center">
            <p className="text-[13px] text-text-secondary">
              {settingsError ? "Fix the setting above, then we'll analyze this route." : "Preparing preview…"}
            </p>
          </div>
        ) : (
          <>
            <div className={`grid gap-3 ${cards.length > 3 ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-3"}`}>
              {cards.map(({ label, value, icon: Icon }) => (
                <div key={label} className="rounded-xl border border-border bg-surface p-4">
                  <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.08em] text-text-secondary">
                    <Icon size={14} />
                    {label}
                  </div>
                  <div className="font-mono text-xl font-semibold">{value}</div>
                </div>
              ))}
            </div>

            {preview.compatibilityWarnings.length > 0 && (
              <div className="rounded-xl border border-gold/45 bg-gold/5 p-4">
                <div className="mb-2 flex items-center gap-2 text-[13px] font-medium text-gold">
                  <Warning size={17} weight="fill" />
                  Compatibility notes
                </div>
                <ul className="space-y-2 pl-5 text-[11px] leading-relaxed text-stone">
                  {preview.compatibilityWarnings.map((warning, index) => (
                    <li key={`${index}-${warning}`} className="list-disc">{warning}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="rounded-xl border border-border bg-surface p-4">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="mb-1 text-[11px] uppercase tracking-[0.1em] text-text-tertiary">Output directory</div>
                  <div className="truncate font-mono text-[13px] text-text-primary">
                    {outputDir || "Choose where the generated files should go"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={onSelectOutputDir}
                  className="shrink-0 rounded-lg border border-border bg-bg p-2.5 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                  aria-label="Select output directory"
                >
                  <FolderOpen size={17} />
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-border bg-surface p-4">
              <div id="preview-report-heading" className="mb-2 text-[11px] uppercase tracking-[0.1em] text-text-secondary">
                Preview report · {artifactLabel(direction)}
              </div>
              <pre
                tabIndex={0}
                role="region"
                aria-labelledby="preview-report-heading"
                className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg font-mono text-[11px] leading-relaxed text-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose"
              >
                {preview.report}
              </pre>
            </div>

            <motion.button
              type="button"
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.99 }}
              transition={SPRING}
              onClick={onConvert}
              disabled={!outputDir}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-rose px-6 py-3 text-[15px] font-semibold text-bg transition-colors hover:bg-rose-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              Create {artifactLabel(direction)}
              <ArrowRight size={18} weight="bold" />
            </motion.button>
          </>
        )}
      </motion.div>
    </div>
  )
}

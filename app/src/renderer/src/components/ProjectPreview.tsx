import { useEffect, useState } from "react"
import { ArrowRight, FolderOpen, MusicNote, MusicNotes, Plugs, Waveform } from "@phosphor-icons/react"
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
import type { PreviewData } from "../hooks/useAppState"
import DAWMark from "./DAWMark"
import SignalPath from "./SignalPath"

interface ProjectPreviewProps {
  direction: ConversionDirection
  sourcePath: string
  preview: PreviewData | null
  outputDir: string | null
  tempo: number
  onDirectionChange: (direction: ConversionDirection) => void
  onTempoChange: (tempo: number) => void
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
  onDirectionChange,
  onTempoChange,
  onSelectOutputDir,
  onConvert,
  loading,
}: ProjectPreviewProps) {
  const [tempoDraft, setTempoDraft] = useState(String(tempo))
  const source = sourceForDirection(direction)
  const destination = destinationForDirection(direction)
  const projectName = preview?.projectName || basename(sourcePath).replace(/\.(logicx|als|ptx|pts|ptf)$/i, "")

  useEffect(() => setTempoDraft(String(tempo)), [tempo])

  const commitTempo = () => {
    const parsed = Number(tempoDraft)
    if (!Number.isFinite(parsed)) {
      setTempoDraft(String(tempo))
      return
    }
    const normalized = Math.min(999, Math.max(20, parsed))
    setTempoDraft(String(normalized))
    if (normalized !== tempo) onTempoChange(normalized)
  }

  const cards = preview
    ? [
        { label: "Tracks", value: preview.tracks, icon: MusicNote },
        preview.clips !== undefined
          ? { label: "Clips", value: preview.clips, icon: Waveform }
          : { label: "Audio files", value: preview.audioFiles, icon: Waveform },
        (preview.midiNotes ?? 0) > 0
          ? { label: "MIDI notes", value: preview.midiNotes ?? 0, icon: MusicNotes }
          : preview.plugins !== undefined
            ? { label: "Plugins", value: preview.plugins, icon: Plugs }
            : { label: "Audio files", value: preview.audioFiles, icon: Waveform },
      ]
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

          <div className={`mt-5 grid gap-4 ${isProToolsSource(direction) ? "grid-cols-[1fr_220px]" : "grid-cols-1"}`}>
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

            {isProToolsSource(direction) && (
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
              </div>
            )}
          </div>

          {isProToolsSource(direction) && (
            <p className="mt-3 text-[11px] leading-relaxed text-text-secondary">
              Pro Tools sessions don't expose their tempo to the parser yet - clips are placed using this BPM.
            </p>
          )}
        </section>

        {loading || !preview ? (
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
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3">
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
              <div className="mb-2 text-[11px] uppercase tracking-[0.1em] text-text-secondary">
                Preview report · {artifactLabel(direction)}
              </div>
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-text-tertiary">
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

import { useState } from "react"
import {
  ArrowCounterClockwise,
  CheckCircle,
  FileText,
  FolderOpen,
  Warning,
  XCircle,
} from "@phosphor-icons/react"
import { AnimatePresence, motion } from "motion/react"
import type { ConversionDirection } from "../conversion"
import { artifactLabel, routeLabel } from "../conversion"
import type { ConversionResult } from "../hooks/useAppState"
import SignalPath from "./SignalPath"

interface ConversionCompleteProps {
  direction: ConversionDirection
  result: ConversionResult | null
  error: string | null
  onConvertAnother: () => void
}

const SPRING = { type: "spring" as const, stiffness: 280, damping: 26 }

export default function ConversionComplete({
  direction,
  result,
  error,
  onConvertAnother,
}: ConversionCompleteProps) {
  const [showReport, setShowReport] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)
  const routeDirection = result?.direction ?? direction

  if (error) {
    return (
      <div className="flex-1 overflow-y-auto px-8 pb-10 pt-8">
        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={SPRING}
          className="mx-auto w-full max-w-2xl space-y-4"
        >
          <section className="rounded-2xl border border-error/70 bg-surface p-6">
            <SignalPath direction={routeDirection} />
            <div className="mt-6 flex items-center gap-3">
              <XCircle size={24} weight="fill" className="text-error" />
              <div>
                <h1 className="text-[15px] font-semibold">Conversion failed</h1>
                <p className="text-[11px] text-text-tertiary">{routeLabel(routeDirection)}</p>
              </div>
            </div>
            <pre className="mt-4 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl bg-bg p-4 font-mono text-[11px] leading-relaxed text-text-secondary">
              {error}
            </pre>
          </section>
          <button
            type="button"
            onClick={onConvertAnother}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 py-2.5 text-[13px] transition-colors hover:bg-surface-hover"
          >
            <ArrowCounterClockwise size={16} />
            Try another session
          </button>
        </motion.div>
      </div>
    )
  }

  if (!result) return null

  const outputLabel = artifactLabel(result.direction)
  const warnings = result.compatibilityWarnings
  const thirdStat = (result.midiNotes ?? 0) > 0
    ? { value: result.midiNotes ?? 0, label: "MIDI notes" }
    : { value: result.audioFiles, label: "Audio files" }
  const stats = [
    { value: result.tracks, label: "Tracks" },
    { value: result.clips, label: "Clips" },
    thirdStat,
  ]

  return (
    <div className="flex-1 overflow-y-auto px-8 pb-10 pt-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={SPRING}
        className="mx-auto w-full max-w-2xl space-y-4"
      >
        <section className="rounded-2xl border border-gold/70 bg-surface p-6 shadow-[0_18px_60px_rgba(0,0,0,0.18)]">
          <SignalPath direction={result.direction} />

          <div className="mt-6 flex items-center gap-3 border-t border-border pt-5">
            <CheckCircle size={25} weight="fill" className="text-gold" />
            <div>
              <h1 className="text-[15px] font-semibold">{outputLabel} ready</h1>
              <p className="text-[11px] text-text-tertiary">{routeLabel(result.direction)}</p>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-3 gap-3">
            {stats.map((stat) => (
              <div key={stat.label} className="rounded-xl border border-border bg-bg/70 px-3 py-3 text-center">
                <div className="font-mono text-xl font-semibold text-text-primary">{stat.value}</div>
                <div className="mt-1 text-[11px] uppercase tracking-[0.08em] text-text-secondary">{stat.label}</div>
              </div>
            ))}
          </div>

          {warnings.length > 0 && (
            <div className="mt-5 rounded-xl border border-gold/45 bg-gold/5 p-4">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-medium text-gold">
                <Warning size={17} weight="fill" />
                Compatibility notes
              </div>
              <ul className="space-y-2 pl-5 text-[11px] leading-relaxed text-stone">
                {warnings.map((warning, index) => (
                  <li key={`${index}-${warning}`} className="list-disc">{warning}</li>
                ))}
              </ul>
            </div>
          )}

          {openError && <p role="alert" className="mt-4 select-text text-[12px] text-error">{openError}</p>}
          <div className="mt-5 grid grid-cols-2 gap-2">
            <motion.button
              type="button"
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.99 }}
              transition={SPRING}
              onClick={() => {
                setOpenError(null)
                void window.api.showInFolder(result.artifactPath).catch((error) => {
                  setOpenError(error instanceof Error ? error.message : String(error))
                })
              }}
              className="flex items-center justify-center gap-2 rounded-xl bg-rose px-4 py-2.5 text-[13px] font-semibold text-bg transition-colors hover:bg-rose-hover"
            >
              <FolderOpen size={16} weight="fill" />
              Open Output Folder
            </motion.button>
            <button
              type="button"
              onClick={() => setShowReport((visible) => !visible)}
              className="flex items-center justify-center gap-2 rounded-xl border border-border bg-bg px-4 py-2.5 text-[13px] font-medium text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
            >
              <FileText size={16} />
              {showReport ? "Hide Report" : "Show Report"}
            </button>
          </div>
        </section>

        <AnimatePresence initial={false}>
          {showReport && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={SPRING}
              className="overflow-hidden rounded-xl border border-border bg-surface"
            >
              <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap p-4 font-mono text-[11px] leading-relaxed text-text-secondary">
                {result.report}
              </pre>
            </motion.div>
          )}
        </AnimatePresence>

        <button
          type="button"
          onClick={onConvertAnother}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 py-2.5 text-[13px] transition-colors hover:bg-surface-hover"
        >
          <ArrowCounterClockwise size={16} />
          Convert another session
        </button>
      </motion.div>
    </div>
  )
}

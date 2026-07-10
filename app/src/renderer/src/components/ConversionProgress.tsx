import { Check } from "@phosphor-icons/react"
import { motion } from "motion/react"
import type { ConversionDirection } from "../conversion"
import { artifactLabel, FORMAT_META, sourceForDirection } from "../conversion"
import SignalPath from "./SignalPath"

interface ConversionProgressProps {
  direction: ConversionDirection
  stage: string
  progress: number
  message: string
  logs: string[]
}

const STAGES = ["Validate", "Parse", "Generate", "Done"] as const
const SPRING = { type: "spring" as const, stiffness: 220, damping: 26 }

function activeStageIndex(stage: string, progress: number): number {
  if (stage === "complete" || progress >= 1) return 3
  if (["generating", "copying", "report-write"].includes(stage) || progress >= 0.55) return 2
  if (["parsing", "mixer", "plugins", "report"].includes(stage) || progress >= 0.1) return 1
  return 0
}

export default function ConversionProgress({
  direction,
  stage,
  progress,
  message,
  logs,
}: ConversionProgressProps) {
  const activeIndex = activeStageIndex(stage, progress)
  const source = sourceForDirection(direction)
  const outputLabel = artifactLabel(direction)

  return (
    <div className="flex-1 overflow-y-auto px-8 pb-10 pt-8">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={SPRING}
        className="mx-auto w-full max-w-3xl space-y-6"
      >
        <header className="text-center">
          <p className="text-[11px] uppercase tracking-[0.16em] text-text-tertiary">Signal in transit</p>
          <h1 className="mt-2 text-xl font-semibold tracking-[-0.02em]">Building your {outputLabel}</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            Reading the {FORMAT_META[source].shortName} session and patching its arrangement across.
          </p>
        </header>

        <section className="route-console rounded-2xl border border-border bg-surface px-6 py-5">
          <SignalPath direction={direction} active />
        </section>

        <section className="rounded-2xl border border-border bg-surface p-5">
          <div className="stage-rail">
            <motion.div
              className="stage-rail-fill"
              initial={{ width: 0 }}
              animate={{ width: `${activeIndex * 25}%` }}
              transition={SPRING}
            />
            {STAGES.map((label, index) => {
              const complete = index < activeIndex || activeIndex === STAGES.length - 1
              const active = index === activeIndex && !complete
              return (
                <div key={label} className="stage-rail-item">
                  <motion.span
                    animate={{ scale: active ? 1.08 : 1 }}
                    transition={SPRING}
                    className={`stage-rail-node ${complete ? "is-complete" : ""} ${active ? "is-active" : ""}`}
                  >
                    {complete ? <Check size={12} weight="bold" /> : index + 1}
                  </motion.span>
                  <span className={`stage-rail-label ${active || complete ? "is-reached" : ""}`}>{label}</span>
                </div>
              )
            })}
          </div>

          <div className="mt-7 flex items-center justify-between gap-4 border-t border-border pt-4">
            <span className="truncate text-[13px] text-text-secondary">{message || "Preparing converter…"}</span>
            <span className="shrink-0 font-mono text-[13px] text-gold">{Math.round(progress * 100)}%</span>
          </div>
        </section>

        <section className="overflow-hidden rounded-xl border border-border bg-[#17151D]">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <span className="text-[11px] uppercase tracking-[0.12em] text-text-tertiary">Converter log</span>
            <span className="size-1.5 rounded-full bg-gold/70" />
          </div>
          <div className="max-h-44 min-h-24 overflow-y-auto px-4 py-3 font-mono text-[11px] leading-relaxed text-text-secondary">
            {logs.length > 0 ? (
              logs.slice(-10).map((log, index) => <div key={`${index}-${log}`}>{log}</div>)
            ) : (
              <span className="text-text-tertiary">Waiting for converter output…</span>
            )}
          </div>
        </section>
      </motion.div>
    </div>
  )
}

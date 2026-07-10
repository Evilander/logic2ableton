import { motion, useReducedMotion } from "motion/react"
import type { ConversionDirection } from "../conversion"
import { FORMAT_META, MODE_META } from "../conversion"
import DAWMark from "./DAWMark"

interface SignalPathProps {
  direction: ConversionDirection
  active?: boolean
  compact?: boolean
}

export default function SignalPath({ direction, active = false, compact = false }: SignalPathProps) {
  const reduceMotion = useReducedMotion()
  const { source, destination } = MODE_META[direction]

  return (
    <div
      className={`signal-path ${compact ? "signal-path-compact" : ""}`}
      aria-label={`${FORMAT_META[source].name} to ${FORMAT_META[destination].name}`}
    >
      <div className="signal-endpoint">
        <span className="signal-mark signal-mark-source">
          <DAWMark format={source} size={compact ? 18 : 28} />
        </span>
        {!compact && <span>{FORMAT_META[source].shortName}</span>}
      </div>

      <div className="signal-line" aria-hidden="true">
        <span className="signal-line-arrow" />
        {active && (
          <motion.span
            className="signal-pulse"
            initial={{ left: "0%", opacity: reduceMotion ? 0.85 : 0.35 }}
            animate={reduceMotion
              ? { left: "50%", opacity: 0.85 }
              : { left: ["0%", "calc(100% - 8px)"], opacity: 0.95 }}
            transition={reduceMotion
              ? { type: "spring", stiffness: 280, damping: 26 }
              : {
                  type: "spring",
                  stiffness: 42,
                  damping: 13,
                  repeat: Infinity,
                  repeatDelay: 0.12,
                }}
          />
        )}
      </div>

      <div className="signal-endpoint">
        <span className="signal-mark signal-mark-destination">
          <DAWMark format={destination} size={compact ? 18 : 28} />
        </span>
        {!compact && <span>{FORMAT_META[destination].shortName}</span>}
      </div>
    </div>
  )
}

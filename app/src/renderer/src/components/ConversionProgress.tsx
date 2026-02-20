import { motion } from "motion/react"
import { CircleNotch } from "@phosphor-icons/react"

interface ConversionProgressProps {
  stage: string
  progress: number
  message: string
  logs: string[]
}

const STAGE_LABELS: Record<string, string> = {
  parsing: "Parsing Logic Pro project",
  plugins: "Matching plugins",
  generating: "Generating Ableton session",
  copying: "Copying audio files",
}

export default function ConversionProgress({ stage, progress, message, logs }: ConversionProgressProps) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md space-y-6"
      >
        {/* Spinner + Stage */}
        <div className="flex items-center gap-3">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
          >
            <CircleNotch size={20} className="text-rose" />
          </motion.div>
          <span className="text-sm font-medium">
            {STAGE_LABELS[stage] || message}
          </span>
        </div>

        {/* Progress Bar */}
        <div className="space-y-2">
          <div className="h-2 bg-surface rounded-full overflow-hidden border border-border">
            <motion.div
              className="h-full bg-rose rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${Math.max(progress * 100, 2)}%` }}
              transition={{ type: "spring", stiffness: 100, damping: 30 }}
            />
          </div>
          <div className="flex justify-between text-xs text-text-tertiary">
            <span>{message}</span>
            <span>{Math.round(progress * 100)}%</span>
          </div>
        </div>

        {/* Log Panel */}
        {logs.length > 0 && (
          <div className="bg-surface rounded-xl border border-border p-3 max-h-40 overflow-y-auto">
            <div className="space-y-0.5 font-mono text-xs text-text-tertiary">
              {logs.map((log, i) => (
                <div key={i}>{log}</div>
              ))}
            </div>
          </div>
        )}
      </motion.div>
    </div>
  )
}

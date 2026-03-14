import { useState } from "react"
import { motion } from "motion/react"
import { ArrowCounterClockwise, CheckCircle, FolderOpen, Play, XCircle } from "@phosphor-icons/react"
import type { ConversionResult } from "../hooks/useAppState"

interface ConversionCompleteProps {
  result: ConversionResult | null
  error: string | null
  onConvertAnother: () => void
}

export default function ConversionComplete({ result, error, onConvertAnother }: ConversionCompleteProps) {
  const [showReport, setShowReport] = useState(false)

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ type: "spring", stiffness: 300, damping: 25 }}
          className="w-full max-w-md space-y-4"
        >
          <div className="bg-surface rounded-2xl border-2 border-error p-6 space-y-4">
            <div className="flex items-center gap-3">
              <XCircle size={24} weight="fill" className="text-error" />
              <span className="font-semibold">Conversion Failed</span>
            </div>
            <p className="text-sm text-text-secondary font-mono bg-bg rounded-lg p-3 whitespace-pre-wrap">
              {error}
            </p>
          </div>
          <button
            onClick={onConvertAnother}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-surface hover:bg-surface-hover border border-border text-sm transition-colors cursor-pointer"
          >
            <ArrowCounterClockwise size={16} />
            Try Again
          </button>
        </motion.div>
      </div>
    )
  }

  if (!result) return null

  const isForward = result.direction === "logic2ableton"
  const primaryActionLabel = isForward ? "Open in Ableton" : "Open Logic Import Guide"
  const title = isForward ? "Conversion Complete" : "Logic Import Package Ready"
  const reportLabel = isForward ? "conversion report" : "transfer report"

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: "spring", stiffness: 300, damping: 25 }}
        className="w-full max-w-md space-y-4"
      >
        <div className="bg-surface rounded-2xl border-2 border-gold p-6 space-y-4">
          <div className="flex items-center gap-3">
            <CheckCircle size={24} weight="fill" className="text-gold" />
            <div>
              <div className="font-semibold">{title}</div>
              <div className="text-xs text-text-tertiary">
                {isForward ? "Logic to Ableton" : "Ableton to Logic"}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-lg font-semibold font-mono">{result.tracks}</div>
              <div className="text-xs text-text-secondary">tracks</div>
            </div>
            <div>
              <div className="text-lg font-semibold font-mono">{result.clips}</div>
              <div className="text-xs text-text-secondary">clips</div>
            </div>
            <div>
              <div className="text-lg font-semibold font-mono">
                {isForward ? result.audioFiles : result.locators ?? 0}
              </div>
              <div className="text-xs text-text-secondary">
                {isForward ? "audio files" : "locators"}
              </div>
            </div>
          </div>

          <div className="flex gap-2">
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              onClick={() => window.api.openFile(result.artifactPath)}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-rose hover:bg-rose-hover text-bg font-medium text-sm transition-colors cursor-pointer"
            >
              <Play size={16} weight="fill" />
              {primaryActionLabel}
            </motion.button>
            <button
              onClick={() => window.api.showInFolder(result.artifactPath)}
              className="px-3 py-2.5 rounded-xl bg-bg hover:bg-surface-hover border border-border text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
            >
              <FolderOpen size={16} />
            </button>
          </div>
        </div>

        <button
          onClick={() => setShowReport(!showReport)}
          className="w-full text-xs text-text-tertiary hover:text-text-secondary text-center py-1 transition-colors cursor-pointer"
        >
          {showReport ? "Hide" : "Show"} {reportLabel}
        </button>

        {showReport && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="bg-surface rounded-xl border border-border p-4 max-h-60 overflow-y-auto"
          >
            <pre className="font-mono text-xs text-text-secondary whitespace-pre-wrap">
              {result.report}
            </pre>
          </motion.div>
        )}

        <button
          onClick={onConvertAnother}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-surface hover:bg-surface-hover border border-border text-sm transition-colors cursor-pointer"
        >
          <ArrowCounterClockwise size={16} />
          Convert Another
        </button>
      </motion.div>
    </div>
  )
}

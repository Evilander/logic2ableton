import { MusicNote, Waveform, Plugs, FolderOpen, ArrowRight } from "@phosphor-icons/react"
import { motion } from "motion/react"
import type { PreviewData } from "../hooks/useAppState"

interface ProjectPreviewProps {
  logicxPath: string
  preview: PreviewData | null
  outputDir: string | null
  onSelectOutputDir: () => void
  onConvert: () => void
  loading: boolean
}

function basename(p: string): string {
  return p.split(/[/\\]/).pop() || p
}

export default function ProjectPreview({
  logicxPath,
  preview,
  outputDir,
  onSelectOutputDir,
  onConvert,
  loading,
}: ProjectPreviewProps) {
  if (loading || !preview) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <motion.div
          animate={{ opacity: [0.4, 1, 0.4] }}
          transition={{ repeat: Infinity, duration: 1.5 }}
          className="text-text-secondary"
        >
          Analyzing project...
        </motion.div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-8">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="max-w-2xl mx-auto space-y-6"
      >
        {/* Project Header */}
        <div>
          <h1 className="text-xl font-semibold mb-1">{preview.projectName}</h1>
          <p className="text-text-secondary text-sm">{basename(logicxPath)}</p>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-surface rounded-xl p-4 border border-border">
            <div className="flex items-center gap-2 text-text-secondary text-xs mb-1">
              <MusicNote size={14} />
              Tracks
            </div>
            <div className="text-2xl font-semibold font-mono">{preview.tracks}</div>
          </div>
          <div className="bg-surface rounded-xl p-4 border border-border">
            <div className="flex items-center gap-2 text-text-secondary text-xs mb-1">
              <Waveform size={14} />
              Audio Files
            </div>
            <div className="text-2xl font-semibold font-mono">{preview.audioFiles}</div>
          </div>
          <div className="bg-surface rounded-xl p-4 border border-border">
            <div className="flex items-center gap-2 text-text-secondary text-xs mb-1">
              <Plugs size={14} />
              Plugins
            </div>
            <div className="text-2xl font-semibold font-mono">{preview.plugins}</div>
          </div>
        </div>

        {/* Output Directory */}
        <div className="bg-surface rounded-xl p-4 border border-border">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-text-secondary mb-1">Output Directory</div>
              <div className="text-sm font-mono text-text-primary truncate max-w-md">
                {outputDir || "Click to select..."}
              </div>
            </div>
            <button
              onClick={onSelectOutputDir}
              className="px-3 py-1.5 rounded-lg bg-bg hover:bg-surface-hover border border-border text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              <FolderOpen size={16} />
            </button>
          </div>
        </div>

        {/* Convert Button */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={onConvert}
          disabled={!outputDir}
          className="w-full flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-rose hover:bg-rose-hover text-bg font-semibold text-base transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          Convert to Ableton
          <ArrowRight size={18} weight="bold" />
        </motion.button>
      </motion.div>
    </div>
  )
}

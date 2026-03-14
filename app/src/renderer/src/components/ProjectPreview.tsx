import { FolderOpen, MapPin, MusicNote, Plugs, Waveform, ArrowRight } from "@phosphor-icons/react"
import { motion } from "motion/react"
import type { PreviewData } from "../hooks/useAppState"

interface ProjectPreviewProps {
  sourcePath: string
  preview: PreviewData | null
  outputDir: string | null
  onSelectOutputDir: () => void
  onConvert: () => void
  loading: boolean
}

function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path
}

export default function ProjectPreview({
  sourcePath,
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

  const isForward = preview.direction === "logic2ableton"
  const cards = isForward
    ? [
        { label: "Tracks", value: preview.tracks, icon: MusicNote },
        { label: "Audio Files", value: preview.audioFiles, icon: Waveform },
        { label: "Plugins", value: preview.plugins ?? 0, icon: Plugs },
      ]
    : [
        { label: "Tracks", value: preview.tracks, icon: MusicNote },
        { label: "Clips", value: preview.clips, icon: Waveform },
        { label: "Locators", value: preview.locators ?? 0, icon: MapPin },
      ]

  return (
    <div className="flex-1 overflow-y-auto p-8">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="max-w-2xl mx-auto space-y-6"
      >
        <div>
          <div className="inline-flex items-center rounded-full border border-border bg-surface px-3 py-1 text-xs text-text-secondary mb-3">
            {isForward ? "Logic to Ableton" : "Ableton to Logic"}
          </div>
          <h1 className="text-xl font-semibold mb-1">{preview.projectName}</h1>
          <p className="text-text-secondary text-sm">{basename(sourcePath)}</p>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {cards.map(({ label, value, icon: Icon }) => (
            <div key={label} className="bg-surface rounded-xl p-4 border border-border">
              <div className="flex items-center gap-2 text-text-secondary text-xs mb-1">
                <Icon size={14} />
                {label}
              </div>
              <div className="text-2xl font-semibold font-mono">{value}</div>
            </div>
          ))}
        </div>

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

        <div className="bg-surface rounded-xl border border-border p-4">
          <div className="text-xs text-text-secondary mb-2">Preview Report</div>
          <pre className="max-h-52 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-text-tertiary">
            {preview.report}
          </pre>
        </div>

        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={onConvert}
          disabled={!outputDir}
          className="w-full flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-rose hover:bg-rose-hover text-bg font-semibold text-base transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          {isForward ? "Convert to Ableton" : "Build Logic Transfer Package"}
          <ArrowRight size={18} weight="bold" />
        </motion.button>
      </motion.div>
    </div>
  )
}

import { Plus, Clock, CheckCircle, XCircle, Waveform } from "@phosphor-icons/react"
import { motion } from "motion/react"

interface SidebarProps {
  history: ConversionRecord[]
  onNewConversion: () => void
  onSelectRecord: (record: ConversionRecord) => void
  selectedId: string | null
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export default function Sidebar({ history, onNewConversion, onSelectRecord, selectedId }: SidebarProps) {
  return (
    <aside className="w-[260px] h-screen flex flex-col border-r border-border bg-surface shrink-0">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 flex items-center gap-2 -webkit-app-region-drag">
        <Waveform size={20} weight="duotone" className="text-rose" />
        <span className="text-sm font-semibold tracking-tight">logic2ableton</span>
      </div>

      {/* New Conversion Button */}
      <div className="px-3 pb-3">
        <button
          onClick={onNewConversion}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-rose text-bg text-sm font-medium hover:bg-rose-hover transition-colors cursor-pointer"
        >
          <Plus size={16} weight="bold" />
          New Conversion
        </button>
      </div>

      {/* History */}
      <div className="flex-1 overflow-y-auto px-2">
        {history.length === 0 ? (
          <div className="px-3 py-8 text-center text-text-tertiary text-xs">
            No conversions yet
          </div>
        ) : (
          <div className="space-y-0.5">
            {history.map((record, i) => (
              <motion.button
                key={record.id}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.04 }}
                onClick={() => onSelectRecord(record)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-sm transition-colors cursor-pointer ${
                  selectedId === record.id
                    ? "bg-surface-hover text-text-primary"
                    : "text-text-secondary hover:bg-surface-hover hover:text-text-primary"
                }`}
              >
                {record.status === "success" ? (
                  <CheckCircle size={14} weight="fill" className="text-gold shrink-0" />
                ) : (
                  <XCircle size={14} weight="fill" className="text-error shrink-0" />
                )}
                <span className="truncate flex-1">{record.projectName}</span>
                <span className="text-xs text-text-tertiary shrink-0">
                  {timeAgo(record.date)}
                </span>
              </motion.button>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-border">
        <div className="flex items-center gap-1.5 text-xs text-text-tertiary">
          <Clock size={12} />
          <span>{history.length} conversion{history.length !== 1 ? "s" : ""}</span>
        </div>
      </div>
    </aside>
  )
}

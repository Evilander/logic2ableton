import { CheckCircle, Clock, Plus, Waveform, XCircle } from "@phosphor-icons/react"
import { motion } from "motion/react"
import type { CSSProperties } from "react"
import type { ConversionDirection } from "../conversion"
import { FORMAT_META, MODE_META, routeLabel } from "../conversion"
import type { ConversionRecord } from "../hooks/useAppState"
import DAWMark from "./DAWMark"

interface SidebarProps {
  history: ConversionRecord[]
  direction: ConversionDirection | null
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
  return `${Math.floor(hours / 24)}d ago`
}

function CompactRoute({ direction }: { direction: ConversionDirection }) {
  const { source, destination } = MODE_META[direction]
  return (
    <span
      className="inline-flex items-center gap-1.5 text-text-tertiary"
      aria-label={routeLabel(direction)}
      title={`${FORMAT_META[source].shortName} → ${FORMAT_META[destination].shortName}`}
    >
      <DAWMark format={source} size={14} />
      <span className="route-mini-line" aria-hidden="true" />
      <DAWMark format={destination} size={14} />
    </span>
  )
}

export default function Sidebar({
  history,
  direction,
  onNewConversion,
  onSelectRecord,
  selectedId,
}: SidebarProps) {
  return (
    <aside className="flex h-screen w-[276px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="h-10 shrink-0" style={{ WebkitAppRegion: "drag" } as CSSProperties} />

      <div className="flex items-center justify-between gap-3 px-4 pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-bg text-rose">
            <Waveform size={18} weight="duotone" />
          </span>
          <div className="min-w-0">
            <div className="truncate text-[13px] font-semibold tracking-tight">Session Transfer</div>
            <div className="text-[11px] text-text-tertiary">Logic · Live · Pro Tools</div>
          </div>
        </div>
        {direction && <CompactRoute direction={direction} />}
      </div>

      <div className="px-3 pb-3">
        <button
          type="button"
          onClick={onNewConversion}
          className="flex w-full items-center gap-2 rounded-lg bg-rose px-3 py-2.5 text-[13px] font-medium text-bg transition-colors hover:bg-rose-hover"
        >
          <Plus size={16} weight="bold" />
          New conversion
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2">
        {history.length === 0 ? (
          <div className="px-3 py-8 text-center text-[11px] text-text-tertiary">Completed routes will appear here.</div>
        ) : (
          <div className="space-y-1">
            {history.map((record, index) => (
              <motion.button
                key={record.id}
                type="button"
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 26, delay: index * 0.025 }}
                onClick={() => onSelectRecord(record)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors ${
                  selectedId === record.id
                    ? "bg-surface-hover text-text-primary"
                    : "text-text-secondary hover:bg-surface-hover hover:text-text-primary"
                }`}
              >
                {record.status === "success" ? (
                  <CheckCircle size={14} weight="fill" className="shrink-0 text-gold" />
                ) : (
                  <XCircle size={14} weight="fill" className="shrink-0 text-error" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px]">{record.projectName}</div>
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <CompactRoute direction={record.direction} />
                    <span className="shrink-0 text-[11px] text-text-tertiary">{timeAgo(record.date)}</span>
                  </div>
                </div>
              </motion.button>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-border px-4 py-3">
        <div className="flex items-center gap-1.5 text-[11px] text-text-tertiary">
          <Clock size={12} />
          <span>{history.length} conversion{history.length !== 1 ? "s" : ""}</span>
        </div>
      </div>
    </aside>
  )
}

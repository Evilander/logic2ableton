import { useCallback, useRef, useState } from "react"
import { motion } from "motion/react"
import { ArrowsLeftRight, CloudArrowUp, FolderOpen } from "@phosphor-icons/react"
import type { ConversionDirection } from "../hooks/useAppState"

interface DropZoneProps {
  direction: ConversionDirection
  onDirectionChange: (direction: ConversionDirection) => void
  onProjectSelected: (path: string) => void
}

const DIRECTION_COPY: Record<ConversionDirection, { title: string; extension: string; button: string }> = {
  logic2ableton: {
    title: "Drop a .logicx project here",
    extension: ".logicx",
    button: "Browse Logic Project",
  },
  ableton2logic: {
    title: "Drop an .als Live Set here",
    extension: ".als",
    button: "Browse Live Set",
  },
}

export default function DropZone({ direction, onDirectionChange, onProjectSelected }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const dragCounter = useRef(0)
  const copy = DIRECTION_COPY[direction]

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current += 1
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current -= 1
    if (dragCounter.current === 0) setIsDragging(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current = 0
    setIsDragging(false)

    const files = e.dataTransfer.files
    if (files.length === 0) return
    const file = files[0] as File & { path?: string }
    const path = file.path
    if (path && path.toLowerCase().endsWith(copy.extension)) {
      onProjectSelected(path)
    }
  }, [copy.extension, onProjectSelected])

  const handleBrowse = async () => {
    const path = await window.api.selectSource(direction)
    if (path) onProjectSelected(path)
  }

  return (
    <div
      className="flex-1 flex items-center justify-center p-8"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <motion.div
        animate={{
          borderColor: isDragging ? "#C4868E" : "#353340",
          scale: isDragging ? 1.005 : 1,
        }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
        className="w-full max-w-xl border-2 border-dashed rounded-2xl p-12 flex flex-col items-center gap-5 cursor-pointer"
        onClick={handleBrowse}
      >
        <div className="flex items-center gap-2 p-1 rounded-full border border-border bg-surface">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onDirectionChange("logic2ableton")
            }}
            className={`px-4 py-2 rounded-full text-sm transition-colors cursor-pointer ${
              direction === "logic2ableton" ? "bg-rose text-bg" : "text-text-secondary hover:text-text-primary"
            }`}
          >
            Logic to Ableton
          </button>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onDirectionChange("ableton2logic")
            }}
            className={`px-4 py-2 rounded-full text-sm transition-colors cursor-pointer ${
              direction === "ableton2logic" ? "bg-rose text-bg" : "text-text-secondary hover:text-text-primary"
            }`}
          >
            Ableton to Logic
          </button>
        </div>

        <motion.div
          animate={{ y: isDragging ? -4 : 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
        >
          <CloudArrowUp size={48} weight="duotone" className="text-stone" />
        </motion.div>

        <div className="text-center max-w-md">
          <p className="text-text-primary text-base font-medium mb-1">{copy.title}</p>
          <p className="text-text-secondary text-sm">
            Use the toggle above to switch directions. The app will preview the session before it writes anything.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              handleBrowse()
            }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface hover:bg-surface-hover border border-border text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            <FolderOpen size={16} />
            {copy.button}
          </button>

          <div className="flex items-center gap-2 text-xs text-text-tertiary">
            <ArrowsLeftRight size={14} />
            Same desktop app, two transfer lanes
          </div>
        </div>
      </motion.div>
    </div>
  )
}

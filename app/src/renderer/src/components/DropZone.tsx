import { useCallback, useRef, useState } from "react"
import { CloudArrowUp, FolderOpen } from "@phosphor-icons/react"
import { motion } from "motion/react"
import { describeUnsupportedSource, detectSourceFormat, FORMAT_META, SOURCE_FORMATS } from "../conversion"
import DAWMark from "./DAWMark"

interface DropZoneProps {
  onProjectSelected: (path: string) => void
}

const browseButtonClass =
  "inline-flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-2.5 text-[13px] font-medium text-text-primary transition-colors hover:border-stone/50 hover:bg-surface-hover"

export default function DropZone({ onProjectSelected }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [dropError, setDropError] = useState<string | null>(null)
  const dragCounter = useRef(0)

  const handleDragEnter = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
    dragCounter.current += 1
    setDropError(null)
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
    dragCounter.current -= 1
    if (dragCounter.current === 0) setIsDragging(false)
  }, [])

  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
  }, [])

  const acceptPath = useCallback((path: string) => {
    if (detectSourceFormat(path)) {
      onProjectSelected(path)
      return
    }
    setDropError(describeUnsupportedSource(path))
  }, [onProjectSelected])

  const handleDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
    dragCounter.current = 0
    setIsDragging(false)
    setDropError(null)

    const file = event.dataTransfer.files[0]
    if (!file) {
      setDropError("Drop a session file or a Logic project folder.")
      return
    }
    // Electron removed File.path in v32; webUtils resolves the native path.
    acceptPath(window.api.getPathForFile(file))
  }, [acceptPath])

  const handleBrowse = async (kind: "file" | "folder") => {
    setDropError(null)
    const path = await window.api.selectSource(kind)
    if (path) acceptPath(path)
  }

  const isMac = window.api.platform === "darwin"

  return (
    <div
      className="flex-1 flex items-center justify-center p-8"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <motion.div
        role="button"
        tabIndex={0}
        aria-label="Select a session to convert"
        animate={{
          borderColor: isDragging ? "#C4868E" : "#353340",
          scale: isDragging ? 1.008 : 1,
        }}
        transition={{ type: "spring", stiffness: 380, damping: 30 }}
        className="dropzone-texture relative isolate w-full max-w-2xl overflow-hidden rounded-3xl border-2 border-dashed px-10 py-14 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose"
        onClick={() => void handleBrowse("file")}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault()
            void handleBrowse("file")
          }
        }}
      >
        <div className="relative z-10 flex flex-col items-center gap-6 text-center">
          <motion.div
            animate={{ y: isDragging ? -5 : 0, scale: isDragging ? 1.05 : 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 22 }}
            className="grid size-16 place-items-center rounded-2xl border border-border bg-surface/90 text-stone shadow-[0_16px_45px_rgba(0,0,0,0.22)]"
          >
            <CloudArrowUp size={34} weight="duotone" />
          </motion.div>

          <div className="max-w-lg">
            <h1 className="text-xl font-semibold tracking-[-0.02em]">Drop a session</h1>
            <p className="mt-2 text-[13px] text-text-secondary">
              {isMac
                ? "We’ll detect the source, inspect the arrangement, then let you choose where it goes."
                : "Drop a .logicx folder or an .als, .ptx, .pts, or .ptf file. We’ll detect the source, inspect the arrangement, then let you choose where it goes."}
            </p>
            {dropError && <p className="mt-3 text-[13px] text-error">{dropError}</p>}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2" aria-label="Supported formats">
            {SOURCE_FORMATS.map((format) => (
              <span
                key={format}
                className="inline-flex items-center gap-2 rounded-full border border-border bg-bg/75 px-3 py-2 text-[11px] text-text-secondary backdrop-blur-sm"
              >
                <DAWMark format={format} size={17} className="text-stone" />
                <span className="font-medium text-text-primary">{FORMAT_META[format].shortName}</span>
                <span>{FORMAT_META[format].extension}</span>
              </span>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                void handleBrowse("file")
              }}
              className={browseButtonClass}
            >
              <FolderOpen size={16} />
              Browse sessions
            </button>
            {!isMac && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  void handleBrowse("folder")
                }}
                className={browseButtonClass}
              >
                <FolderOpen size={16} />
                Browse Logic project
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  )
}

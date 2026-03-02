import { useState, useCallback, useRef } from "react"
import { motion } from "motion/react"
import { CloudArrowUp, FolderOpen } from "@phosphor-icons/react"

interface DropZoneProps {
  onProjectSelected: (path: string) => void
}

export default function DropZone({ onProjectSelected }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const dragCounter = useRef(0)

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current++
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current--
    if (dragCounter.current === 0) setIsDragging(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      dragCounter.current = 0
      setIsDragging(false)

      const files = e.dataTransfer.files
      if (files.length > 0) {
        const file = files[0] as File & { path?: string }
        const path = file.path
        if (path && path.endsWith(".logicx")) {
          onProjectSelected(path)
        }
      }
    },
    [onProjectSelected],
  )

  const handleBrowse = async () => {
    const path = await window.api.selectLogicx()
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
        className="w-full max-w-lg border-2 border-dashed rounded-2xl p-12 flex flex-col items-center gap-4 cursor-pointer"
        onClick={handleBrowse}
      >
        <motion.div
          animate={{ y: isDragging ? -4 : 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
        >
          <CloudArrowUp size={48} weight="duotone" className="text-stone" />
        </motion.div>

        <div className="text-center">
          <p className="text-text-primary text-base font-medium mb-1">
            Drop a .logicx project here
          </p>
          <p className="text-text-secondary text-sm">
            or click to browse
          </p>
        </div>

        <button
          onClick={(e) => { e.stopPropagation(); handleBrowse() }}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface hover:bg-surface-hover border border-border text-sm text-text-secondary hover:text-text-primary transition-colors"
        >
          <FolderOpen size={16} />
          Browse
        </button>
      </motion.div>
    </div>
  )
}

import { useEffect, useState, useCallback } from "react"
import { AnimatePresence, motion } from "motion/react"
import { useAppState } from "./hooks/useAppState"
import Sidebar from "./components/Sidebar"
import DropZone from "./components/DropZone"
import ProjectPreview from "./components/ProjectPreview"
import ConversionProgress from "./components/ConversionProgress"
import ConversionComplete from "./components/ConversionComplete"

export default function App() {
  const state = useAppState()
  const [previewLoading, setPreviewLoading] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)

  // Load history on mount
  useEffect(() => {
    window.api.getHistory().then(state.setHistory)
  }, [])

  // Handle project selection → preview
  const handleProjectSelected = useCallback(async (path: string) => {
    state.setLogicxPath(path)
    state.setView("preview")
    setPreviewLoading(true)

    const cleanup = window.api.onPreviewProgress((event) => {
      if (event.stage === "complete") {
        state.setPreview({
          projectName: path.split(/[/\\]/).pop()?.replace(".logicx", "") || "Unknown",
          tracks: event.tracks || 0,
          audioFiles: event.audio_files || 0,
          plugins: event.plugins || 0,
          report: event.report || "",
        })
        setPreviewLoading(false)
      }
    })

    await window.api.startPreview(path)
    return cleanup
  }, [])

  // Handle output dir selection
  const handleSelectOutputDir = async () => {
    const dir = await window.api.selectOutputDir()
    if (dir) state.setOutputDir(dir)
  }

  // Handle conversion
  const handleConvert = useCallback(async () => {
    if (!state.logicxPath || !state.outputDir) return

    state.setView("converting")
    state.setProgress(0)
    setLogs([])

    const cleanupProgress = window.api.onProgress((event) => {
      state.setProgress(event.progress)
      state.setProgressMessage(event.message)
      state.setProgressStage(event.stage)
      setLogs((prev) => [...prev, event.message])

      if (event.stage === "complete" && event.als_path) {
        state.setResult({
          alsPath: event.als_path,
          report: event.report || "",
          tracks: event.tracks || 0,
          clips: event.clips || 0,
          audioFiles: event.audio_files || 0,
        })
        state.setView("complete")

        // Save to history
        const record: ConversionRecord = {
          id: crypto.randomUUID(),
          projectName: state.preview?.projectName || "Unknown",
          inputPath: state.logicxPath!,
          outputPath: event.als_path,
          date: new Date().toISOString(),
          status: "success",
          report: event.report || "",
          stats: {
            tracks: event.tracks || 0,
            clips: event.clips || 0,
            audioFiles: event.audio_files || 0,
          },
        }
        window.api.addHistory(record)
        state.setHistory((prev) => [record, ...prev])
      }

      if (event.stage === "error") {
        state.setError(event.message)
        state.setView("error")
      }
    })

    const cleanupError = window.api.onError((err) => {
      setLogs((prev) => [...prev, `ERROR: ${err}`])
    })

    const cleanupExit = window.api.onExit((code) => {
      if (code !== 0 && state.view === "converting") {
        state.setError(`Converter exited with code ${code}`)
        state.setView("error")

        const record: ConversionRecord = {
          id: crypto.randomUUID(),
          projectName: state.preview?.projectName || "Unknown",
          inputPath: state.logicxPath!,
          outputPath: "",
          date: new Date().toISOString(),
          status: "failed",
          report: logs.join("\n"),
        }
        window.api.addHistory(record)
        state.setHistory((prev) => [record, ...prev])
      }
      cleanupProgress()
      cleanupError()
      cleanupExit()
    })

    await window.api.startConversion(state.logicxPath, state.outputDir)
  }, [state.logicxPath, state.outputDir, state.preview])

  // Handle history record selection
  const handleSelectRecord = (record: ConversionRecord) => {
    setSelectedHistoryId(record.id)
    if (record.status === "success") {
      state.setResult({
        alsPath: record.outputPath,
        report: record.report,
        tracks: record.stats?.tracks || 0,
        clips: record.stats?.clips || 0,
        audioFiles: record.stats?.audioFiles || 0,
      })
      state.setView("complete")
    } else {
      state.setError(record.report)
      state.setView("error")
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        history={state.history}
        onNewConversion={state.reset}
        onSelectRecord={handleSelectRecord}
        selectedId={selectedHistoryId}
      />

      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Titlebar drag region */}
        <div className="h-8 shrink-0" style={{ WebkitAppRegion: "drag" } as React.CSSProperties} />

        <AnimatePresence mode="wait">
          {state.view === "empty" && (
            <motion.div
              key="empty"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex-1 flex"
            >
              <DropZone onProjectSelected={handleProjectSelected} />
            </motion.div>
          )}

          {state.view === "preview" && (
            <motion.div
              key="preview"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex-1 flex"
            >
              <ProjectPreview
                logicxPath={state.logicxPath!}
                preview={state.preview}
                outputDir={state.outputDir}
                onSelectOutputDir={handleSelectOutputDir}
                onConvert={handleConvert}
                loading={previewLoading}
              />
            </motion.div>
          )}

          {state.view === "converting" && (
            <motion.div
              key="converting"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex-1 flex"
            >
              <ConversionProgress
                stage={state.progressStage}
                progress={state.progress}
                message={state.progressMessage}
                logs={logs}
              />
            </motion.div>
          )}

          {(state.view === "complete" || state.view === "error") && (
            <motion.div
              key="complete"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex-1 flex"
            >
              <ConversionComplete
                result={state.result}
                error={state.error}
                onConvertAnother={state.reset}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  )
}

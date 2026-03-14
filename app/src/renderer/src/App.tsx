import { useEffect, useRef, useState, type CSSProperties, type MutableRefObject } from "react"
import { AnimatePresence, motion } from "motion/react"
import ConversionComplete from "./components/ConversionComplete"
import ConversionProgress from "./components/ConversionProgress"
import DropZone from "./components/DropZone"
import ProjectPreview from "./components/ProjectPreview"
import Sidebar from "./components/Sidebar"
import { useAppState, type ConversionDirection, type ConversionRecord } from "./hooks/useAppState"

type CleanupRef = MutableRefObject<(() => void) | null>

function artifactPathFromEvent(event: ProgressEvent): string | null {
  return event.artifact_path || event.als_path || null
}

function nameFromPath(path: string): string {
  return path.split(/[/\\]/).pop() || "Unknown"
}

export default function App() {
  const state = useAppState()
  const [previewLoading, setPreviewLoading] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)
  const previewCleanupRef = useRef<(() => void) | null>(null)
  const conversionCleanupRef = useRef<(() => void) | null>(null)
  const logsRef = useRef<string[]>([])

  const cleanupListeners = (ref: CleanupRef) => {
    ref.current?.()
    ref.current = null
  }

  const appendLog = (message: string) => {
    logsRef.current = [...logsRef.current, message]
    setLogs(logsRef.current)
  }

  const persistHistory = async (record: ConversionRecord) => {
    try {
      state.setHistory(await window.api.addHistory(record))
    } catch {
      state.setHistory((prev) => [record, ...prev].slice(0, 100))
    }
  }

  const abortActiveWork = () => {
    cleanupListeners(previewCleanupRef)
    cleanupListeners(conversionCleanupRef)
    void window.api.cancelActiveJob()
  }

  useEffect(() => {
    void window.api.getHistory().then(state.setHistory)
  }, [])

  useEffect(() => {
    return () => {
      cleanupListeners(previewCleanupRef)
      cleanupListeners(conversionCleanupRef)
    }
  }, [])

  const handleDirectionChange = (direction: ConversionDirection) => {
    setSelectedHistoryId(null)
    abortActiveWork()
    setPreviewLoading(false)
    logsRef.current = []
    setLogs([])
    state.reset(direction)
  }

  const handleProjectSelected = async (path: string) => {
    cleanupListeners(previewCleanupRef)
    state.setSourcePath(path)
    state.setPreview(null)
    state.setError(null)
    state.setView("preview")
    setPreviewLoading(true)

    let settled = false
    const cleanups = [
      window.api.onPreviewProgress((event) => {
        if (event.stage !== "complete" || settled) return
        settled = true
        state.setPreview({
          direction: state.direction,
          projectName: nameFromPath(path).replace(/\.(logicx|als)$/i, ""),
          tracks: event.tracks || 0,
          clips: event.clips || event.audio_files || 0,
          audioFiles: event.audio_files || 0,
          plugins: event.plugins,
          locators: event.locators,
          report: event.report || "",
        })
        setPreviewLoading(false)
        cleanupListeners(previewCleanupRef)
      }),
      window.api.onPreviewError((error) => {
        if (settled) return
        settled = true
        setPreviewLoading(false)
        state.setError(error)
        state.setView("error")
        cleanupListeners(previewCleanupRef)
      }),
      window.api.onPreviewExit((code) => {
        if (!settled && code !== 0) {
          settled = true
          setPreviewLoading(false)
          state.setError(`Preview failed with exit code ${code}`)
          state.setView("error")
        }
        cleanupListeners(previewCleanupRef)
      }),
    ]

    previewCleanupRef.current = () => {
      for (const cleanup of cleanups) cleanup()
    }

    try {
      await window.api.startPreview(state.direction, path)
    } catch (error) {
      if (!settled) {
        setPreviewLoading(false)
        state.setError(error instanceof Error ? error.message : String(error))
        state.setView("error")
      }
      cleanupListeners(previewCleanupRef)
    }
  }

  const handleSelectOutputDir = async () => {
    const dir = await window.api.selectOutputDir()
    if (dir) state.setOutputDir(dir)
  }

  const handleConvert = async () => {
    if (!state.sourcePath || !state.outputDir || conversionCleanupRef.current) return

    cleanupListeners(conversionCleanupRef)
    state.setView("converting")
    state.setProgress(0)
    state.setProgressMessage("")
    state.setProgressStage("")
    state.setError(null)
    state.setResult(null)
    logsRef.current = []
    setLogs([])

    let outcome: "pending" | "success" | "failed" = "pending"

    const recordFailure = (message: string) => {
      if (outcome !== "pending") return
      outcome = "failed"
      state.setError(message)
      state.setView("error")

      const record: ConversionRecord = {
        id: crypto.randomUUID(),
        direction: state.direction,
        projectName: state.preview?.projectName || "Unknown",
        inputPath: state.sourcePath!,
        outputPath: "",
        date: new Date().toISOString(),
        status: "failed",
        report: message,
      }
      void persistHistory(record)
    }

    const cleanups = [
      window.api.onProgress((event) => {
        state.setProgress(event.progress)
        state.setProgressMessage(event.message)
        state.setProgressStage(event.stage)
        appendLog(event.message)

        const artifactPath = artifactPathFromEvent(event)
        if (event.stage === "complete" && artifactPath && outcome === "pending") {
          outcome = "success"
          state.setResult({
            direction: state.direction,
            artifactPath,
            report: event.report || "",
            tracks: event.tracks || 0,
            clips: event.clips || 0,
            audioFiles: event.audio_files || 0,
            locators: event.locators,
          })
          state.setView("complete")

          const record: ConversionRecord = {
            id: crypto.randomUUID(),
            direction: state.direction,
            projectName: state.preview?.projectName || "Unknown",
            inputPath: state.sourcePath!,
            outputPath: artifactPath,
            date: new Date().toISOString(),
            status: "success",
            report: event.report || "",
            stats: {
              tracks: event.tracks || 0,
              clips: event.clips,
              audioFiles: event.audio_files || 0,
              locators: event.locators,
            },
          }
          void persistHistory(record)
        }

        if (event.stage === "error") {
          recordFailure(event.report || event.message)
        }
      }),
      window.api.onError((error) => {
        appendLog(`ERROR: ${error}`)
      }),
      window.api.onExit((code) => {
        if (code !== 0) {
          const failureMessage = [`Converter exited with code ${code}`, logsRef.current.join("\n")]
            .filter(Boolean)
            .join("\n\n")
          recordFailure(failureMessage)
        }
        cleanupListeners(conversionCleanupRef)
      }),
    ]

    conversionCleanupRef.current = () => {
      for (const cleanup of cleanups) cleanup()
    }

    try {
      await window.api.startConversion(state.direction, state.sourcePath, state.outputDir)
    } catch (error) {
      recordFailure(error instanceof Error ? error.message : String(error))
      cleanupListeners(conversionCleanupRef)
    }
  }

  const handleSelectRecord = (record: ConversionRecord) => {
    abortActiveWork()
    setSelectedHistoryId(record.id)
    setPreviewLoading(false)
    logsRef.current = []
    setLogs([])
    state.setDirection(record.direction)

    if (record.status === "success") {
      state.setResult({
        direction: record.direction,
        artifactPath: record.outputPath,
        report: record.report,
        tracks: record.stats?.tracks || 0,
        clips: record.stats?.clips || 0,
        audioFiles: record.stats?.audioFiles || 0,
        locators: record.stats?.locators,
      })
      state.setView("complete")
      return
    }

    state.setError(record.report)
    state.setView("error")
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        history={state.history}
        direction={state.direction}
        onNewConversion={() => handleDirectionChange(state.direction)}
        onSelectRecord={handleSelectRecord}
        selectedId={selectedHistoryId}
      />

      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="h-8 shrink-0" style={{ WebkitAppRegion: "drag" } as CSSProperties} />

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
              <DropZone
                direction={state.direction}
                onDirectionChange={handleDirectionChange}
                onProjectSelected={handleProjectSelected}
              />
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
                sourcePath={state.sourcePath!}
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
                direction={state.direction}
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
                onConvertAnother={() => handleDirectionChange(state.direction)}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  )
}

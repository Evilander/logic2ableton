import { useEffect, useRef, useState, type CSSProperties, type MutableRefObject } from "react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import ConversionComplete from "./components/ConversionComplete"
import ConversionProgress from "./components/ConversionProgress"
import DropZone from "./components/DropZone"
import ProjectPreview from "./components/ProjectPreview"
import Sidebar from "./components/Sidebar"
import type { ConversionDirection } from "./conversion"
import {
  defaultDirectionForSource,
  destinationForDirection,
  detectSourceFormat,
  isProToolsSource,
} from "./conversion"
import { useAppState, type ConversionRecord } from "./hooks/useAppState"

type CleanupRef = MutableRefObject<(() => void) | null>

function outputPathFromEvent(event: ProgressEvent, direction: ConversionDirection): string | null {
  if (destinationForDirection(direction) === "ableton") {
    return event.als_path || event.artifact_path || null
  }
  return event.package_path || event.artifact_path || null
}

function nameFromPath(path: string): string {
  return (path.split(/[/\\]/).pop() || "Unknown").replace(/\.(logicx|als|ptx|pts|ptf)$/i, "")
}

export default function App() {
  const state = useAppState()
  const reduceMotion = useReducedMotion()
  const [previewLoading, setPreviewLoading] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null)
  const previewCleanupRef = useRef<(() => void) | null>(null)
  const conversionCleanupRef = useRef<(() => void) | null>(null)
  const previewRequestRef = useRef(0)
  const conversionStartingRef = useRef(false)
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
      state.setHistory((current) => [record, ...current].slice(0, 100))
    }
  }

  const abortActiveWork = () => {
    previewRequestRef.current += 1
    cleanupListeners(previewCleanupRef)
    cleanupListeners(conversionCleanupRef)
    void window.api.cancelActiveJob().catch(() => {})
  }

  useEffect(() => {
    void window.api.getHistory().then(state.setHistory)
  }, [])

  useEffect(() => {
    return () => {
      previewRequestRef.current += 1
      cleanupListeners(previewCleanupRef)
      cleanupListeners(conversionCleanupRef)
      void window.api.cancelActiveJob().catch(() => {})
    }
  }, [])

  const runPreview = async (path: string, direction: ConversionDirection, tempo: number) => {
    const requestId = previewRequestRef.current + 1
    previewRequestRef.current = requestId
    cleanupListeners(previewCleanupRef)
    try {
      await window.api.cancelActiveJob()
    } catch (error) {
      if (previewRequestRef.current === requestId) {
        state.setError(error instanceof Error ? error.message : String(error))
        state.setView("error")
        setPreviewLoading(false)
      }
      return
    }
    if (previewRequestRef.current !== requestId) return

    state.setPreview(null)
    state.setError(null)
    state.setView("preview")
    setPreviewLoading(true)

    let settled = false
    const failPreview = (message: string) => {
      if (settled || previewRequestRef.current !== requestId) return
      settled = true
      setPreviewLoading(false)
      state.setError(message)
      state.setView("error")
      cleanupListeners(previewCleanupRef)
    }

    const cleanups = [
      window.api.onPreviewProgress((event) => {
        if (previewRequestRef.current !== requestId || settled) return
        if (event.stage === "error") {
          failPreview(event.report || event.message)
          return
        }
        if (event.stage !== "complete") return

        settled = true
        state.setPreview({
          projectName: nameFromPath(path),
          tracks: event.tracks ?? 0,
          clips: event.clips,
          audioFiles: event.audio_files ?? 0,
          plugins: event.plugins,
          midiNotes: event.midi_notes,
          report: event.report ?? "",
        })
        setPreviewLoading(false)
        cleanupListeners(previewCleanupRef)
      }),
      window.api.onPreviewError((error) => failPreview(error)),
      window.api.onPreviewExit((code) => {
        if (!settled) {
          failPreview(code === 0
            ? "Preview ended without returning a report."
            : `Preview failed with exit code ${code}`)
        }
        if (previewRequestRef.current === requestId) cleanupListeners(previewCleanupRef)
      }),
    ]

    previewCleanupRef.current = () => {
      for (const cleanup of cleanups) cleanup()
    }

    try {
      await window.api.startPreview(direction, path, isProToolsSource(direction) ? tempo : undefined)
    } catch (error) {
      failPreview(error instanceof Error ? error.message : String(error))
    }
  }

  const handleProjectSelected = (path: string) => {
    const sourceFormat = detectSourceFormat(path)
    if (!sourceFormat) {
      state.setError("Choose a supported Logic, Ableton, or Pro Tools session.")
      state.setView("error")
      return
    }

    const direction = defaultDirectionForSource(sourceFormat)
    setSelectedHistoryId(null)
    cleanupListeners(conversionCleanupRef)
    logsRef.current = []
    setLogs([])
    state.setDirection(direction)
    state.setSourcePath(path)
    state.setOutputDir(null)
    state.setTempo(120)
    state.setResult(null)
    void runPreview(path, direction, 120)
  }

  const handleDirectionChange = (direction: ConversionDirection) => {
    if (!state.sourcePath || direction === state.direction) return
    setSelectedHistoryId(null)
    state.setDirection(direction)
    void runPreview(state.sourcePath, direction, state.tempo)
  }

  const handleTempoChange = (tempo: number) => {
    state.setTempo(tempo)
    if (state.sourcePath) void runPreview(state.sourcePath, state.direction, tempo)
  }

  const handleSelectOutputDir = async () => {
    const directory = await window.api.selectOutputDir()
    if (directory) state.setOutputDir(directory)
  }

  const handleConvert = async () => {
    if (!state.sourcePath || !state.outputDir || conversionCleanupRef.current || conversionStartingRef.current) return
    conversionStartingRef.current = true

    const direction = state.direction
    const sourcePath = state.sourcePath
    const outputDir = state.outputDir
    const projectName = state.preview?.projectName || nameFromPath(sourcePath)
    const tempo = state.tempo

    const requestId = ++previewRequestRef.current
    cleanupListeners(previewCleanupRef)
    try {
      await window.api.cancelActiveJob()
    } catch (error) {
      if (previewRequestRef.current === requestId) {
        state.setError(error instanceof Error ? error.message : String(error))
        state.setView("error")
      }
      return
    } finally {
      conversionStartingRef.current = false
    }
    if (previewRequestRef.current !== requestId) return

    state.setView("converting")
    state.setProgress(0)
    state.setProgressMessage("Validating session…")
    state.setProgressStage("validation")
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

      void persistHistory({
        id: crypto.randomUUID(),
        direction,
        projectName,
        inputPath: sourcePath,
        outputPath: "",
        date: new Date().toISOString(),
        status: "failed",
        report: message,
      })
    }

    const cleanups = [
      window.api.onProgress((event) => {
        state.setProgress(event.progress)
        state.setProgressMessage(event.message)
        state.setProgressStage(event.stage)
        appendLog(event.message)

        const outputPath = outputPathFromEvent(event, direction)
        if (event.stage === "complete" && outputPath && outcome === "pending") {
          outcome = "success"
          const compatibilityWarnings = event.compatibility_warnings ?? []
          state.setResult({
            direction,
            artifactPath: outputPath,
            report: event.report ?? "",
            tracks: event.tracks ?? 0,
            clips: event.clips ?? 0,
            audioFiles: event.audio_files ?? 0,
            midiNotes: event.midi_notes,
            compatibilityWarnings,
          })
          state.setView("complete")

          void persistHistory({
            id: crypto.randomUUID(),
            direction,
            projectName,
            inputPath: sourcePath,
            outputPath,
            date: new Date().toISOString(),
            status: "success",
            report: event.report ?? "",
            compatibilityWarnings,
            stats: {
              tracks: event.tracks ?? 0,
              clips: event.clips,
              audioFiles: event.audio_files ?? 0,
              midiNotes: event.midi_notes,
            },
          })
        }

        if (event.stage === "error") recordFailure(event.report || event.message)
      }),
      window.api.onError((error) => appendLog(`ERROR: ${error}`)),
      window.api.onExit((code) => {
        if (outcome === "pending") {
          const summary = code === 0
            ? "Converter exited without returning an output path."
            : `Converter exited with code ${code}.`
          const failureMessage = [summary, logsRef.current.join("\n")].filter(Boolean).join("\n\n")
          recordFailure(failureMessage)
        }
        cleanupListeners(conversionCleanupRef)
      }),
    ]

    conversionCleanupRef.current = () => {
      for (const cleanup of cleanups) cleanup()
    }

    try {
      await window.api.startConversion(
        direction,
        sourcePath,
        outputDir,
        isProToolsSource(direction) ? tempo : undefined,
      )
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
    state.setSourcePath(record.inputPath)

    if (record.status === "success") {
      state.setResult({
        direction: record.direction,
        artifactPath: record.outputPath,
        report: record.report,
        tracks: record.stats?.tracks ?? 0,
        clips: record.stats?.clips ?? 0,
        audioFiles: record.stats?.audioFiles ?? 0,
        midiNotes: record.stats?.midiNotes,
        compatibilityWarnings: record.compatibilityWarnings ?? [],
      })
      state.setError(null)
      state.setView("complete")
      return
    }

    state.setResult(null)
    state.setError(record.report)
    state.setView("error")
  }

  const handleNewConversion = () => {
    abortActiveWork()
    setSelectedHistoryId(null)
    setPreviewLoading(false)
    logsRef.current = []
    setLogs([])
    state.reset()
  }

  const viewMotion = {
    initial: reduceMotion ? { opacity: 1 } : { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    exit: reduceMotion ? { opacity: 1 } : { opacity: 0, y: -8 },
    transition: { type: "spring" as const, stiffness: 320, damping: 30 },
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        history={state.history}
        direction={state.sourcePath ? state.direction : null}
        onNewConversion={handleNewConversion}
        onSelectRecord={handleSelectRecord}
        selectedId={selectedHistoryId}
      />

      <main className="flex flex-1 flex-col overflow-hidden">
        <div className="h-8 shrink-0" style={{ WebkitAppRegion: "drag" } as CSSProperties} />

        <AnimatePresence mode="wait">
          {state.view === "empty" && (
            <motion.div key="empty" {...viewMotion} className="flex flex-1">
              <DropZone onProjectSelected={handleProjectSelected} />
            </motion.div>
          )}

          {state.view === "preview" && (
            <motion.div key="preview" {...viewMotion} className="flex flex-1">
              <ProjectPreview
                direction={state.direction}
                sourcePath={state.sourcePath!}
                preview={state.preview}
                outputDir={state.outputDir}
                tempo={state.tempo}
                onDirectionChange={handleDirectionChange}
                onTempoChange={handleTempoChange}
                onSelectOutputDir={handleSelectOutputDir}
                onConvert={() => void handleConvert()}
                loading={previewLoading}
              />
            </motion.div>
          )}

          {state.view === "converting" && (
            <motion.div key="converting" {...viewMotion} className="flex flex-1">
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
            <motion.div key={state.view} {...viewMotion} className="flex flex-1">
              <ConversionComplete
                direction={state.direction}
                result={state.result}
                error={state.error}
                onConvertAnother={handleNewConversion}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  )
}

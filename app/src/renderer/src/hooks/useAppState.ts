import { useState } from "react"
import type { ConversionDirection } from "../conversion"

export type { ConversionDirection, SourceFormat } from "../conversion"
export type AppView = "empty" | "preview" | "converting" | "complete" | "error"

export interface PreviewData {
  projectName: string
  tracks: number
  clips?: number
  audioFiles: number
  plugins?: number
  midiTracks?: number
  midiNotes?: number
  compatibilityWarnings: string[]
  report: string
}

export interface ConversionResult {
  direction: ConversionDirection
  artifactPath: string
  report: string
  tracks: number
  clips: number
  audioFiles: number
  midiTracks?: number
  midiNotes?: number
  compatibilityWarnings: string[]
}

// A field-level preview validation failure. `field` is null for errors that
// aren't tied to one input (e.g. switching destination format), in which
// case the message is shown as a general notice instead of under a field.
export type PreviewSettingsField = "tempo" | "smpteStart" | "keepUnwarped" | "timelinePath" | null

export interface PreviewSettingsError {
  field: PreviewSettingsField
  message: string
}

export interface ConversionRecord {
  id: string
  direction: ConversionDirection
  projectName: string
  inputPath: string
  outputPath: string
  date: string
  status: "success" | "failed"
  report: string
  compatibilityWarnings?: string[]
  stats?: {
    tracks: number
    clips?: number
    audioFiles: number
    midiTracks?: number
    midiNotes?: number
  }
}

export function useAppState() {
  const [direction, setDirection] = useState<ConversionDirection>("logic2ableton")
  const [tempo, setTempo] = useState(120)
  const [smpteStart, setSmpteStart] = useState("01:00:00:00")
  const [keepUnwarped, setKeepUnwarped] = useState("")
  const [timelinePath, setTimelinePath] = useState<string | null>(null)
  const [view, setView] = useState<AppView>("empty")
  const [sourcePath, setSourcePath] = useState<string | null>(null)
  const [outputDir, setOutputDir] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [progress, setProgress] = useState(0)
  const [progressMessage, setProgressMessage] = useState("")
  const [progressStage, setProgressStage] = useState("")
  const [result, setResult] = useState<ConversionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The full persisted report for a failure, kept separate from `error` (the
  // headline) so the desktop can show the specific exception up front and
  // leave the longer report behind a toggle instead of burying one in the other.
  const [errorReport, setErrorReport] = useState<string | null>(null)
  const [history, setHistory] = useState<ConversionRecord[]>([])

  const reset = () => {
    setDirection("logic2ableton")
    setTempo(120)
    setSmpteStart("01:00:00:00")
    setKeepUnwarped("")
    setTimelinePath(null)
    setView("empty")
    setSourcePath(null)
    setOutputDir(null)
    setPreview(null)
    setProgress(0)
    setProgressMessage("")
    setProgressStage("")
    setResult(null)
    setError(null)
    setErrorReport(null)
  }

  return {
    direction, setDirection,
    tempo, setTempo,
    smpteStart, setSmpteStart,
    keepUnwarped, setKeepUnwarped,
    timelinePath, setTimelinePath,
    view, setView,
    sourcePath, setSourcePath,
    outputDir, setOutputDir,
    preview, setPreview,
    progress, setProgress,
    progressMessage, setProgressMessage,
    progressStage, setProgressStage,
    result, setResult,
    error, setError,
    errorReport, setErrorReport,
    history, setHistory,
    reset,
  }
}

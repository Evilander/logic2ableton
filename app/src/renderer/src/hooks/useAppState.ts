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
  midiNotes?: number
  report: string
}

export interface ConversionResult {
  direction: ConversionDirection
  artifactPath: string
  report: string
  tracks: number
  clips: number
  audioFiles: number
  midiNotes?: number
  compatibilityWarnings: string[]
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
    midiNotes?: number
  }
}

export function useAppState() {
  const [direction, setDirection] = useState<ConversionDirection>("logic2ableton")
  const [tempo, setTempo] = useState(120)
  const [view, setView] = useState<AppView>("empty")
  const [sourcePath, setSourcePath] = useState<string | null>(null)
  const [outputDir, setOutputDir] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [progress, setProgress] = useState(0)
  const [progressMessage, setProgressMessage] = useState("")
  const [progressStage, setProgressStage] = useState("")
  const [result, setResult] = useState<ConversionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<ConversionRecord[]>([])

  const reset = () => {
    setDirection("logic2ableton")
    setTempo(120)
    setView("empty")
    setSourcePath(null)
    setOutputDir(null)
    setPreview(null)
    setProgress(0)
    setProgressMessage("")
    setProgressStage("")
    setResult(null)
    setError(null)
  }

  return {
    direction, setDirection,
    tempo, setTempo,
    view, setView,
    sourcePath, setSourcePath,
    outputDir, setOutputDir,
    preview, setPreview,
    progress, setProgress,
    progressMessage, setProgressMessage,
    progressStage, setProgressStage,
    result, setResult,
    error, setError,
    history, setHistory,
    reset,
  }
}

import { useState } from "react"

export type ConversionDirection = "logic2ableton" | "ableton2logic"
export type AppView = "empty" | "preview" | "converting" | "complete" | "error"

export interface PreviewData {
  direction: ConversionDirection
  projectName: string
  tracks: number
  clips: number
  audioFiles: number
  plugins?: number
  locators?: number
  report: string
}

export interface ConversionResult {
  direction: ConversionDirection
  artifactPath: string
  report: string
  tracks: number
  clips: number
  audioFiles: number
  locators?: number
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
  stats?: { tracks: number; clips?: number; audioFiles: number; locators?: number }
}

export function useAppState() {
  const [direction, setDirection] = useState<ConversionDirection>("logic2ableton")
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

  const reset = (nextDirection: ConversionDirection = direction) => {
    setDirection(nextDirection)
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

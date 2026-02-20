import { useState } from "react"

export type AppView = "empty" | "preview" | "converting" | "complete" | "error"

export interface PreviewData {
  projectName: string
  tracks: number
  audioFiles: number
  plugins: number
  report: string
}

export interface ConversionResult {
  alsPath: string
  report: string
  tracks: number
  clips: number
  audioFiles: number
}

export function useAppState() {
  const [view, setView] = useState<AppView>("empty")
  const [logicxPath, setLogicxPath] = useState<string | null>(null)
  const [outputDir, setOutputDir] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [progress, setProgress] = useState(0)
  const [progressMessage, setProgressMessage] = useState("")
  const [progressStage, setProgressStage] = useState("")
  const [result, setResult] = useState<ConversionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<ConversionRecord[]>([])

  const reset = () => {
    setView("empty")
    setLogicxPath(null)
    setOutputDir(null)
    setPreview(null)
    setProgress(0)
    setProgressMessage("")
    setProgressStage("")
    setResult(null)
    setError(null)
  }

  return {
    view, setView,
    logicxPath, setLogicxPath,
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

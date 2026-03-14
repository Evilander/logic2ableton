/// <reference types="vite/client" />

type ConversionDirection = "logic2ableton" | "ableton2logic"

interface ProgressEvent {
  direction?: ConversionDirection
  stage: string
  progress: number
  message: string
  als_path?: string
  artifact_path?: string
  package_path?: string
  report?: string
  report_path?: string
  tracks?: number
  clips?: number
  audio_files?: number
  plugins?: number
  locators?: number
  compatibility_warnings?: string[]
  warning?: string
}

interface ConversionRecord {
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

interface Window {
  api: {
    selectSource: (direction: ConversionDirection) => Promise<string | null>
    selectOutputDir: () => Promise<string | null>
    startConversion: (direction: ConversionDirection, sourcePath: string, outputDir: string) => Promise<void>
    startPreview: (direction: ConversionDirection, sourcePath: string) => Promise<void>
    cancelActiveJob: () => Promise<void>
    openFile: (path: string) => Promise<string>
    showInFolder: (path: string) => Promise<void>
    getHistory: () => Promise<ConversionRecord[]>
    addHistory: (record: ConversionRecord) => Promise<ConversionRecord[]>
    onProgress: (cb: (event: ProgressEvent) => void) => () => void
    onPreviewProgress: (cb: (event: ProgressEvent) => void) => () => void
    onPreviewError: (cb: (error: string) => void) => () => void
    onPreviewExit: (cb: (code: number) => void) => () => void
    onError: (cb: (error: string) => void) => () => void
    onExit: (cb: (code: number) => void) => () => void
  }
}

/// <reference types="vite/client" />

interface ProgressEvent {
  stage: string
  progress: number
  message: string
  als_path?: string
  report?: string
  tracks?: number
  clips?: number
  audio_files?: number
  plugins?: number
}

interface ConversionRecord {
  id: string
  projectName: string
  inputPath: string
  outputPath: string
  date: string
  status: "success" | "failed"
  report: string
  stats?: { tracks: number; clips: number; audioFiles: number }
}

interface Window {
  api: {
    selectLogicx: () => Promise<string | null>
    selectOutputDir: () => Promise<string | null>
    startConversion: (logicxPath: string, outputDir: string) => Promise<void>
    startPreview: (logicxPath: string) => Promise<void>
    openFile: (path: string) => Promise<void>
    showInFolder: (path: string) => Promise<void>
    getHistory: () => Promise<ConversionRecord[]>
    addHistory: (record: ConversionRecord) => Promise<void>
    onProgress: (cb: (event: ProgressEvent) => void) => () => void
    onPreviewProgress: (cb: (event: ProgressEvent) => void) => () => void
    onError: (cb: (error: string) => void) => () => void
    onExit: (cb: (code: number) => void) => () => void
  }
}

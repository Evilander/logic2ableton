import { contextBridge, ipcRenderer } from "electron"

export type ConversionDirection = "logic2ableton" | "ableton2logic"

export interface ProgressEvent {
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

function subscribe<T>(channel: string, cb: (value: T) => void) {
  const handler = (_: unknown, value: T) => cb(value)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api = {
  selectSource: (direction: ConversionDirection): Promise<string | null> =>
    ipcRenderer.invoke("select-source", direction),
  selectOutputDir: (): Promise<string | null> => ipcRenderer.invoke("select-output-dir"),
  startConversion: (
    direction: ConversionDirection,
    sourcePath: string,
    outputDir: string,
  ): Promise<void> => ipcRenderer.invoke("start-conversion", direction, sourcePath, outputDir),
  startPreview: (direction: ConversionDirection, sourcePath: string): Promise<void> =>
    ipcRenderer.invoke("start-preview", direction, sourcePath),
  cancelActiveJob: (): Promise<void> => ipcRenderer.invoke("cancel-active-job"),
  openFile: (path: string): Promise<string> => ipcRenderer.invoke("open-file", path),
  showInFolder: (path: string): Promise<void> => ipcRenderer.invoke("show-in-folder", path),
  getHistory: (): Promise<ConversionRecord[]> => ipcRenderer.invoke("get-history"),
  addHistory: (record: ConversionRecord): Promise<ConversionRecord[]> => ipcRenderer.invoke("add-history", record),
  onProgress: (cb: (event: ProgressEvent) => void) => subscribe("conversion-progress", cb),
  onPreviewProgress: (cb: (event: ProgressEvent) => void) => subscribe("preview-progress", cb),
  onPreviewError: (cb: (error: string) => void) => subscribe("preview-error", cb),
  onPreviewExit: (cb: (code: number) => void) => subscribe("preview-exit", cb),
  onError: (cb: (error: string) => void) => subscribe("conversion-error", cb),
  onExit: (cb: (code: number) => void) => subscribe("conversion-exit", cb),
}

contextBridge.exposeInMainWorld("api", api)

export type API = typeof api

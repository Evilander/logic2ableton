import { contextBridge, ipcRenderer } from "electron"

export interface ProgressEvent {
  stage: string
  progress: number
  message: string
  als_path?: string
  report?: string
  report_path?: string
  tracks?: number
  clips?: number
  audio_files?: number
  plugins?: number
}

export interface ConversionRecord {
  id: string
  projectName: string
  inputPath: string
  outputPath: string
  date: string
  status: "success" | "failed"
  report: string
  stats?: { tracks: number; clips?: number; audioFiles: number }
}

function subscribe<T>(channel: string, cb: (value: T) => void) {
  const handler = (_: unknown, value: T) => cb(value)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api = {
  selectLogicx: (): Promise<string | null> => ipcRenderer.invoke("select-logicx"),
  selectOutputDir: (): Promise<string | null> => ipcRenderer.invoke("select-output-dir"),
  startConversion: (logicxPath: string, outputDir: string): Promise<void> =>
    ipcRenderer.invoke("start-conversion", logicxPath, outputDir),
  startPreview: (logicxPath: string): Promise<void> =>
    ipcRenderer.invoke("start-preview", logicxPath),
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

import { contextBridge, ipcRenderer } from "electron"

export interface ProgressEvent {
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

export interface ConversionRecord {
  id: string
  projectName: string
  inputPath: string
  outputPath: string
  date: string
  status: "success" | "failed"
  report: string
  stats?: { tracks: number; clips: number; audioFiles: number }
}

const api = {
  selectLogicx: (): Promise<string | null> => ipcRenderer.invoke("select-logicx"),
  selectOutputDir: (): Promise<string | null> => ipcRenderer.invoke("select-output-dir"),
  startConversion: (logicxPath: string, outputDir: string): Promise<void> =>
    ipcRenderer.invoke("start-conversion", logicxPath, outputDir),
  startPreview: (logicxPath: string): Promise<void> =>
    ipcRenderer.invoke("start-preview", logicxPath),
  openFile: (path: string): Promise<void> => ipcRenderer.invoke("open-file", path),
  showInFolder: (path: string): Promise<void> => ipcRenderer.invoke("show-in-folder", path),
  getHistory: (): Promise<ConversionRecord[]> => ipcRenderer.invoke("get-history"),
  addHistory: (record: ConversionRecord): Promise<void> => ipcRenderer.invoke("add-history", record),

  onProgress: (cb: (event: ProgressEvent) => void) => {
    const handler = (_: unknown, event: ProgressEvent) => cb(event)
    ipcRenderer.on("conversion-progress", handler)
    return () => ipcRenderer.removeListener("conversion-progress", handler)
  },
  onPreviewProgress: (cb: (event: ProgressEvent) => void) => {
    const handler = (_: unknown, event: ProgressEvent) => cb(event)
    ipcRenderer.on("preview-progress", handler)
    return () => ipcRenderer.removeListener("preview-progress", handler)
  },
  onError: (cb: (error: string) => void) => {
    const handler = (_: unknown, error: string) => cb(error)
    ipcRenderer.on("conversion-error", handler)
    return () => ipcRenderer.removeListener("conversion-error", handler)
  },
  onExit: (cb: (code: number) => void) => {
    const handler = (_: unknown, code: number) => cb(code)
    ipcRenderer.on("conversion-exit", handler)
    return () => ipcRenderer.removeListener("conversion-exit", handler)
  },
}

contextBridge.exposeInMainWorld("api", api)

export type API = typeof api

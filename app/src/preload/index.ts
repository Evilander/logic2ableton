import { contextBridge, ipcRenderer, webUtils } from "electron"

export type ConversionDirection =
  | "logic2ableton"
  | "ableton2logic"
  | "protools2ableton"
  | "protools2logic"
  | "ableton2protools"
  | "logic2protools"

export type SourceFormat = "logic" | "ableton" | "protools"

export type SourceSelectionKind = "file" | "folder"

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
  midi_tracks?: number
  midi_notes?: number
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
  compatibilityWarnings?: string[]
  stats?: {
    tracks: number
    clips?: number
    audioFiles: number
    midiNotes?: number
  }
}

function subscribe<T>(channel: string, cb: (value: T) => void) {
  const handler = (_: unknown, value: T) => cb(value)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api = {
  selectSource: (kind: SourceSelectionKind): Promise<string | null> => ipcRenderer.invoke("select-source", kind),
  getPathForFile: (file: File): string => webUtils.getPathForFile(file),
  platform: process.platform,
  selectOutputDir: (): Promise<string | null> => ipcRenderer.invoke("select-output-dir"),
  startConversion: (
    direction: ConversionDirection,
    sourcePath: string,
    outputDir: string,
    tempo?: number,
  ): Promise<void> => ipcRenderer.invoke("start-conversion", direction, sourcePath, outputDir, tempo),
  startPreview: (direction: ConversionDirection, sourcePath: string, tempo?: number): Promise<void> =>
    ipcRenderer.invoke("start-preview", direction, sourcePath, tempo),
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

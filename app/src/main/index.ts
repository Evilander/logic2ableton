import type { ChildProcess } from "node:child_process"
import type { IpcMainInvokeEvent } from "electron"
import { app, BrowserWindow, dialog, ipcMain, shell } from "electron"
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs"
import { dirname, extname, join, normalize } from "node:path"
import type { ConversionDirection, ProgressEvent } from "./converter"
import { CONVERSION_DIRECTIONS, runConversion } from "./converter"

interface ConversionStats {
  tracks: number
  clips?: number
  audioFiles: number
  midiNotes?: number
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
  compatibilityWarnings?: string[]
  stats?: ConversionStats
}

type StoredConversionRecord = Omit<ConversionRecord, "direction"> & {
  direction?: ConversionDirection
}

const HISTORY_LIMIT = 100
const ALLOWED_OPEN_EXTENSIONS = new Set([".als", ".txt", ".md", ".json", ".csv"])
const SUPPORTED_SOURCE_EXTENSIONS = new Set([".logicx", ".als", ".ptx", ".pts"])
const CONVERSION_DIRECTION_SET = new Set<ConversionDirection>(CONVERSION_DIRECTIONS)

let mainWindow: BrowserWindow | null = null
let activeJob: { kind: "conversion" | "preview"; child: ChildProcess } | null = null
const approvedPaths = new Set<string>()

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#1A1820",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }))
  mainWindow.webContents.on("will-navigate", (event) => event.preventDefault())

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(join(__dirname, "../renderer/index.html"))
  }
}

function previewOutputDir(direction: ConversionDirection): string {
  const outputDir = join(app.getPath("temp"), "logic2ableton-preview", direction)
  mkdirSync(outputDir, { recursive: true })
  return outputDir
}

function getHistoryPath(): string {
  return join(app.getPath("userData"), "conversion-history.json")
}

function normalizePathInput(filePath: string): string {
  if (typeof filePath !== "string" || !filePath.trim()) {
    throw new Error("A valid file path is required")
  }
  return normalize(filePath.trim())
}

function isConversionDirection(value: unknown): value is ConversionDirection {
  return typeof value === "string" && CONVERSION_DIRECTION_SET.has(value as ConversionDirection)
}

function normalizeTempo(value: unknown): number | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value !== "number" || !Number.isFinite(value) || value < 20 || value > 999) {
    throw new Error("Tempo must be a number between 20 and 999 BPM")
  }
  return value
}

function approvePath(filePath: string | null | undefined): void {
  if (!filePath) return
  approvedPaths.add(normalizePathInput(filePath))
}

function assertApprovedPath(filePath: string): string {
  const normalized = normalizePathInput(filePath)
  if (!approvedPaths.has(normalized)) {
    throw new Error("Path is not available for this operation")
  }
  const extension = extname(normalized).toLowerCase()
  if (extension && !ALLOWED_OPEN_EXTENSIONS.has(extension)) {
    throw new Error("Unsupported file type")
  }
  return normalized
}

function clearActiveJob(child?: ChildProcess | null): void {
  if (!activeJob) return
  if (!child || activeJob.child.pid === child.pid) {
    activeJob = null
  }
}

function stopActiveJob(): void {
  const current = activeJob
  activeJob = null
  if (!current || current.child.killed) return
  current.child.kill()
}

function startJob(
  kind: "conversion" | "preview",
  event: IpcMainInvokeEvent,
  direction: ConversionDirection,
  sourcePath: string,
  outputDir: string,
  reportOnly = false,
  tempo?: number,
): void {
  if (activeJob) {
    throw new Error(`${activeJob.kind === "preview" ? "Preview" : "Conversion"} already in progress`)
  }

  const progressChannel = kind === "preview" ? "preview-progress" : "conversion-progress"
  const errorChannel = kind === "preview" ? "preview-error" : "conversion-error"
  const exitChannel = kind === "preview" ? "preview-exit" : "conversion-exit"

  let child: ChildProcess | null = null
  child = runConversion(
    direction,
    normalizePathInput(sourcePath),
    normalizePathInput(outputDir),
    (progress: ProgressEvent) => {
      approvePath(progress.als_path)
      approvePath(progress.artifact_path)
      approvePath(progress.package_path)
      approvePath(progress.report_path)
      event.sender.send(progressChannel, progress)
    },
    (error) => event.sender.send(errorChannel, error),
    (code) => {
      clearActiveJob(child)
      event.sender.send(exitChannel, code)
    },
    reportOnly,
    tempo,
  )

  if (!child) return
  activeJob = { kind, child }
}

function isConversionStats(value: unknown): value is ConversionStats {
  if (!value || typeof value !== "object") return false
  const stats = value as Partial<ConversionStats>
  return typeof stats.tracks === "number"
    && typeof stats.audioFiles === "number"
    && (stats.clips === undefined || typeof stats.clips === "number")
    && (stats.midiNotes === undefined || typeof stats.midiNotes === "number")
}

function isStoredConversionRecord(value: unknown): value is StoredConversionRecord {
  if (!value || typeof value !== "object") return false
  const record = value as Partial<StoredConversionRecord>
  return typeof record.id === "string"
    && (record.direction === undefined || isConversionDirection(record.direction))
    && typeof record.projectName === "string"
    && typeof record.inputPath === "string"
    && typeof record.outputPath === "string"
    && typeof record.date === "string"
    && (record.status === "success" || record.status === "failed")
    && typeof record.report === "string"
    && (record.compatibilityWarnings === undefined
      || (Array.isArray(record.compatibilityWarnings)
        && record.compatibilityWarnings.every((warning) => typeof warning === "string")))
    && (record.stats === undefined || isConversionStats(record.stats))
}

function isConversionRecord(value: unknown): value is ConversionRecord {
  return isStoredConversionRecord(value) && isConversionDirection(value.direction)
}

function normalizeRecord(record: StoredConversionRecord): ConversionRecord {
  return {
    ...record,
    direction: record.direction ?? "logic2ableton",
  }
}

function readHistory(): ConversionRecord[] {
  const historyPath = getHistoryPath()
  try {
    const parsed = JSON.parse(readFileSync(historyPath, "utf-8"))
    if (!Array.isArray(parsed)) return []
    const history = parsed.filter(isStoredConversionRecord).map(normalizeRecord).slice(0, HISTORY_LIMIT)
    for (const record of history) {
      if (record.status === "success") {
        approvePath(record.outputPath)
      }
    }
    return history
  } catch {
    return []
  }
}

function writeHistory(history: ConversionRecord[]): void {
  const historyPath = getHistoryPath()
  const tmpPath = `${historyPath}.tmp`
  mkdirSync(dirname(historyPath), { recursive: true })
  try {
    writeFileSync(tmpPath, JSON.stringify(history.slice(0, HISTORY_LIMIT), null, 2))
    renameSync(tmpPath, historyPath)
  } catch (error) {
    if (existsSync(tmpPath)) {
      unlinkSync(tmpPath)
    }
    throw error
  }
}

app.whenReady().then(() => {
  createWindow()

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on("before-quit", () => {
  stopActiveJob()
})

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit()
})

ipcMain.handle("select-source", async () => {
  const isMac = process.platform === "darwin"

  // Logic projects are macOS bundles, so the unified picker must permit both
  // files and directories there. Other platforms only need file-based sessions.
  const properties: Array<"openFile" | "openDirectory"> = isMac
    ? ["openFile", "openDirectory"]
    : ["openFile"]

  const result = await dialog.showOpenDialog({
    properties,
    title: "Select a session",
    filters: [
      {
        name: "Logic, Ableton, or Pro Tools Session",
        extensions: ["logicx", "als", "ptx", "pts"],
      },
    ],
  })

  if (result.canceled || result.filePaths.length === 0) return null
  const selected = result.filePaths[0]
  if (!SUPPORTED_SOURCE_EXTENSIONS.has(extname(selected).toLowerCase())) return null
  return selected
})

ipcMain.handle("select-output-dir", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory", "createDirectory"],
    title: "Select output directory",
  })
  if (result.canceled) return null
  return result.filePaths[0]
})

ipcMain.handle("start-conversion", async (
  event,
  direction: ConversionDirection,
  sourcePath: string,
  outputDir: string,
  tempo?: number,
) => {
  if (!isConversionDirection(direction)) throw new Error("Unsupported conversion mode")
  startJob("conversion", event, direction, sourcePath, outputDir, false, normalizeTempo(tempo))
})

ipcMain.handle("start-preview", async (
  event,
  direction: ConversionDirection,
  sourcePath: string,
  tempo?: number,
) => {
  if (!isConversionDirection(direction)) throw new Error("Unsupported conversion mode")
  startJob("preview", event, direction, sourcePath, previewOutputDir(direction), true, normalizeTempo(tempo))
})

ipcMain.handle("cancel-active-job", async () => {
  stopActiveJob()
})

ipcMain.handle("open-file", async (_, filePath: string) => {
  return shell.openPath(assertApprovedPath(filePath))
})

ipcMain.handle("show-in-folder", async (_, filePath: string) => {
  shell.showItemInFolder(assertApprovedPath(filePath))
})

ipcMain.handle("get-history", async () => {
  return readHistory()
})

ipcMain.handle("add-history", async (_, record: unknown) => {
  if (!isConversionRecord(record)) {
    throw new Error("Invalid conversion history record")
  }
  if (record.status === "success") {
    approvePath(record.outputPath)
  }
  const history = [record, ...readHistory()].slice(0, HISTORY_LIMIT)
  writeHistory(history)
  return history
})

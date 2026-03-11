import type { ChildProcess } from "node:child_process"
import type { IpcMainInvokeEvent } from "electron"
import { app, BrowserWindow, dialog, ipcMain, shell } from "electron"
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs"
import { dirname, extname, join, normalize } from "node:path"
import type { ProgressEvent } from "./converter"
import { runConversion } from "./converter"

interface ConversionStats {
  tracks: number
  clips?: number
  audioFiles: number
}

interface ConversionRecord {
  id: string
  projectName: string
  inputPath: string
  outputPath: string
  date: string
  status: "success" | "failed"
  report: string
  stats?: ConversionStats
}

const HISTORY_LIMIT = 100
const ALLOWED_OPEN_EXTENSIONS = new Set([".als", ".txt"])

let mainWindow: BrowserWindow | null = null
let activeJob: { kind: "conversion" | "preview"; child: ChildProcess } | null = null
const approvedPaths = new Set<string>()

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 700,
    minWidth: 800,
    minHeight: 500,
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

function previewOutputDir(): string {
  const outputDir = join(app.getPath("temp"), "logic2ableton-preview")
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
  logicxPath: string,
  outputDir: string,
  reportOnly = false,
): void {
  if (activeJob) {
    throw new Error(`${activeJob.kind === "preview" ? "Preview" : "Conversion"} already in progress`)
  }

  const progressChannel = kind === "preview" ? "preview-progress" : "conversion-progress"
  const errorChannel = kind === "preview" ? "preview-error" : "conversion-error"
  const exitChannel = kind === "preview" ? "preview-exit" : "conversion-exit"

  const child = runConversion(
    normalizePathInput(logicxPath),
    normalizePathInput(outputDir),
    (progress: ProgressEvent) => {
      approvePath(progress.als_path)
      approvePath(progress.report_path)
      event.sender.send(progressChannel, progress)
    },
    (error) => event.sender.send(errorChannel, error),
    (code) => {
      clearActiveJob(child)
      event.sender.send(exitChannel, code)
    },
    reportOnly,
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
}

function isConversionRecord(value: unknown): value is ConversionRecord {
  if (!value || typeof value !== "object") return false
  const record = value as Partial<ConversionRecord>
  return typeof record.id === "string"
    && typeof record.projectName === "string"
    && typeof record.inputPath === "string"
    && typeof record.outputPath === "string"
    && typeof record.date === "string"
    && (record.status === "success" || record.status === "failed")
    && typeof record.report === "string"
    && (record.stats === undefined || isConversionStats(record.stats))
}

function readHistory(): ConversionRecord[] {
  const historyPath = getHistoryPath()
  try {
    const parsed = JSON.parse(readFileSync(historyPath, "utf-8"))
    if (!Array.isArray(parsed)) return []
    const history = parsed.filter(isConversionRecord).slice(0, HISTORY_LIMIT)
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

ipcMain.handle("select-logicx", async () => {
  const isMac = process.platform === "darwin"
  const result = await dialog.showOpenDialog({
    properties: isMac ? ["openFile"] : ["openDirectory"],
    title: "Select a Logic Pro project",
    ...(isMac && {
      filters: [{ name: "Logic Pro Project", extensions: ["logicx"] }],
    }),
  })
  if (result.canceled || result.filePaths.length === 0) return null
  const selected = result.filePaths[0]
  if (!selected.endsWith(".logicx")) return null
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

ipcMain.handle("start-conversion", async (event, logicxPath: string, outputDir: string) => {
  startJob("conversion", event, logicxPath, outputDir)
})

ipcMain.handle("start-preview", async (event, logicxPath: string) => {
  startJob("preview", event, logicxPath, previewOutputDir(), true)
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

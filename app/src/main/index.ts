import { app, BrowserWindow, dialog, ipcMain, shell } from "electron"
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs"
import { dirname, join, normalize } from "node:path"
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

let mainWindow: BrowserWindow | null = null

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
    return parsed.filter(isConversionRecord).slice(0, HISTORY_LIMIT)
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
  runConversion(
    normalizePathInput(logicxPath),
    normalizePathInput(outputDir),
    (progress) => event.sender.send("conversion-progress", progress),
    (error) => event.sender.send("conversion-error", error),
    (code) => event.sender.send("conversion-exit", code),
  )
})

ipcMain.handle("start-preview", async (event, logicxPath: string) => {
  runConversion(
    normalizePathInput(logicxPath),
    previewOutputDir(),
    (progress) => event.sender.send("preview-progress", progress),
    (error) => event.sender.send("preview-error", error),
    (code) => event.sender.send("preview-exit", code),
    true,
  )
})

ipcMain.handle("open-file", async (_, filePath: string) => {
  return shell.openPath(normalizePathInput(filePath))
})

ipcMain.handle("show-in-folder", async (_, filePath: string) => {
  shell.showItemInFolder(normalizePathInput(filePath))
})

ipcMain.handle("get-history", async () => {
  return readHistory()
})

ipcMain.handle("add-history", async (_, record: unknown) => {
  if (!isConversionRecord(record)) {
    throw new Error("Invalid conversion history record")
  }
  const history = [record, ...readHistory()].slice(0, HISTORY_LIMIT)
  writeHistory(history)
  return history
})

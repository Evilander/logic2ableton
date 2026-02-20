import { app, BrowserWindow, ipcMain, dialog, shell } from "electron"
import { join } from "path"
import { runConversion } from "./converter"

let mainWindow: BrowserWindow | null = null

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 700,
    minWidth: 800,
    minHeight: 500,
    backgroundColor: "#1A1820",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      sandbox: false,
    },
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(join(__dirname, "../renderer/index.html"))
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

// IPC Handlers

ipcMain.handle("select-logicx", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
    title: "Select a Logic Pro project",
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
    logicxPath,
    outputDir,
    (progress) => event.sender.send("conversion-progress", progress),
    (error) => event.sender.send("conversion-error", error),
    (code) => event.sender.send("conversion-exit", code),
  )
})

ipcMain.handle("start-preview", async (event, logicxPath: string) => {
  runConversion(
    logicxPath,
    ".",
    (progress) => event.sender.send("preview-progress", progress),
    (error) => event.sender.send("preview-error", error),
    (code) => event.sender.send("preview-exit", code),
    true, // reportOnly
  )
})

ipcMain.handle("open-file", async (_, filePath: string) => {
  await shell.openPath(filePath)
})

ipcMain.handle("show-in-folder", async (_, filePath: string) => {
  shell.showItemInFolder(filePath)
})

ipcMain.handle("get-history", async () => {
  const fs = await import("fs")
  const historyPath = join(app.getPath("userData"), "conversion-history.json")
  try {
    return JSON.parse(fs.readFileSync(historyPath, "utf-8"))
  } catch {
    return []
  }
})

ipcMain.handle("add-history", async (_, record: unknown) => {
  const fs = await import("fs")
  const historyPath = join(app.getPath("userData"), "conversion-history.json")
  let history: unknown[] = []
  try {
    history = JSON.parse(fs.readFileSync(historyPath, "utf-8"))
  } catch {
    // first entry
  }
  history.unshift(record)
  if (history.length > 100) history = history.slice(0, 100)
  fs.writeFileSync(historyPath, JSON.stringify(history, null, 2))
})

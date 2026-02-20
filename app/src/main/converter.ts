import { spawn, ChildProcess } from "child_process"
import { join, resolve } from "path"
import { app } from "electron"

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

function getConverterCommand(): { cmd: string; baseArgs: string[] } {
  if (app.isPackaged) {
    const ext = process.platform === "win32" ? ".exe" : ""
    return {
      cmd: join(process.resourcesPath, `logic2ableton${ext}`),
      baseArgs: [],
    }
  }
  // Dev mode: use system Python against the source
  return {
    cmd: "python",
    baseArgs: ["-m", "logic2ableton.cli"],
  }
}

export function runConversion(
  logicxPath: string,
  outputDir: string,
  onProgress: (event: ProgressEvent) => void,
  onError: (error: string) => void,
  onExit: (code: number) => void,
  reportOnly = false,
): ChildProcess {
  const { cmd, baseArgs } = getConverterCommand()
  const args = [
    ...baseArgs,
    logicxPath,
    "--output", outputDir,
    "--json-progress",
  ]
  if (reportOnly) {
    args.push("--report-only", "--no-copy")
  }

  const cwd = app.isPackaged ? undefined : resolve(__dirname, "../../..")

  const child = spawn(cmd, args, { cwd })

  let buffer = ""
  child.stdout?.on("data", (data: Buffer) => {
    buffer += data.toString()
    const lines = buffer.split("\n")
    buffer = lines.pop() || ""
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        onProgress(JSON.parse(line))
      } catch {
        // non-JSON line, skip
      }
    }
  })

  child.stderr?.on("data", (data: Buffer) => {
    onError(data.toString())
  })

  child.on("close", (code) => {
    onExit(code ?? 1)
  })

  return child
}

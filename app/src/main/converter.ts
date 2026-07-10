import { spawn, ChildProcess } from "node:child_process"
import { existsSync } from "node:fs"
import { join, resolve } from "node:path"
import { app } from "electron"

export const CONVERSION_DIRECTIONS = [
  "logic2ableton",
  "ableton2logic",
  "protools2ableton",
  "protools2logic",
  "ableton2protools",
  "logic2protools",
] as const

export type ConversionDirection = (typeof CONVERSION_DIRECTIONS)[number]

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

function getConverterCommand(): { cmd: string; baseArgs: string[] } {
  if (app.isPackaged) {
    const ext = process.platform === "win32" ? ".exe" : ""
    const packagedBinary = join(process.resourcesPath, `logic2ableton${ext}`)
    if (!existsSync(packagedBinary)) {
      throw new Error(`Bundled converter not found at ${packagedBinary}`)
    }
    return {
      cmd: packagedBinary,
      baseArgs: [],
    }
  }

  // Dev mode runs the source package directly. Modern macOS/Linux ship
  // `python3`, not `python`.
  return {
    cmd: process.platform === "win32" ? "python" : "python3",
    baseArgs: ["-m", "logic2ableton.cli"],
  }
}

export function runConversion(
  direction: ConversionDirection,
  sourcePath: string,
  outputDir: string,
  onProgress: (event: ProgressEvent) => void,
  onError: (error: string) => void,
  onExit: (code: number) => void,
  reportOnly = false,
  tempo?: number,
): ChildProcess | null {
  let cmd: string
  let baseArgs: string[]

  try {
    ({ cmd, baseArgs } = getConverterCommand())
  } catch (error) {
    onError(error instanceof Error ? error.message : String(error))
    onExit(1)
    return null
  }

  const args = [
    ...baseArgs,
    "--mode",
    direction,
    sourcePath,
    "--output",
    outputDir,
    "--json-progress",
  ]
  if (reportOnly) {
    args.push("--report-only")
  }
  if (direction.startsWith("protools2") && tempo !== undefined) {
    args.push("--tempo", String(tempo))
  }

  const cwd = app.isPackaged ? undefined : resolve(__dirname, "../../..")
  const child = spawn(cmd, args, { cwd })
  let finished = false

  const finish = (code: number) => {
    if (finished) return
    finished = true
    onExit(code)
  }

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
        // Ignore non-JSON output from the converter.
      }
    }
  })

  child.stderr?.on("data", (data: Buffer) => {
    onError(data.toString())
  })

  child.on("error", (error) => {
    onError(`Failed to start converter: ${error.message}`)
    finish(1)
  })

  child.on("close", (code) => {
    finish(code ?? 1)
  })

  return child
}

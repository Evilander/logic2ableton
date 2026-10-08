import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const { version } = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"))
const binary = process.argv[2] || join(appRoot, "resources", process.platform === "win32" ? "logic2ableton.exe" : "logic2ableton")
const actual = execFileSync(binary, ["--version"], { encoding: "utf8", timeout: 15000, windowsHide: true }).trim()
if (actual !== `logic2ableton ${version}`) {
  throw new Error(`Converter version mismatch: expected ${version}, received ${actual}`)
}
console.log(`Converter version verified: ${version}`)

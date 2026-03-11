import { execFileSync } from "node:child_process"
import { mkdtempSync, mkdirSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"

const sourceIcon = join(process.cwd(), "resources", "icon.png")
const outputIcon = join(process.cwd(), "resources", "icon.icns")

if (process.platform !== "darwin") {
  console.log("Skipping macOS icon generation on non-macOS host.")
  process.exit(0)
}

const scratchDir = mkdtempSync(join(tmpdir(), "logic2ableton-iconset-"))
const iconsetDir = join(scratchDir, "icon.iconset")
const iconSizes = [16, 32, 128, 256, 512]

mkdirSync(iconsetDir, { recursive: true })

try {
  for (const size of iconSizes) {
    const icon1x = join(iconsetDir, `icon_${size}x${size}.png`)
    const icon2x = join(iconsetDir, `icon_${size}x${size}@2x.png`)
    execFileSync("sips", ["-z", String(size), String(size), sourceIcon, "--out", icon1x], { stdio: "inherit" })
    execFileSync("sips", ["-z", String(size * 2), String(size * 2), sourceIcon, "--out", icon2x], { stdio: "inherit" })
  }

  mkdirSync(dirname(outputIcon), { recursive: true })
  execFileSync("iconutil", ["-c", "icns", iconsetDir, "-o", outputIcon], { stdio: "inherit" })
  console.log(`Generated ${outputIcon}`)
} finally {
  rmSync(scratchDir, { recursive: true, force: true })
}

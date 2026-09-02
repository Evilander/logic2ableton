export const SOURCE_FORMATS = ["logic", "ableton", "protools"] as const

export type SourceFormat = (typeof SOURCE_FORMATS)[number]

interface FormatMeta {
  name: string
  shortName: string
  extension: string
}

interface ModeMeta {
  source: SourceFormat
  destination: SourceFormat
}

export const FORMAT_META: Record<SourceFormat, FormatMeta> = {
  logic: { name: "Logic Pro", shortName: "Logic", extension: ".logicx" },
  ableton: { name: "Ableton Live", shortName: "Ableton", extension: ".als" },
  protools: { name: "Pro Tools", shortName: "Pro Tools", extension: ".ptx" },
}

export const MODE_META = {
  logic2ableton: { source: "logic", destination: "ableton" },
  ableton2logic: { source: "ableton", destination: "logic" },
  protools2ableton: { source: "protools", destination: "ableton" },
  protools2logic: { source: "protools", destination: "logic" },
  ableton2protools: { source: "ableton", destination: "protools" },
  logic2protools: { source: "logic", destination: "protools" },
} as const satisfies Record<string, ModeMeta>

export type ConversionDirection = keyof typeof MODE_META

const DEFAULT_DIRECTION: Record<SourceFormat, ConversionDirection> = {
  logic: "logic2ableton",
  ableton: "ableton2logic",
  protools: "protools2ableton",
}

const DESTINATIONS: Record<SourceFormat, SourceFormat[]> = {
  logic: ["ableton", "protools"],
  ableton: ["logic", "protools"],
  protools: ["ableton", "logic"],
}

const MODE_BY_ROUTE: Record<SourceFormat, Partial<Record<SourceFormat, ConversionDirection>>> = {
  logic: { ableton: "logic2ableton", protools: "logic2protools" },
  ableton: { logic: "ableton2logic", protools: "ableton2protools" },
  protools: { ableton: "protools2ableton", logic: "protools2logic" },
}

export function detectSourceFormat(path: string): SourceFormat | null {
  const normalized = path.trim().toLowerCase()
  if (normalized.endsWith(".logicx")) return "logic"
  if (normalized.endsWith(".als")) return "ableton"
  if (normalized.endsWith(".ptx") || normalized.endsWith(".pts") || normalized.endsWith(".ptf")) return "protools"
  return null
}

function basenameOf(path: string): string {
  const trimmed = path.trim()
  return trimmed.split(/[/\\]/).pop() || trimmed
}

export function describeUnsupportedSource(path: string): string {
  const trimmed = path.trim()
  const normalized = trimmed.toLowerCase()
  const name = basenameOf(trimmed)

  const insideLogicxPackage = normalized
    .split(/[/\\]/)
    .some((segment) => segment.endsWith(".logicx"))
  if (insideLogicxPackage && !normalized.endsWith(".logicx")) {
    return "Choose the .logicx package itself, not a file inside it."
  }
  if (normalized.endsWith(".logic")) {
    return `“${name}” is a Logic Pro 8 or 9 project. This app reads .logicx packages, which Logic Pro X (10.0) and later save. Open the project in a current Logic Pro, choose File › Save As, and use the .logicx it writes.`
  }
  if (normalized.endsWith(".lso")) {
    return `“${name}” is a Logic 7 song file. Open it in Logic Pro and save it as a .logicx package first.`
  }
  if (normalized.endsWith(".alp")) {
    return `“${name}” is an Ableton Live Pack. Install it in Live first, then choose the .als set it contains.`
  }
  return `“${name}” isn’t a supported session. Choose a .logicx, .als, .ptx, .pts, or .ptf session.`
}

export function defaultDirectionForSource(source: SourceFormat): ConversionDirection {
  return DEFAULT_DIRECTION[source]
}

export function destinationsForSource(source: SourceFormat): SourceFormat[] {
  return DESTINATIONS[source]
}

export function directionForRoute(source: SourceFormat, destination: SourceFormat): ConversionDirection {
  const direction = MODE_BY_ROUTE[source][destination]
  if (!direction) throw new Error(`Unsupported conversion route: ${source} to ${destination}`)
  return direction
}

export function sourceForDirection(direction: ConversionDirection): SourceFormat {
  return MODE_META[direction].source
}

export function destinationForDirection(direction: ConversionDirection): SourceFormat {
  return MODE_META[direction].destination
}

export function routeLabel(direction: ConversionDirection): string {
  const { source, destination } = MODE_META[direction]
  return `${FORMAT_META[source].shortName} to ${FORMAT_META[destination].shortName}`
}

export function artifactLabel(direction: ConversionDirection): string {
  const destination = destinationForDirection(direction)
  if (destination === "ableton") return "Live Set"
  if (destination === "logic") return "Logic transfer package"
  return "Pro Tools transfer package"
}

export function isProToolsSource(direction: ConversionDirection): boolean {
  return sourceForDirection(direction) === "protools"
}

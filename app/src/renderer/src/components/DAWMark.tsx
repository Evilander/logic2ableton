import type { SourceFormat } from "../conversion"
import { FORMAT_META } from "../conversion"

interface DAWMarkProps {
  format: SourceFormat
  size?: number
  className?: string
  labelled?: boolean
}

export default function DAWMark({
  format,
  size = 24,
  className = "",
  labelled = false,
}: DAWMarkProps) {
  const accessibility = labelled
    ? { role: "img", "aria-label": FORMAT_META[format].name }
    : { "aria-hidden": true }

  if (format === "logic") {
    return (
      <svg
        {...accessibility}
        className={className}
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
      >
        <path
          d="M7.4 3.75h9.2a2 2 0 0 1 1.73 1l4.6 7.25-4.6 7.25a2 2 0 0 1-1.73 1H7.4a2 2 0 0 1-1.73-1L1.07 12l4.6-7.25a2 2 0 0 1 1.73-1Z"
          stroke="currentColor"
          strokeWidth="1.45"
        />
        <path
          d="M5.8 12c1.2-3.2 2.4-3.2 3.6 0s2.4 3.2 3.6 0 2.4-3.2 3.6 0 1.2 3.2 1.6 0"
          stroke="currentColor"
          strokeWidth="1.55"
          strokeLinecap="round"
        />
      </svg>
    )
  }

  if (format === "ableton") {
    return (
      <svg
        {...accessibility}
        className={className}
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
      >
        <path d="M5.5 5.5v13M10 7.5v9" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <path d="M14.5 9.25h4M13 14.75h6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <path d="M3 20.25h18" stroke="currentColor" strokeWidth="1" strokeLinecap="round" opacity=".45" />
      </svg>
    )
  }

  return (
    <svg
      {...accessibility}
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="8.25" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2.4 1.8" />
      <path
        d="M4.8 12h2.1l1.25-3.25 2.25 6.5 2.2-7.8 2.15 8.1L16.3 12h2.9"
        stroke="currentColor"
        strokeWidth="1.55"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

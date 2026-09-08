export function formatDuration(totalSeconds: number | null): string {
  if (totalSeconds === null) return '—'
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const parts: string[] = []
  if (hours > 0) parts.push(`${hours}h`)
  if (minutes > 0 || hours > 0) parts.push(`${minutes}m`)
  return parts.length > 0 ? parts.join(' ') : '0m'
}

const TIMESTAMP_OFFSET_PATTERN = /(Z|[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/

export function timestampOffset(timestamp: string): string {
  const match = timestamp.match(TIMESTAMP_OFFSET_PATTERN)
  return match?.[1] === 'Z' ? '+00:00' : match?.[1] ?? ''
}

export function compactTimestampOffset(timestamp: string): string {
  return timestampOffset(timestamp).slice(0, 6)
}

export function formatEventTime(timestamp: string, showOffset = false): string {
  const offset = compactTimestampOffset(timestamp)
  return `${timestamp.slice(11, 16)}${showOffset && offset ? ` ${offset}` : ''}`
}

export function formatEventDateTime(timestamp: string, showOffset = false): string {
  return `${timestamp.slice(0, 10)} ${formatEventTime(timestamp, showOffset)}`
}

export function formatSessionRange(
  entryTimestamp: string | null,
  exitTimestamp: string | null,
): string {
  if (!entryTimestamp && !exitTimestamp) return '—'
  const showOffsets = Boolean(
    entryTimestamp
    && exitTimestamp
    && timestampOffset(entryTimestamp) !== timestampOffset(exitTimestamp),
  )
  const entry = entryTimestamp ? formatEventTime(entryTimestamp, showOffsets) : '—'
  let exit = exitTimestamp ? formatEventTime(exitTimestamp, showOffsets) : '—'
  if (
    entryTimestamp
    && exitTimestamp
    && entryTimestamp.slice(0, 10) !== exitTimestamp.slice(0, 10)
  ) {
    exit = `${exitTimestamp.slice(8, 10)}.${exitTimestamp.slice(5, 7)} ${exit}`
  }
  return `${entry} → ${exit}`
}

export type WorkItemPresentationStatus =
  | 'valid'
  | 'missing_exit'
  | 'duplicate_entry'
  | 'orphan_exit'
  | 'unusually_long_session'
  | 'ambiguous_timestamp'

export function dayOverviewPresentation(status: WorkItemPresentationStatus): {
  showRange: boolean
  showDuration: boolean
  showWarning: boolean
} {
  const hasCompleteInterval = status === 'valid' || status === 'unusually_long_session'
  return {
    showRange: hasCompleteInterval,
    showDuration: hasCompleteInterval,
    showWarning: status !== 'valid',
  }
}

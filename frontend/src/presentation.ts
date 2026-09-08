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

export function formatEventTime(timestamp: string): string {
  const offsetMatch = timestamp.match(/(Z|[+-]\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?$/)
  const offset = offsetMatch?.[1] === 'Z' ? 'UTC' : `UTC${offsetMatch?.[1] ?? ''}`
  return `${timestamp.slice(11, 16)} ${offset}`
}

export function formatEventDateTime(timestamp: string): string {
  return `${timestamp.slice(0, 10)} ${formatEventTime(timestamp)}`
}

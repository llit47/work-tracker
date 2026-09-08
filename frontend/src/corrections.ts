export type ManualEventType = 'entry' | 'exit'

const LOCAL_DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/
const EXPLICIT_OFFSET_PATTERN = /(Z|[+-]\d{2}:\d{2})$/
const OFFSET_SAMPLE_RANGE_HOURS = 48

export function extractOffsetFromIso(timestamp: string): string | null {
  const match = timestamp.match(EXPLICIT_OFFSET_PATTERN)
  if (!match) return null
  return match[1] === 'Z' ? '+00:00' : match[1]
}

export function getPossibleOffsetsForLocalDateTime(value: string): string[] {
  const parts = parseLocalDateTime(value)
  if (!parts) return []

  const wallClockMilliseconds = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  )
  const candidateOffsets = new Set<number>()
  for (let hour = -OFFSET_SAMPLE_RANGE_HOURS; hour <= OFFSET_SAMPLE_RANGE_HOURS; hour += 1) {
    candidateOffsets.add(new Date(wallClockMilliseconds + hour * 60 * 60 * 1000).getTimezoneOffset())
  }

  return [...candidateOffsets]
    .filter((offsetMinutes) => {
      const candidate = new Date(wallClockMilliseconds + offsetMinutes * 60 * 1000)
      return candidate.getTimezoneOffset() === offsetMinutes
        && candidate.getFullYear() === parts.year
        && candidate.getMonth() === parts.month - 1
        && candidate.getDate() === parts.day
        && candidate.getHours() === parts.hour
        && candidate.getMinutes() === parts.minute
        && candidate.getSeconds() === parts.second
    })
    // UTC = local wall-clock + Date#getTimezoneOffset; ascending offsets are occurrence order.
    .sort((left, right) => left - right)
    .map(formatOffset)
}

export function localDateTimeToOffsetIso(value: string, preferredOffset?: string): string | null {
  const possibleOffsets = getPossibleOffsetsForLocalDateTime(value)
  const offset = preferredOffset
    ? possibleOffsets.find((candidate) => candidate === preferredOffset)
    : possibleOffsets.length === 1 ? possibleOffsets[0] : undefined
  return offset ? formatLocalDateTimeWithExplicitOffset(value, offset) : null
}

export function resolveCorrectionTimestamp(
  value: string,
  existingTimestamp?: string,
  initialLocalValue?: string,
  selectedOffset?: string | null,
): string | null {
  if (existingTimestamp && initialLocalValue === value) {
    const existingOffset = extractOffsetFromIso(existingTimestamp)
    if (!selectedOffset || selectedOffset === existingOffset) return existingTimestamp
  }
  return localDateTimeToOffsetIso(value, selectedOffset ?? undefined)
}

export function formatLocalDateTimeWithOffset(value: string, offsetMinutes: number): string | null {
  const parts = parseLocalDateTime(value)
  if (!parts || !Number.isInteger(offsetMinutes)) return null
  return formatLocalDateTimeWithExplicitOffset(value, formatOffset(offsetMinutes))
}

function formatLocalDateTimeWithExplicitOffset(value: string, offset: string): string | null {
  const parts = parseLocalDateTime(value)
  if (!parts) return null
  const seconds = parts.second.toString().padStart(2, '0')
  return `${value.slice(0, 16)}:${seconds}${offset}`
}

function formatOffset(offsetMinutes: number): string {
  const sign = offsetMinutes <= 0 ? '+' : '-'
  const absoluteOffset = Math.abs(offsetMinutes)
  const offsetHours = Math.floor(absoluteOffset / 60).toString().padStart(2, '0')
  const offsetRemainder = (absoluteOffset % 60).toString().padStart(2, '0')
  return `${sign}${offsetHours}:${offsetRemainder}`
}

export function timestampToLocalInput(timestamp: string): string {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? '' : formatDateForInput(date)
}

function parseLocalDateTime(value: string) {
  const match = value.match(LOCAL_DATE_TIME_PATTERN)
  if (!match) return null
  const [year, month, day, hour, minute, second] = match.slice(1).map((part) => Number(part ?? 0))
  if (
    year < 2000
    || year > 2100
    || month < 1
    || month > 12
    || day < 1
    || day > 31
    || hour > 23
    || minute > 59
    || second > 59
  ) {
    return null
  }
  const calendarDate = new Date(Date.UTC(year, month - 1, day, hour, minute, second))
  if (
    calendarDate.getUTCFullYear() !== year
    || calendarDate.getUTCMonth() !== month - 1
    || calendarDate.getUTCDate() !== day
    || calendarDate.getUTCHours() !== hour
    || calendarDate.getUTCMinutes() !== minute
    || calendarDate.getUTCSeconds() !== second
  ) {
    return null
  }
  return { year, month, day, hour, minute, second }
}

function formatDateForInput(date: Date): string {
  const year = date.getFullYear().toString().padStart(4, '0')
  const month = (date.getMonth() + 1).toString().padStart(2, '0')
  const day = date.getDate().toString().padStart(2, '0')
  const hour = date.getHours().toString().padStart(2, '0')
  const minute = date.getMinutes().toString().padStart(2, '0')
  return `${year}-${month}-${day}T${hour}:${minute}`
}

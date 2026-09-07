export type ManualEventType = 'entry' | 'exit'

const LOCAL_DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

export function localDateTimeToOffsetIso(value: string): string | null {
  const parts = parseLocalDateTime(value)
  if (!parts) return null

  const localDate = new Date(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  )
  if (
    localDate.getFullYear() !== parts.year
    || localDate.getMonth() !== parts.month - 1
    || localDate.getDate() !== parts.day
    || localDate.getHours() !== parts.hour
    || localDate.getMinutes() !== parts.minute
    || localDate.getSeconds() !== parts.second
  ) {
    return null
  }

  return formatLocalDateTimeWithOffset(value, localDate.getTimezoneOffset())
}

export function formatLocalDateTimeWithOffset(value: string, offsetMinutes: number): string | null {
  const parts = parseLocalDateTime(value)
  if (!parts || !Number.isInteger(offsetMinutes)) return null
  const sign = offsetMinutes <= 0 ? '+' : '-'
  const absoluteOffset = Math.abs(offsetMinutes)
  const offsetHours = Math.floor(absoluteOffset / 60).toString().padStart(2, '0')
  const offsetRemainder = (absoluteOffset % 60).toString().padStart(2, '0')
  const seconds = parts.second.toString().padStart(2, '0')
  return `${value.slice(0, 16)}:${seconds}${sign}${offsetHours}:${offsetRemainder}`
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
  const second = date.getSeconds().toString().padStart(2, '0')
  return `${year}-${month}-${day}T${hour}:${minute}:${second}`
}

export const MIN_YEAR = 2000
export const MAX_YEAR = 2100

export type MonthSelection = {
  year: number
  month: number
}

export type MonthSelectionFromUrl = {
  selection: MonthSelection
  shouldNormalize: boolean
}

export function currentMonth(date = new Date()): MonthSelection {
  return { year: date.getFullYear(), month: date.getMonth() + 1 }
}

export function currentLocalDate(date = new Date()): string {
  const year = date.getFullYear().toString().padStart(4, '0')
  const month = (date.getMonth() + 1).toString().padStart(2, '0')
  const day = date.getDate().toString().padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function millisecondsUntilNextLocalDay(now = new Date()): number {
  const nextDay = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  return Math.max(0, nextDay.getTime() - now.getTime())
}

export function isValidMonth(selection: MonthSelection): boolean {
  return (
    Number.isInteger(selection.year)
    && selection.year >= MIN_YEAR
    && selection.year <= MAX_YEAR
    && Number.isInteger(selection.month)
    && selection.month >= 1
    && selection.month <= 12
  )
}

export function readMonthFromSearch(search: string, fallback: MonthSelection): MonthSelectionFromUrl {
  const params = new URLSearchParams(search)
  const yearValue = params.get('year')
  const monthValue = params.get('month')

  if (yearValue === null && monthValue === null) {
    return { selection: fallback, shouldNormalize: false }
  }

  const hasValidShape = /^\d{4}$/.test(yearValue ?? '') && /^\d{1,2}$/.test(monthValue ?? '')
  const selection = {
    year: Number(yearValue),
    month: Number(monthValue),
  }

  if (!hasValidShape || !isValidMonth(selection)) {
    return { selection: fallback, shouldNormalize: true }
  }

  return { selection, shouldNormalize: false }
}

export function shiftMonth(selection: MonthSelection, change: -1 | 1): MonthSelection {
  const zeroBasedMonth = selection.year * 12 + selection.month - 1 + change
  const shifted = {
    year: Math.floor(zeroBasedMonth / 12),
    month: (zeroBasedMonth % 12) + 1,
  }
  return isValidMonth(shifted) ? shifted : selection
}

export function monthInputValue(selection: MonthSelection): string {
  return `${selection.year.toString().padStart(4, '0')}-${selection.month.toString().padStart(2, '0')}`
}

export function parseMonthInput(value: string): MonthSelection | null {
  if (!/^\d{4}-\d{2}$/.test(value)) return null
  const [year, month] = value.split('-').map(Number)
  const selection = { year, month }
  return isValidMonth(selection) ? selection : null
}

export function monthSearch(selection: MonthSelection): string {
  return `?year=${selection.year}&month=${selection.month}`
}

export function isSameMonth(left: MonthSelection, right: MonthSelection): boolean {
  return left.year === right.year && left.month === right.month
}

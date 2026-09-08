import type { FetchLike } from './pay.js'

export type DashboardStatus = 'working' | 'outside' | 'ambiguous'

export type CurrentSession = {
  entry_timestamp: string
  entry_timestamp_utc: string
  elapsed_seconds: number
}

export type TodayDashboard = {
  date: string
  completed_duration_seconds: number
  running_duration_seconds: number | null
  effective_duration_seconds: number
}

export type MonthDashboard = {
  year: number
  month: number
  completed_duration_seconds: number
  work_days: number
  pay: string
  currency: string
}

export type DashboardSummary = {
  status: DashboardStatus
  generated_at: string
  current_session: CurrentSession | null
  today: TodayDashboard
  month: MonthDashboard
}

export const DASHBOARD_POLL_INTERVAL_MS = 30_000
export const LIVE_TIMER_INTERVAL_MS = 1_000

function timestampFingerprint(timestamp: string | null): string | number | null {
  if (timestamp === null) return null
  const milliseconds = Date.parse(timestamp)
  return Number.isFinite(milliseconds) ? milliseconds : timestamp
}

export function dashboardDataFingerprint(dashboard: DashboardSummary): string {
  return JSON.stringify({
    status: dashboard.status,
    currentEntryInstant: timestampFingerprint(
      dashboard.current_session?.entry_timestamp_utc ?? null,
    ),
    runningToday: dashboard.today.running_duration_seconds !== null,
    year: dashboard.month.year,
    month: dashboard.month.month,
    completedDuration: dashboard.month.completed_duration_seconds,
    workDays: dashboard.month.work_days,
    pay: dashboard.month.pay,
    currency: dashboard.month.currency,
  })
}

export function shouldRefreshMonthlyData(
  previousDashboard: DashboardSummary | null,
  dashboard: DashboardSummary,
  selectedMonth: { year: number; month: number },
): boolean {
  const previousFingerprint = previousDashboard
    ? dashboardDataFingerprint(previousDashboard)
    : null
  if (previousFingerprint === dashboardDataFingerprint(dashboard)) return false

  const entryMonths = [previousDashboard, dashboard]
    .map((snapshot) => dashboardEntryMonth(snapshot))
    .filter((month): month is { year: number; month: number } => month !== null)
  return (
    isSameCalendarMonth(selectedMonth, dashboard.month)
    || entryMonths.some((month) => isSameCalendarMonth(selectedMonth, month))
  )
}

function dashboardEntryMonth(
  dashboard: DashboardSummary | null,
): { year: number; month: number } | null {
  const timestamp = dashboard?.current_session?.entry_timestamp
  const match = timestamp?.match(/^(\d{4})-(\d{2})-/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  return month >= 1 && month <= 12 ? { year, month } : null
}

function isSameCalendarMonth(
  left: { year: number; month: number },
  right: { year: number; month: number },
): boolean {
  return left.year === right.year && left.month === right.month
}

export const dashboardStatusPresentation: Record<DashboardStatus, {
  label: string
  description: string
}> = {
  working: {
    label: 'W PRACY',
    description: 'Trwa jednoznacznie rozpoczęta zmiana.',
  },
  outside: {
    label: 'POZA PRACĄ',
    description: 'Brak otwartej zmiany.',
  },
  ambiguous: {
    label: 'STATUS NIEJEDNOZNACZNY',
    description: 'Zdarzenia wymagają sprawdzenia. Czas bieżącej zmiany nie jest zgadywany.',
  },
}

export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function formatLiveTimer(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  return [hours, minutes].map((part) => String(part).padStart(2, '0')).join(':')
}

export function formatDashboardDuration(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const parts: string[] = []
  if (hours > 0) parts.push(`${hours} godz.`)
  if (minutes > 0 || hours > 0) parts.push(`${minutes} min`)
  return parts.length > 0 ? parts.join(' ') : '0 min'
}

export function advanceElapsedSeconds(
  baselineSeconds: number,
  receivedAtMilliseconds: number,
  nowMilliseconds: number,
): number {
  const locallyElapsed = Math.max(0, nowMilliseconds - receivedAtMilliseconds)
  return baselineSeconds + Math.floor(locallyElapsed / 1000)
}

export function advanceTodayEffectiveDuration(
  today: TodayDashboard,
  baselineLiveSeconds: number,
  currentLiveSeconds: number,
): number {
  if (today.running_duration_seconds === null) return today.effective_duration_seconds
  return today.effective_duration_seconds + Math.max(0, currentLiveSeconds - baselineLiveSeconds)
}

export async function loadDashboard(
  fetcher: FetchLike,
  apiBase: string,
  timezone: string,
  signal?: AbortSignal,
): Promise<DashboardSummary> {
  const query = new URLSearchParams({ timezone })
  const response = await fetcher(`${apiBase}/api/dashboard?${query.toString()}`, { signal })
  if (!response.ok) throw new Error(`Dashboard request failed with status ${response.status}`)
  return await response.json() as DashboardSummary
}

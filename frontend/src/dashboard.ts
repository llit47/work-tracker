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

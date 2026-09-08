import { useEffect, useState } from 'react'

import {
  DASHBOARD_POLL_INTERVAL_MS,
  LIVE_TIMER_INTERVAL_MS,
  advanceElapsedSeconds,
  advanceTodayEffectiveDuration,
  browserTimezone,
  dashboardStatusPresentation,
  formatDashboardDuration,
  formatLiveTimer,
  loadDashboard,
  type DashboardSummary,
} from './dashboard'
import { formatMoney } from './pay'

type DashboardPanelProps = {
  apiBase: string
  refreshRequest: number
  onSummaryChange: (summary: DashboardSummary | null) => void
}

function formatShiftStart(timestamp: string): string {
  return new Intl.DateTimeFormat('pl-PL', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp))
}

export default function DashboardPanel({
  apiBase,
  refreshRequest,
  onSummaryChange,
}: DashboardPanelProps) {
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [retryRequest, setRetryRequest] = useState(0)
  const [receivedAt, setReceivedAt] = useState(0)
  const [timerNow, setTimerNow] = useState(() => Date.now())

  useEffect(() => {
    let disposed = false
    let requestGeneration = 0
    let activeController: AbortController | null = null

    const refresh = async (showLoading: boolean) => {
      requestGeneration += 1
      const generation = requestGeneration
      activeController?.abort()
      const controller = new AbortController()
      activeController = controller
      if (showLoading) setState('loading')

      try {
        const loaded = await loadDashboard(
          fetch,
          apiBase,
          browserTimezone(),
          controller.signal,
        )
        if (!disposed && generation === requestGeneration) {
          const loadedAt = Date.now()
          setDashboard(loaded)
          onSummaryChange(loaded)
          setReceivedAt(loadedAt)
          setTimerNow(loadedAt)
          setState('ready')
        }
      } catch {
        if (!disposed && generation === requestGeneration) {
          setDashboard(null)
          setState('error')
          onSummaryChange(null)
        }
      }
    }

    void refresh(true)
    const pollingTimer = window.setInterval(
      () => { void refresh(false) },
      DASHBOARD_POLL_INTERVAL_MS,
    )
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') void refresh(false)
    }
    window.addEventListener('focus', refreshWhenVisible)
    document.addEventListener('visibilitychange', refreshWhenVisible)

    return () => {
      disposed = true
      requestGeneration += 1
      activeController?.abort()
      window.clearInterval(pollingTimer)
      window.removeEventListener('focus', refreshWhenVisible)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [apiBase, onSummaryChange, refreshRequest, retryRequest])

  useEffect(() => {
    if (dashboard?.status !== 'working' || !dashboard.current_session) return undefined
    const timer = window.setInterval(() => setTimerNow(Date.now()), LIVE_TIMER_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [dashboard])

  if (state === 'loading' && !dashboard) {
    return <section className="live-dashboard loading" aria-label="Bieżący status"><p>Ładowanie bieżącego statusu…</p></section>
  }

  if (state === 'error') {
    return (
      <section className="live-dashboard dashboard-error" aria-label="Bieżący status" role="alert">
        <p>Nie udało się pobrać bieżącego statusu.</p>
        <button type="button" onClick={() => setRetryRequest((request) => request + 1)}>Spróbuj ponownie</button>
      </section>
    )
  }

  if (!dashboard) return null

  const presentation = dashboardStatusPresentation[dashboard.status]
  const liveElapsed = dashboard.current_session
    ? advanceElapsedSeconds(dashboard.current_session.elapsed_seconds, receivedAt, timerNow)
    : 0
  const todayEffectiveDuration = advanceTodayEffectiveDuration(
    dashboard.today,
    dashboard.current_session?.elapsed_seconds ?? 0,
    liveElapsed,
  )

  return (
    <section className={`live-dashboard ${dashboard.status}`} aria-label="Bieżący status" aria-live="polite">
      <header className="live-status-heading">
        <div>
          <span className="live-status-label">Bieżący status</span>
          <strong>{presentation.label}</strong>
        </div>
        <p>{presentation.description}</p>
      </header>

      <div className="dashboard-metrics">
        <div>
          <span>Dzisiaj</span>
          <strong>{formatDashboardDuration(todayEffectiveDuration)}</strong>
          <small>Ukończone: {formatDashboardDuration(dashboard.today.completed_duration_seconds)}</small>
        </div>
        <div className="live-shift-metric">
          <span>Bieżąca zmiana</span>
          <strong>{dashboard.status === 'working' ? formatLiveTimer(liveElapsed) : '—'}</strong>
          <small>
            {dashboard.current_session
              ? `Od ${formatShiftStart(dashboard.current_session.entry_timestamp)}`
              : 'Brak otwartej zmiany'}
          </small>
        </div>
        <div>
          <span>Ten miesiąc</span>
          <strong>{formatDashboardDuration(dashboard.month.completed_duration_seconds)}</strong>
          <small>Dni pracy: {dashboard.month.work_days}</small>
        </div>
        <div>
          <span>Wynagrodzenie</span>
          <strong>{formatMoney(dashboard.month.pay, dashboard.month.currency)}</strong>
          <small>Tylko zakończone, poprawne sesje</small>
        </div>
      </div>
    </section>
  )
}

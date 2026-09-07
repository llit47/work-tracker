import { useEffect, useState } from 'react'

import {
  MAX_YEAR,
  MIN_YEAR,
  currentMonth,
  isSameMonth,
  millisecondsUntilNextLocalDay,
  monthInputValue,
  monthSearch,
  parseMonthInput,
  readMonthFromSearch,
  shiftMonth,
  type MonthSelection,
} from './monthNavigation'

type WorkEvent = {
  id: number
  event_type: 'entry' | 'exit'
  location: string
  event_timestamp: string
  event_timestamp_utc: string
  received_at: string
  source: string
}

type SessionStatus =
  | 'valid'
  | 'missing_exit'
  | 'duplicate_entry'
  | 'orphan_exit'
  | 'unusually_long_session'
  | 'ambiguous_timestamp'

type WorkTimeItem = {
  status: SessionStatus
  location: string
  local_date: string
  duration_seconds: number | null
  events: WorkEvent[]
}

type WorkDay = {
  date: string
  total_duration_seconds: number
  anomaly_count: number
  items: WorkTimeItem[]
}

type WorkSummary = {
  year: number
  month: number
  total_duration_seconds: number
  work_days: number
  anomaly_count: number
  days: WorkDay[]
}

const anomalyLabels: Record<Exclude<SessionStatus, 'valid'>, string> = {
  missing_exit: 'Brak wyjścia',
  duplicate_entry: 'Niejednoznaczne wejście',
  orphan_exit: 'Wyjście bez wejścia',
  unusually_long_session: 'Podejrzanie długa sesja',
  ambiguous_timestamp: 'Sprzeczne zdarzenia o tej samej godzinie',
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? ''

function formatMonth(selection: MonthSelection) {
  const date = new Date(selection.year, selection.month - 1, 1)
  return new Intl.DateTimeFormat('pl-PL', { month: 'long', year: 'numeric' }).format(date).toUpperCase()
}

function formatDay(date: string) {
  return new Intl.DateTimeFormat('pl-PL', { day: 'numeric', month: 'long' }).format(new Date(`${date}T00:00:00Z`))
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return '—'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60
  const parts = []
  if (hours > 0) parts.push(`${hours}h`)
  if (minutes > 0 || hours > 0) parts.push(`${minutes}m`)
  if (remainingSeconds > 0) parts.push(`${remainingSeconds}s`)
  return parts.length > 0 ? parts.join(' ') : '0m'
}

function formatEventTime(timestamp: string) {
  const offset = timestamp.endsWith('Z') ? 'UTC' : `UTC${timestamp.slice(-6)}`
  return `${timestamp.slice(11, 16)} ${offset}`
}

function SessionItem({ item }: { item: WorkTimeItem }) {
  const entry = item.events.find((event) => event.event_type === 'entry')
  const exit = item.events.find((event) => event.event_type === 'exit')

  if (item.status === 'valid' || item.status === 'unusually_long_session') {
    return (
      <article className={`session ${item.status === 'valid' ? 'valid' : 'warning'}`}>
        <div>
          <strong>{entry ? formatEventTime(entry.event_timestamp) : '—'} → {exit ? formatEventTime(exit.event_timestamp) : '—'}</strong>
          <span className="location">{item.location}</span>
        </div>
        <div className="session-result">
          <strong>{formatDuration(item.duration_seconds)}</strong>
          {item.status !== 'valid' && <span className="anomaly">⚠ {anomalyLabels[item.status]}</span>}
        </div>
      </article>
    )
  }

  return (
    <article className="session warning">
      <div className="raw-events">
        {item.events.map((event) => (
          <span key={event.id}>
            {formatEventTime(event.event_timestamp)} — {event.event_type === 'entry' ? 'WEJŚCIE' : 'WYJŚCIE'}
          </span>
        ))}
        <span className="location">{item.location}</span>
      </div>
      <span className="anomaly">⚠ {anomalyLabels[item.status]}</span>
    </article>
  )
}

function writeMonthToUrl(selection: MonthSelection, mode: 'push' | 'replace') {
  const url = `${window.location.pathname}${monthSearch(selection)}`
  if (mode === 'push') window.history.pushState(null, '', url)
  else window.history.replaceState(null, '', url)
}

function App() {
  const [today, setToday] = useState(() => currentMonth())
  const [initialUrlMonth] = useState(() => readMonthFromSearch(window.location.search, today))
  const [selectedMonth, setSelectedMonth] = useState(initialUrlMonth.selection)
  const [summary, setSummary] = useState<WorkSummary | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [retryRequest, setRetryRequest] = useState(0)

  useEffect(() => {
    let refreshTimer: number

    const refreshCurrentMonth = () => {
      const freshCurrentMonth = currentMonth()
      setToday((previousMonth) => (
        isSameMonth(previousMonth, freshCurrentMonth) ? previousMonth : freshCurrentMonth
      ))
    }

    const scheduleMidnightRefresh = () => {
      refreshTimer = window.setTimeout(() => {
        refreshCurrentMonth()
        scheduleMidnightRefresh()
      }, millisecondsUntilNextLocalDay())
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshCurrentMonth()
    }

    scheduleMidnightRefresh()
    window.addEventListener('focus', refreshCurrentMonth)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      window.clearTimeout(refreshTimer)
      window.removeEventListener('focus', refreshCurrentMonth)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [])

  useEffect(() => {
    if (initialUrlMonth.shouldNormalize) writeMonthToUrl(initialUrlMonth.selection, 'replace')
  }, [initialUrlMonth])

  useEffect(() => {
    const handlePopState = () => {
      const freshCurrentMonth = currentMonth()
      setToday((previousMonth) => (
        isSameMonth(previousMonth, freshCurrentMonth) ? previousMonth : freshCurrentMonth
      ))
      const urlMonth = readMonthFromSearch(window.location.search, freshCurrentMonth)
      if (urlMonth.shouldNormalize) writeMonthToUrl(urlMonth.selection, 'replace')
      setSelectedMonth(urlMonth.selection)
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let ignoreResponse = false

    setSummary(null)
    setState('loading')

    const load = async () => {
      try {
        const response = await fetch(
          `${apiBase}/api/work-summary?year=${selectedMonth.year}&month=${selectedMonth.month}`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error('API request failed')
        const loadedSummary: WorkSummary = await response.json()
        if (!ignoreResponse) {
          setSummary(loadedSummary)
          setState('ready')
        }
      } catch {
        if (!ignoreResponse) setState('error')
      }
    }
    void load()

    return () => {
      ignoreResponse = true
      controller.abort()
    }
  }, [selectedMonth.year, selectedMonth.month, retryRequest])

  const selectMonth = (selection: MonthSelection) => {
    if (isSameMonth(selection, selectedMonth)) return
    writeMonthToUrl(selection, 'push')
    setSelectedMonth(selection)
  }

  const isFirstSupportedMonth = selectedMonth.year === MIN_YEAR && selectedMonth.month === 1
  const isLastSupportedMonth = selectedMonth.year === MAX_YEAR && selectedMonth.month === 12
  const isCurrentMonth = isSameMonth(selectedMonth, today)
  const averageDayDuration = summary && summary.work_days > 0
    ? Math.floor(summary.total_duration_seconds / summary.work_days)
    : 0

  return (
    <main className="page">
      <section className="card" aria-live="polite">
        <h1>Work Tracker</h1>
        <nav className="month-navigation" aria-label="Nawigacja miesiąca">
          <button
            className="month-arrow"
            type="button"
            aria-label="Poprzedni miesiąc"
            title="Poprzedni miesiąc"
            disabled={isFirstSupportedMonth}
            onClick={() => selectMonth(shiftMonth(selectedMonth, -1))}
          >
            ‹
          </button>
          <h2>{formatMonth(selectedMonth)}</h2>
          <button
            className="month-arrow"
            type="button"
            aria-label="Następny miesiąc"
            title="Następny miesiąc"
            disabled={isLastSupportedMonth}
            onClick={() => selectMonth(shiftMonth(selectedMonth, 1))}
          >
            ›
          </button>
        </nav>
        <div className="month-actions">
          <label>
            <span>Wybierz miesiąc</span>
            <input
              type="month"
              min={`${MIN_YEAR}-01`}
              max={`${MAX_YEAR}-12`}
              value={monthInputValue(selectedMonth)}
              onChange={(event) => {
                const selection = parseMonthInput(event.target.value)
                if (selection) selectMonth(selection)
              }}
            />
          </label>
          <button
            type="button"
            disabled={isCurrentMonth}
            onClick={() => {
              const freshCurrentMonth = currentMonth()
              setToday(freshCurrentMonth)
              selectMonth(freshCurrentMonth)
            }}
          >
            Dzisiaj
          </button>
        </div>
        {state === 'loading' && <p className="message">Ładowanie czasu pracy…</p>}
        {state === 'error' && (
          <div className="message error-message">
            <p>Nie udało się pobrać danych. Sprawdź połączenie z API.</p>
            <button type="button" onClick={() => setRetryRequest((request) => request + 1)}>Spróbuj ponownie</button>
          </div>
        )}
        {state === 'ready' && summary && <>
          <section className="summary" aria-label="Podsumowanie miesiąca">
            <div><span>Czas pracy</span><strong>{formatDuration(summary.total_duration_seconds)}</strong></div>
            <div><span>Dni pracy</span><strong>{summary.work_days}</strong></div>
            <div><span>Średnio dziennie</span><strong>{formatDuration(averageDayDuration)}</strong></div>
            <div><span>Problemy</span><strong className={summary.anomaly_count > 0 ? 'problem-count' : ''}>{summary.anomaly_count}</strong></div>
          </section>

          {summary.days.length === 0 && <p className="message">Brak zdarzeń w tym miesiącu.</p>}
          <div className="days">
            {summary.days.map((day) => (
              <section className="day" key={day.date}>
                <div className="day-heading">
                  <h3>{formatDay(day.date)}</h3>
                  <span>Razem: <strong>{formatDuration(day.total_duration_seconds)}</strong></span>
                </div>
                <div className="sessions">
                  {day.items.map((item) => <SessionItem key={`${item.status}-${item.events.map((event) => event.id).join('-')}`} item={item} />)}
                </div>
              </section>
            ))}
          </div>
        </>}
      </section>
    </main>
  )
}

export default App

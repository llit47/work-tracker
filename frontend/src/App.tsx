import { useEffect, useState } from 'react'

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

function formatMonth(date: Date) {
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

function App() {
  const now = new Date()
  const [summary, setSummary] = useState<WorkSummary | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetch(`${apiBase}/api/work-summary?year=${now.getFullYear()}&month=${now.getMonth() + 1}`)
        if (!response.ok) throw new Error('API request failed')
        setSummary(await response.json())
        setState('ready')
      } catch {
        setState('error')
      }
    }
    void load()
  }, [])

  return (
    <main className="page">
      <section className="card" aria-live="polite">
        <h1>Work Tracker</h1>
        <h2>{formatMonth(now)}</h2>
        {state === 'loading' && <p className="message">Ładowanie czasu pracy…</p>}
        {state === 'error' && <p className="message error">Nie udało się pobrać danych. Sprawdź połączenie z API.</p>}
        {state === 'ready' && summary && <>
          <section className="summary" aria-label="Podsumowanie miesiąca">
            <div><span>Czas pracy</span><strong>{formatDuration(summary.total_duration_seconds)}</strong></div>
            <div><span>Dni pracy</span><strong>{summary.work_days}</strong></div>
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

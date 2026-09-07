import { useEffect, useState, type FormEvent } from 'react'

import {
  extractOffsetFromIso,
  getPossibleOffsetsForLocalDateTime,
  localDateTimeToOffsetIso,
  resolveCorrectionTimestamp,
  timestampToLocalInput,
  type ManualEventType,
} from './corrections'
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
import {
  getVisibleDays,
  hasStandaloneUndoAction,
  isEventVisible,
  readShowIgnoredEventsPreference,
  writeShowIgnoredEventsPreference,
} from './viewSettings'

type WorkEvent = {
  id: number
  event_type: 'entry' | 'exit'
  location: string
  event_timestamp: string
  event_timestamp_utc: string
  received_at: string
  source: string
  raw_event_id: number | null
  correction_id: number | null
  correction_type: 'timestamp_override' | 'ignore_event' | 'manual_event' | null
  original_event_timestamp: string | null
  is_manual: boolean
  is_ignored: boolean
  is_timestamp_corrected: boolean
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
  ignored_events: WorkEvent[]
}

type WorkSummary = {
  year: number
  month: number
  total_duration_seconds: number
  work_days: number
  anomaly_count: number
  days: WorkDay[]
}

type CorrectionForm =
  | {
      kind: 'timestamp'
      event: WorkEvent
      value: string
      initialValue: string
      existingTimestamp: string
      selectedOffset: string | null
    }
  | {
      kind: 'manual'
      eventType: ManualEventType
      location: string
      value: string
      selectedOffset: string | null
    }

type EventActions = {
  onEdit: (event: WorkEvent) => void
  onIgnore: (event: WorkEvent) => void
  onUndo: (event: WorkEvent) => void
  disabled: boolean
}

const anomalyLabels: Record<Exclude<SessionStatus, 'valid'>, string> = {
  missing_exit: 'Brak wyjścia',
  duplicate_entry: 'Niejednoznaczne wejście',
  orphan_exit: 'Wyjście bez wejścia',
  unusually_long_session: 'Podejrzanie długa sesja',
  ambiguous_timestamp: 'Sprzeczne zdarzenia o tej samej godzinie',
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? ''
const defaultLocation = 'gabinet_zabki'

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

function formatEventDateTime(timestamp: string) {
  return `${timestamp.slice(0, 10)} ${formatEventTime(timestamp)}`
}

function EventRow({ event, actions }: { event: WorkEvent; actions: EventActions }) {
  const eventLabel = event.event_type === 'entry' ? 'WEJŚCIE' : 'WYJŚCIE'

  return (
    <div className={`event-row${event.is_ignored ? ' ignored' : ''}`}>
      <div className="event-audit">
        {event.is_timestamp_corrected && event.original_event_timestamp ? (
          <>
            <span>Oryginalnie: {formatEventDateTime(event.original_event_timestamp)} — {eventLabel}</span>
            <strong>Po korekcie: {formatEventDateTime(event.event_timestamp)} — {eventLabel}</strong>
            <small className="audit-status corrected">Zdarzenie Home Assistant · Korekta ręczna</small>
          </>
        ) : (
          <>
            <strong>{formatEventTime(event.event_timestamp)} — {eventLabel}</strong>
            {!event.is_manual && !event.is_ignored && <small className="audit-status source">Home Assistant</small>}
            {event.is_manual && <small className="audit-status manual">Dodano ręcznie</small>}
            {event.is_ignored && <small className="audit-status ignored">Zdarzenie Home Assistant · Zignorowano ręcznie</small>}
          </>
        )}
      </div>
      <div className="event-actions">
        {!event.is_manual && !event.is_ignored && !event.is_timestamp_corrected && (
          <>
            <button type="button" disabled={actions.disabled} onClick={() => actions.onEdit(event)}>Edytuj godzinę</button>
            <button type="button" disabled={actions.disabled} onClick={() => actions.onIgnore(event)}>Ignoruj zdarzenie</button>
          </>
        )}
        {event.is_timestamp_corrected && (
          <>
            <button type="button" disabled={actions.disabled} onClick={() => actions.onEdit(event)}>Edytuj godzinę</button>
            <button type="button" disabled={actions.disabled} onClick={() => actions.onUndo(event)}>Cofnij korektę</button>
          </>
        )}
        {hasStandaloneUndoAction(event) && (
          <button type="button" disabled={actions.disabled} onClick={() => actions.onUndo(event)}>Cofnij korektę</button>
        )}
      </div>
    </div>
  )
}

function SessionItem({ item, actions }: { item: WorkTimeItem; actions: EventActions }) {
  const entry = item.events.find((event) => event.event_type === 'entry')
  const exit = item.events.find((event) => event.event_type === 'exit')

  if (item.status === 'valid' || item.status === 'unusually_long_session') {
    return (
      <article className={`session ${item.status === 'valid' ? 'valid' : 'warning'}`}>
        <div className="session-details">
          <strong className="session-range">{entry ? formatEventTime(entry.event_timestamp) : '—'} → {exit ? formatEventTime(exit.event_timestamp) : '—'}</strong>
          <span className="location">{item.location}</span>
          <div className="event-list">
            {item.events.map((event) => <EventRow key={event.id} event={event} actions={actions} />)}
          </div>
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
      <div className="session-details">
        <div className="event-list">
          {item.events.map((event) => <EventRow key={event.id} event={event} actions={actions} />)}
        </div>
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

function findSummaryLocation(summary: WorkSummary | null): string {
  if (!summary) return defaultLocation
  for (const day of summary.days) {
    const itemLocation = day.items[0]?.location
    if (itemLocation) return itemLocation
    const ignoredLocation = day.ignored_events[0]?.location
    if (ignoredLocation) return ignoredLocation
  }
  return defaultLocation
}

function App() {
  const [today, setToday] = useState(() => currentMonth())
  const [initialUrlMonth] = useState(() => readMonthFromSearch(window.location.search, today))
  const [selectedMonth, setSelectedMonth] = useState(initialUrlMonth.selection)
  const [summary, setSummary] = useState<WorkSummary | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [retryRequest, setRetryRequest] = useState(0)
  const [correctionForm, setCorrectionForm] = useState<CorrectionForm | null>(null)
  const [isSavingCorrection, setIsSavingCorrection] = useState(false)
  const [actionMessage, setActionMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [showIgnoredEvents, setShowIgnoredEvents] = useState(() => {
    try {
      return readShowIgnoredEventsPreference(window.localStorage)
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      writeShowIgnoredEventsPreference(window.localStorage, showIgnoredEvents)
    } catch {
      // The setting still works until this page is closed when storage is unavailable.
    }
  }, [showIgnoredEvents])

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

  useEffect(() => {
    setCorrectionForm(null)
    setActionMessage(null)
  }, [selectedMonth.year, selectedMonth.month])

  const selectMonth = (selection: MonthSelection) => {
    if (isSameMonth(selection, selectedMonth)) return
    writeMonthToUrl(selection, 'push')
    setSelectedMonth(selection)
  }

  const refreshSummaryAfterCorrection = (message: string) => {
    setCorrectionForm(null)
    setActionMessage({ kind: 'success', text: message })
    setSummary(null)
    setState('loading')
    setRetryRequest((request) => request + 1)
  }

  const sendCorrectionRequest = async (url: string, request: RequestInit, successMessage: string) => {
    setIsSavingCorrection(true)
    setActionMessage(null)
    try {
      const response = await fetch(`${apiBase}${url}`, request)
      if (!response.ok) throw new Error('Correction request failed')
      refreshSummaryAfterCorrection(successMessage)
    } catch {
      setActionMessage({ kind: 'error', text: 'Nie udało się zapisać korekty. Spróbuj ponownie.' })
    } finally {
      setIsSavingCorrection(false)
    }
  }

  const saveCorrection = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!correctionForm) return
    const possibleOffsets = getPossibleOffsetsForLocalDateTime(correctionForm.value)
    const preservedOffset = correctionForm.kind === 'timestamp'
      && correctionForm.value === correctionForm.initialValue
      ? extractOffsetFromIso(correctionForm.existingTimestamp)
      : null
    const selectedOffset = correctionForm.selectedOffset ?? preservedOffset
    if (possibleOffsets.length > 1 && !selectedOffset) {
      setActionMessage({ kind: 'error', text: 'Wybierz właściwe wystąpienie godziny po zmianie czasu.' })
      return
    }
    const timestamp = correctionForm.kind === 'timestamp'
      ? resolveCorrectionTimestamp(
          correctionForm.value,
          correctionForm.existingTimestamp,
          correctionForm.initialValue,
          correctionForm.selectedOffset,
        )
      : localDateTimeToOffsetIso(correctionForm.value, selectedOffset ?? undefined)
    if (!timestamp) {
      setActionMessage({ kind: 'error', text: 'Podaj prawidłową lokalną datę i godzinę.' })
      return
    }

    if (correctionForm.kind === 'timestamp') {
      if (correctionForm.event.raw_event_id === null) return
      void sendCorrectionRequest(
        `/api/work-events/${correctionForm.event.raw_event_id}/timestamp-correction`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timestamp }),
        },
        'Korekta godziny została zapisana.',
      )
      return
    }

    void sendCorrectionRequest(
      '/api/manual-events',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event: correctionForm.eventType,
          location: correctionForm.location,
          timestamp,
        }),
      },
      'Ręczne zdarzenie zostało dodane.',
    )
  }

  const ignoreEvent = (event: WorkEvent) => {
    if (event.raw_event_id === null || !window.confirm('Czy na pewno chcesz zignorować to zdarzenie?')) return
    void sendCorrectionRequest(
      `/api/work-events/${event.raw_event_id}/ignore`,
      { method: 'PUT' },
      'Zdarzenie zostało zignorowane.',
    )
  }

  const undoCorrection = (event: WorkEvent) => {
    if (event.correction_id === null || !window.confirm('Czy na pewno chcesz cofnąć tę korektę?')) return
    void sendCorrectionRequest(
      `/api/corrections/${event.correction_id}`,
      { method: 'DELETE' },
      'Korekta została cofnięta.',
    )
  }

  const editEvent = (event: WorkEvent) => {
    setActionMessage(null)
    const value = timestampToLocalInput(event.event_timestamp)
    setCorrectionForm({
      kind: 'timestamp',
      event,
      value,
      initialValue: value,
      existingTimestamp: event.event_timestamp,
      selectedOffset: null,
    })
  }

  const addManualEvent = (eventType: ManualEventType) => {
    setActionMessage(null)
    setCorrectionForm({
      kind: 'manual',
      eventType,
      location: findSummaryLocation(summary),
      value: '',
      selectedOffset: null,
    })
  }

  const isFirstSupportedMonth = selectedMonth.year === MIN_YEAR && selectedMonth.month === 1
  const isLastSupportedMonth = selectedMonth.year === MAX_YEAR && selectedMonth.month === 12
  const isCurrentMonth = isSameMonth(selectedMonth, today)
  const averageDayDuration = summary && summary.work_days > 0
    ? Math.floor(summary.total_duration_seconds / summary.work_days)
    : 0
  const eventActions: EventActions = {
    onEdit: editEvent,
    onIgnore: ignoreEvent,
    onUndo: undoCorrection,
    disabled: isSavingCorrection,
  }
  const correctionPossibleOffsets = correctionForm
    ? getPossibleOffsetsForLocalDateTime(correctionForm.value)
    : []
  const correctionPreservedOffset = correctionForm?.kind === 'timestamp'
    && correctionForm.value === correctionForm.initialValue
    ? extractOffsetFromIso(correctionForm.existingTimestamp)
    : null
  const correctionSelectedOffset = correctionForm?.selectedOffset ?? correctionPreservedOffset ?? ''
  const visibleDays = summary ? getVisibleDays(summary.days, showIgnoredEvents) : []

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
        <details className="settings-panel">
          <summary>Ustawienia</summary>
          <div className="settings-content">
            <h3>Widok</h3>
            <label className="setting-option">
              <input
                type="checkbox"
                checked={showIgnoredEvents}
                onChange={(event) => setShowIgnoredEvents(event.target.checked)}
              />
              <span>Pokaż ignorowane wydarzenia</span>
            </label>
          </div>
        </details>
        <section className="correction-toolbar" aria-label="Korekty ręczne">
          <strong>Korekty ręczne</strong>
          <div>
            <button type="button" disabled={isSavingCorrection} onClick={() => addManualEvent('entry')}>Dodaj wejście</button>
            <button type="button" disabled={isSavingCorrection} onClick={() => addManualEvent('exit')}>Dodaj wyjście</button>
          </div>
        </section>
        {correctionForm && (
          <form className="correction-form" onSubmit={saveCorrection}>
            <h3>{correctionForm.kind === 'timestamp' ? 'Edytuj godzinę' : correctionForm.eventType === 'entry' ? 'Dodaj wejście' : 'Dodaj wyjście'}</h3>
            {correctionForm.kind === 'timestamp' ? (
              <div className="correction-context">
                <span>Oryginalna data i godzina: <strong>{formatEventDateTime(correctionForm.event.original_event_timestamp ?? correctionForm.event.event_timestamp)}</strong></span>
                {correctionForm.event.is_timestamp_corrected && (
                  <span>Aktualna korekta: <strong>{formatEventDateTime(correctionForm.event.event_timestamp)}</strong></span>
                )}
              </div>
            ) : (
              <p className="correction-context">Lokalizacja: <strong>{correctionForm.location}</strong></p>
            )}
            <label>
              <span>Data i godzina</span>
              <input
                type="datetime-local"
                min="2000-01-01T00:00:00"
                max="2100-12-31T23:59:59"
                step="1"
                required
                value={correctionForm.value}
                onChange={(event) => setCorrectionForm({
                  ...correctionForm,
                  value: event.target.value,
                  selectedOffset: null,
                })}
              />
            </label>
            {correctionPossibleOffsets.length > 1 && (
              <div className="dst-choice">
                <p>Ta godzina występuje dwa razy z powodu zmiany czasu. Wybierz właściwe wystąpienie.</p>
                <label>
                  <span>Wystąpienie godziny</span>
                  <select
                    required
                    value={correctionSelectedOffset}
                    onChange={(event) => setCorrectionForm({
                      ...correctionForm,
                      selectedOffset: event.target.value || null,
                    })}
                  >
                    <option value="">Wybierz wystąpienie</option>
                    {correctionPossibleOffsets.map((offset, index) => (
                      <option key={offset} value={offset}>
                        {index === 0 ? 'Pierwsze' : 'Drugie'} wystąpienie ({offset})
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
            <p className="timezone-note">Zostanie zapisana korekta ręczna z lokalnym offsetem strefy czasowej tego urządzenia.</p>
            <div className="form-actions">
              <button type="submit" disabled={isSavingCorrection}>{isSavingCorrection ? 'Zapisywanie…' : 'Zapisz'}</button>
              <button type="button" disabled={isSavingCorrection} onClick={() => setCorrectionForm(null)}>Anuluj</button>
            </div>
          </form>
        )}
        {actionMessage && (
          <p className={`action-message ${actionMessage.kind}`} role={actionMessage.kind === 'error' ? 'alert' : 'status'}>
            {actionMessage.text}
          </p>
        )}
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

          {visibleDays.length === 0 && <p className="message">Brak zdarzeń w tym miesiącu.</p>}
          <div className="days">
            {visibleDays.map((day) => (
              <section className="day" key={day.date}>
                <div className="day-heading">
                  <h3>{formatDay(day.date)}</h3>
                  <span>Razem: <strong>{formatDuration(day.total_duration_seconds)}</strong></span>
                </div>
                <div className="sessions">
                  {day.items.map((item) => (
                    <SessionItem
                      key={`${item.status}-${item.events.map((event) => event.id).join('-')}`}
                      item={item}
                      actions={eventActions}
                    />
                  ))}
                  {day.ignored_events
                    .filter((event) => isEventVisible(event, showIgnoredEvents))
                    .map((event) => (
                      <article className="session ignored-session" key={`ignored-${event.id}`}>
                        <div className="session-details">
                          <EventRow event={event} actions={eventActions} />
                          <span className="location">{event.location}</span>
                        </div>
                      </article>
                    ))}
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

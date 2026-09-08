import { useEffect, useRef, useState, type FormEvent } from 'react'

import {
  DEFAULT_APPLICATION_SETTINGS,
  getLocationDisplayName,
  loadApplicationSettings,
  saveApplicationSettings,
  validateApplicationTitle,
  type ApplicationSettings,
  type ApplicationSettingsFormErrors,
} from './applicationSettings'
import DashboardPanel from './DashboardPanel'
import {
  shouldRefreshMonthlyData,
  type DashboardSummary,
} from './dashboard'
import {
  downloadMonthlyExport,
  exportLabels,
  monthlyExportFilename,
  type ExportFormat,
} from './export'
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
  PayRateRequestError,
  PaySummaryRequestError,
  createPayRate,
  describeRatesUsed,
  findDailyPay,
  formatHourlyRate,
  formatMoney,
  loadPaySummary,
  validatePayRateForm,
  type MonthlyPaySummary,
  type PayRate,
  type PayRateFormErrors,
  type PayRateFormValues,
} from './pay'
import {
  countActionableProblems,
  dayOverviewPresentation,
  formatDuration,
  formatEventDateTime,
  formatEventTime,
  formatSessionRange,
  isPendingCurrentSession,
  timestampOffset,
  workItemStatusLabels,
  type ActiveSessionContext,
} from './presentation'
import {
  readThemePreference,
  resolveTheme,
  writeThemePreference,
  type ThemePreference,
} from './theme'
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

const apiBase = import.meta.env.VITE_API_BASE_URL ?? ''
const defaultLocation = 'gabinet_zabki'

function localDateInputValue(date = new Date()): string {
  const year = String(date.getFullYear()).padStart(4, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function initialPayRateForm(): PayRateFormValues {
  return { effectiveFrom: localDateInputValue(), hourlyRate: '', currency: 'PLN' }
}

function formatMonth(selection: MonthSelection) {
  const date = new Date(selection.year, selection.month - 1, 1)
  return new Intl.DateTimeFormat('pl-PL', { month: 'long', year: 'numeric' }).format(date).toUpperCase()
}

function formatDay(date: string) {
  return new Intl.DateTimeFormat('pl-PL', { day: 'numeric', month: 'long' }).format(new Date(`${date}T00:00:00Z`))
}

function formatCalendarDate(date: string): string {
  const [year, month, day] = date.split('-')
  return `${day}.${month}.${year}`
}

function timestampHasAmbiguousLocalTime(timestamp: string): boolean {
  return getPossibleOffsetsForLocalDateTime(timestampToLocalInput(timestamp)).length > 1
}

function EventRow({
  event,
  actions,
  showOffset = false,
}: {
  event: WorkEvent
  actions: EventActions
  showOffset?: boolean
}) {
  const eventLabel = event.event_type === 'entry' ? 'WEJŚCIE' : 'WYJŚCIE'
  const originalTimestamp = event.original_event_timestamp ?? event.event_timestamp
  const correctionNeedsOffsets = event.is_timestamp_corrected && (
    timestampOffset(originalTimestamp) !== timestampOffset(event.event_timestamp)
    || timestampHasAmbiguousLocalTime(originalTimestamp)
    || timestampHasAmbiguousLocalTime(event.event_timestamp)
  )
  const eventNeedsOffset = showOffset || timestampHasAmbiguousLocalTime(event.event_timestamp)

  return (
    <div className={`event-row${event.is_ignored ? ' ignored' : ''}`}>
      <div className="event-audit">
        {event.is_timestamp_corrected && event.original_event_timestamp ? (
          <>
            <span>Oryginalnie: {formatEventDateTime(event.original_event_timestamp, correctionNeedsOffsets)} — {eventLabel}</span>
            <strong>Po korekcie: {formatEventDateTime(event.event_timestamp, correctionNeedsOffsets)} — {eventLabel}</strong>
            <small className="audit-status corrected">Zdarzenie Home Assistant · Korekta ręczna</small>
          </>
        ) : (
          <>
            <strong>{formatEventTime(event.event_timestamp, eventNeedsOffset)} — {eventLabel}</strong>
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

function SessionItem({
  item,
  actions,
  locationName,
  activeSessionContext,
}: {
  item: WorkTimeItem
  actions: EventActions
  locationName: string
  activeSessionContext: ActiveSessionContext
}) {
  const entry = item.events.find((event) => event.event_type === 'entry')
  const exit = item.events.find((event) => event.event_type === 'exit')
  const showEventOffsets = new Set(item.events.map((event) => timestampOffset(event.event_timestamp))).size > 1

  if (item.status === 'valid' || item.status === 'unusually_long_session') {
    return (
      <article className={`session ${item.status === 'valid' ? 'valid' : 'warning'}`}>
        <div className="session-details">
          <strong className="session-range">{formatSessionRange(entry?.event_timestamp ?? null, exit?.event_timestamp ?? null)}</strong>
          <span className="location">{locationName}</span>
          <div className="event-list">
            {item.events.map((event) => (
              <EventRow key={event.id} event={event} actions={actions} showOffset={showEventOffsets} />
            ))}
          </div>
        </div>
        <div className="session-result">
          <strong>{formatDuration(item.duration_seconds)}</strong>
          {item.status !== 'valid' && <span className="anomaly">⚠ {workItemStatusLabels[item.status]}</span>}
        </div>
      </article>
    )
  }

  if (isPendingCurrentSession(item, activeSessionContext)) {
    return (
      <article className="session pending">
        <div className="session-details">
          <strong className="session-range">{formatSessionRange(entry?.event_timestamp ?? null, null)}</strong>
          <span className="location">{locationName}</span>
          <div className="event-list">
            {item.events.map((event) => (
              <EventRow key={event.id} event={event} actions={actions} showOffset={showEventOffsets} />
            ))}
          </div>
        </div>
        <span className="pending-status">Trwająca zmiana</span>
      </article>
    )
  }

  return (
    <article className="session warning">
      <div className="session-details">
        <div className="event-list">
          {item.events.map((event) => (
            <EventRow key={event.id} event={event} actions={actions} showOffset={showEventOffsets} />
          ))}
        </div>
        <span className="location">{locationName}</span>
      </div>
      <span className="anomaly">⚠ {workItemStatusLabels[item.status]}</span>
    </article>
  )
}

function DayOverview({
  day,
  settings,
  showIgnoredEvents,
  activeSessionContext,
}: {
  day: WorkDay
  settings: ApplicationSettings
  showIgnoredEvents: boolean
  activeSessionContext: ActiveSessionContext
}) {
  const locations = [...new Set([
    ...day.items.map((item) => item.location),
    ...(showIgnoredEvents ? day.ignored_events.map((event) => event.location) : []),
  ])].map((location) => getLocationDisplayName(settings, location))

  return (
    <div className="day-overview">
      <div className="day-overview-items">
        {day.items.map((item) => {
          const entry = item.events.find((event) => event.event_type === 'entry')
          const exit = item.events.find((event) => event.event_type === 'exit')
          const presentation = dayOverviewPresentation(item, activeSessionContext)
          return (
            <span
              className={`day-overview-item${presentation.showWarning ? ' warning' : presentation.statusLabel ? ' pending' : ''}`}
              key={`${item.status}-${item.events.map((event) => event.id).join('-')}`}
            >
              {presentation.showRange && (
                <strong>{formatSessionRange(entry?.event_timestamp ?? null, exit?.event_timestamp ?? null)}</strong>
              )}
              {presentation.showDuration && <span>{formatDuration(item.duration_seconds)}</span>}
              {presentation.statusLabel && (
                <strong>{presentation.showWarning ? '⚠ ' : ''}{presentation.statusLabel}</strong>
              )}
            </span>
          )
        })}
        {showIgnoredEvents && day.ignored_events.length > 0 && (
          <span className="day-overview-item ignored-note">
            Zignorowane zdarzenia: {day.ignored_events.length}
          </span>
        )}
      </div>
      {locations.length > 0 && <small>{locations.join(', ')}</small>}
    </div>
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
  const [dashboardSummary, setDashboardSummary] = useState<DashboardSummary | null>(null)
  const previousDashboardSummary = useRef<DashboardSummary | null>(null)
  const [initialUrlMonth] = useState(() => readMonthFromSearch(window.location.search, today))
  const [selectedMonth, setSelectedMonth] = useState(initialUrlMonth.selection)
  const [summary, setSummary] = useState<WorkSummary | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [retryRequest, setRetryRequest] = useState(0)
  const [dashboardRefreshRequest, setDashboardRefreshRequest] = useState(0)
  const [paySummary, setPaySummary] = useState<MonthlyPaySummary | null>(null)
  const [payState, setPayState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [payError, setPayError] = useState('')
  const [payRetryRequest, setPayRetryRequest] = useState(0)
  const [payRates, setPayRates] = useState<PayRate[]>([])
  const [payRatesState, setPayRatesState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [payRatesRetryRequest, setPayRatesRetryRequest] = useState(0)
  const [payRateForm, setPayRateForm] = useState<PayRateFormValues>(() => initialPayRateForm())
  const [payRateFormErrors, setPayRateFormErrors] = useState<PayRateFormErrors>({})
  const [isSavingPayRate, setIsSavingPayRate] = useState(false)
  const [payRateMessage, setPayRateMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [correctionForm, setCorrectionForm] = useState<CorrectionForm | null>(null)
  const [isSavingCorrection, setIsSavingCorrection] = useState(false)
  const [actionMessage, setActionMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [exportingFormat, setExportingFormat] = useState<ExportFormat | null>(null)
  const [exportError, setExportError] = useState('')
  const [showIgnoredEvents, setShowIgnoredEvents] = useState(() => {
    try {
      return readShowIgnoredEventsPreference(window.localStorage)
    } catch {
      return false
    }
  })
  const [themePreference, setThemePreference] = useState<ThemePreference>(() => {
    try {
      return readThemePreference(window.localStorage)
    } catch {
      return 'auto'
    }
  })
  const [applicationSettings, setApplicationSettings] = useState<ApplicationSettings>(
    DEFAULT_APPLICATION_SETTINGS,
  )
  const [applicationSettingsForm, setApplicationSettingsForm] = useState<ApplicationSettings>(
    DEFAULT_APPLICATION_SETTINGS,
  )
  const [applicationSettingsState, setApplicationSettingsState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [applicationSettingsRetry, setApplicationSettingsRetry] = useState(0)
  const [applicationSettingsErrors, setApplicationSettingsErrors] = useState<ApplicationSettingsFormErrors>({})
  const [applicationSettingsMessage, setApplicationSettingsMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [isSavingApplicationSettings, setIsSavingApplicationSettings] = useState(false)

  useEffect(() => {
    try {
      writeShowIgnoredEventsPreference(window.localStorage, showIgnoredEvents)
    } catch {
      // The setting still works until this page is closed when storage is unavailable.
    }
  }, [showIgnoredEvents])

  useEffect(() => {
    try {
      writeThemePreference(window.localStorage, themePreference)
    } catch {
      // The theme remains active for this tab when storage is unavailable.
    }
    const colorScheme = window.matchMedia('(prefers-color-scheme: dark)')
    const applyResolvedTheme = () => {
      document.documentElement.dataset.theme = resolveTheme(themePreference, colorScheme.matches)
    }
    applyResolvedTheme()
    if (themePreference === 'auto') colorScheme.addEventListener('change', applyResolvedTheme)
    return () => colorScheme.removeEventListener('change', applyResolvedTheme)
  }, [themePreference])

  useEffect(() => {
    const controller = new AbortController()
    let ignoreResponse = false
    setApplicationSettingsState('loading')
    const load = async () => {
      try {
        const loaded = await loadApplicationSettings(fetch, apiBase, controller.signal)
        if (!ignoreResponse) {
          setApplicationSettings(loaded)
          setApplicationSettingsForm(loaded)
          setApplicationSettingsState('ready')
        }
      } catch {
        if (!ignoreResponse) setApplicationSettingsState('error')
      }
    }
    void load()
    return () => {
      ignoreResponse = true
      controller.abort()
    }
  }, [applicationSettingsRetry])

  useEffect(() => {
    document.title = applicationSettings.application_title
  }, [applicationSettings.application_title])

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
    if (dashboardSummary === null) return

    const previousDashboard = previousDashboardSummary.current
    previousDashboardSummary.current = dashboardSummary
    if (!shouldRefreshMonthlyData(previousDashboard, dashboardSummary)) return

    setRetryRequest((request) => request + 1)
    setPayRetryRequest((request) => request + 1)
  }, [dashboardSummary])

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
    const controller = new AbortController()
    let ignoreResponse = false

    setPaySummary(null)
    setPayState('loading')
    setPayError('')

    const load = async () => {
      try {
        const loadedSummary = await loadPaySummary(
          fetch,
          apiBase,
          selectedMonth.year,
          selectedMonth.month,
          controller.signal,
        )
        if (!ignoreResponse) {
          setPaySummary(loadedSummary)
          setPayState('ready')
        }
      } catch (error) {
        if (!ignoreResponse) {
          setPayError(error instanceof PaySummaryRequestError && error.status === 409
            ? 'Nie można obliczyć łącznego wynagrodzenia dla stawek w różnych walutach.'
            : 'Nie udało się pobrać danych o wynagrodzeniu.')
          setPayState('error')
        }
      }
    }
    void load()

    return () => {
      ignoreResponse = true
      controller.abort()
    }
  }, [selectedMonth.year, selectedMonth.month, payRetryRequest])

  useEffect(() => {
    const controller = new AbortController()
    let ignoreResponse = false

    setPayRatesState('loading')

    const load = async () => {
      try {
        const response = await fetch(`${apiBase}/api/pay-rates`, { signal: controller.signal })
        if (!response.ok) throw new Error('Pay-rate request failed')
        const loadedRates: PayRate[] = await response.json()
        if (!ignoreResponse) {
          setPayRates(loadedRates)
          setPayRatesState('ready')
        }
      } catch {
        if (!ignoreResponse) setPayRatesState('error')
      }
    }
    void load()

    return () => {
      ignoreResponse = true
      controller.abort()
    }
  }, [payRatesRetryRequest])

  useEffect(() => {
    setCorrectionForm(null)
    setActionMessage(null)
    setExportError('')
  }, [selectedMonth.year, selectedMonth.month])

  const selectMonth = (selection: MonthSelection) => {
    if (isSameMonth(selection, selectedMonth)) return
    writeMonthToUrl(selection, 'push')
    setSelectedMonth(selection)
  }

  const refreshPaySummary = () => {
    setPaySummary(null)
    setPayState('loading')
    setPayError('')
    setPayRetryRequest((request) => request + 1)
  }

  const refreshSummaryAfterCorrection = (message: string) => {
    setCorrectionForm(null)
    setActionMessage({ kind: 'success', text: message })
    setSummary(null)
    setState('loading')
    setRetryRequest((request) => request + 1)
    refreshPaySummary()
    setDashboardRefreshRequest((request) => request + 1)
  }

  const saveGlobalApplicationSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setApplicationSettingsMessage(null)
    const titleValidation = validateApplicationTitle(applicationSettingsForm.application_title)
    setApplicationSettingsErrors(titleValidation.errors)
    if (!titleValidation.value) return

    setIsSavingApplicationSettings(true)
    try {
      const saved = await saveApplicationSettings(fetch, apiBase, {
        application_title: titleValidation.value,
        locations: applicationSettingsForm.locations,
      })
      setApplicationSettings(saved)
      setApplicationSettingsForm(saved)
      setApplicationSettingsState('ready')
      setApplicationSettingsMessage({ kind: 'success', text: 'Ustawienia aplikacji zostały zapisane.' })
    } catch {
      setApplicationSettingsMessage({ kind: 'error', text: 'Nie udało się zapisać ustawień aplikacji.' })
    } finally {
      setIsSavingApplicationSettings(false)
    }
  }

  const savePayRate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setPayRateMessage(null)
    const validation = validatePayRateForm(payRateForm)
    setPayRateFormErrors(validation.errors)
    if (!validation.payload) return

    setIsSavingPayRate(true)
    try {
      await createPayRate(fetch, apiBase, validation.payload, {
        rates: () => setPayRatesRetryRequest((request) => request + 1),
        paySummary: refreshPaySummary,
      })
      setDashboardRefreshRequest((request) => request + 1)
      setPayRateForm((current) => ({ ...current, hourlyRate: '' }))
      setPayRateMessage({ kind: 'success', text: 'Nowa stawka została zapisana.' })
    } catch (error) {
      const message = error instanceof PayRateRequestError && error.status === 409
        ? 'Dla tej daty obowiązywania istnieje już stawka.'
        : error instanceof PayRateRequestError && error.status === 422
          ? 'Nie udało się zapisać stawki. Popraw dane formularza.'
          : 'Nie udało się zapisać stawki. Spróbuj ponownie.'
      setPayRateMessage({ kind: 'error', text: message })
    } finally {
      setIsSavingPayRate(false)
    }
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

  const startExport = async (format: ExportFormat) => {
    setExportingFormat(format)
    setExportError('')
    try {
      const blob = await downloadMonthlyExport(
        fetch,
        apiBase,
        format,
        selectedMonth.year,
        selectedMonth.month,
      )
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = monthlyExportFilename(format, selectedMonth.year, selectedMonth.month)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
    } catch {
      setExportError('Nie udało się przygotować pliku. Spróbuj ponownie.')
    } finally {
      setExportingFormat(null)
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
  const correctionOriginalTimestamp = correctionForm?.kind === 'timestamp'
    ? correctionForm.event.original_event_timestamp ?? correctionForm.event.event_timestamp
    : null
  const correctionAuditShowsOffsets = correctionForm?.kind === 'timestamp'
    && correctionOriginalTimestamp !== null
    && (
      timestampOffset(correctionOriginalTimestamp) !== timestampOffset(correctionForm.event.event_timestamp)
      || timestampHasAmbiguousLocalTime(correctionOriginalTimestamp)
      || timestampHasAmbiguousLocalTime(correctionForm.event.event_timestamp)
    )
  const visibleDays = summary ? getVisibleDays(summary.days, showIgnoredEvents) : []
  const activeSessionContext: ActiveSessionContext = {
    dashboardStatus: dashboardSummary?.status ?? null,
    currentEntryTimestampUtc: dashboardSummary?.current_session?.entry_timestamp_utc ?? null,
    runningToday: dashboardSummary !== null && dashboardSummary.today.running_duration_seconds !== null,
  }
  const visibleProblemCount = summary
    ? summary.days.reduce(
        (total, day) => total + countActionableProblems(day.items, activeSessionContext),
        0,
      )
    : 0
  const latestPayRate = payRates.length > 0 ? payRates[payRates.length - 1] : null
  const usedRateDescription = paySummary
    ? describeRatesUsed(paySummary.rates_used, paySummary.currency)
    : 'Stawka niedostępna'

  return (
    <main className="page">
      <section className="card" aria-live="polite">
        <h1>{applicationSettings.application_title}</h1>
        <DashboardPanel
          apiBase={apiBase}
          refreshRequest={dashboardRefreshRequest}
          onSummaryChange={setDashboardSummary}
        />
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
        <section className="export-panel" aria-labelledby="export-heading">
          <div>
            <strong id="export-heading">Eksport</strong>
            <span>Raport dla wybranego miesiąca</span>
          </div>
          <div className="export-actions">
            {(['csv', 'pdf'] as const).map((format) => (
              <button
                type="button"
                key={format}
                disabled={exportingFormat !== null}
                onClick={() => { void startExport(format) }}
              >
                {exportingFormat === format ? 'Przygotowywanie…' : exportLabels[format]}
              </button>
            ))}
          </div>
          {exportError && <p role="alert">{exportError}</p>}
        </section>
        <details className="settings-panel">
          <summary>Ustawienia</summary>
          <div className="settings-content">
            <section className="settings-section application-settings" aria-labelledby="application-settings-heading">
              <h3 id="application-settings-heading">Aplikacja</h3>
              {applicationSettingsState === 'loading' && (
                <p className="settings-note">Ładowanie ustawień aplikacji…</p>
              )}
              {applicationSettingsState === 'error' && (
                <div className="settings-error" role="alert">
                  <span>Nie udało się pobrać ustawień aplikacji.</span>
                  <button type="button" onClick={() => setApplicationSettingsRetry((value) => value + 1)}>
                    Spróbuj ponownie
                  </button>
                </div>
              )}
              <form className="application-settings-form" onSubmit={saveGlobalApplicationSettings} noValidate>
                <label>
                  <span>Nazwa aplikacji</span>
                  <input
                    type="text"
                    required
                    maxLength={100}
                    value={applicationSettingsForm.application_title}
                    aria-invalid={Boolean(applicationSettingsErrors.applicationTitle)}
                    aria-describedby={applicationSettingsErrors.applicationTitle ? 'application-title-error' : undefined}
                    onChange={(event) => {
                      setApplicationSettingsForm({
                        ...applicationSettingsForm,
                        application_title: event.target.value,
                      })
                      setApplicationSettingsErrors({})
                    }}
                  />
                  {applicationSettingsErrors.applicationTitle && (
                    <small id="application-title-error" className="field-error">
                      {applicationSettingsErrors.applicationTitle}
                    </small>
                  )}
                </label>
                <div className="location-settings">
                  <h4>Lokalizacje</h4>
                  {applicationSettingsForm.locations.map((locationSetting) => (
                    <label key={locationSetting.location}>
                      <span>Nazwa wyświetlana</span>
                      <input
                        type="text"
                        maxLength={100}
                        placeholder={locationSetting.location}
                        value={locationSetting.display_name ?? ''}
                        onChange={(event) => setApplicationSettingsForm({
                          ...applicationSettingsForm,
                          locations: applicationSettingsForm.locations.map((item) => (
                            item.location === locationSetting.location
                              ? { ...item, display_name: event.target.value || null }
                              : item
                          )),
                        })}
                      />
                      <small>Identyfikator techniczny: <code>{locationSetting.location}</code></small>
                    </label>
                  ))}
                </div>
                <button type="submit" disabled={isSavingApplicationSettings || applicationSettingsState !== 'ready'}>
                  {isSavingApplicationSettings ? 'Zapisywanie…' : 'Zapisz ustawienia aplikacji'}
                </button>
                {applicationSettingsMessage && (
                  <p
                    className={`inline-message ${applicationSettingsMessage.kind}`}
                    role={applicationSettingsMessage.kind === 'error' ? 'alert' : 'status'}
                  >
                    {applicationSettingsMessage.text}
                  </p>
                )}
              </form>
            </section>
            <section className="settings-section">
              <h3>Widok</h3>
              <label className="theme-setting">
                <span>Motyw</span>
                <select
                  value={themePreference}
                  onChange={(event) => setThemePreference(event.target.value as ThemePreference)}
                >
                  <option value="auto">Auto</option>
                  <option value="light">Jasny</option>
                  <option value="dark">Ciemny</option>
                </select>
              </label>
              <label className="setting-option">
                <input
                  type="checkbox"
                  checked={showIgnoredEvents}
                  onChange={(event) => setShowIgnoredEvents(event.target.checked)}
                />
                <span>Pokaż ignorowane wydarzenia</span>
              </label>
            </section>
            <section className="settings-section pay-settings" aria-labelledby="pay-settings-heading">
              <h3 id="pay-settings-heading">Wynagrodzenie</h3>
              {payRatesState === 'loading' && <p className="settings-note">Ładowanie stawek…</p>}
              {payRatesState === 'error' && (
                <div className="settings-error" role="alert">
                  <span>Nie udało się pobrać historii stawek.</span>
                  <button type="button" onClick={() => setPayRatesRetryRequest((request) => request + 1)}>
                    Spróbuj ponownie
                  </button>
                </div>
              )}
              {payRatesState === 'ready' && latestPayRate && (
                <p className="current-rate">
                  <span>Najnowsza skonfigurowana stawka</span>
                  <strong>{formatHourlyRate(latestPayRate.hourly_rate, latestPayRate.currency)}</strong>
                </p>
              )}

              <form className="pay-rate-form" onSubmit={savePayRate} noValidate>
                <h4>Dodaj nową stawkę</h4>
                <p className="settings-note">
                  Nowa stawka obowiązuje od wybranej daty. Wcześniejsze okresy zachowują dotychczasowe stawki.
                </p>
                <div className="pay-rate-fields">
                  <label>
                    <span>Stawka godzinowa</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder="np. 55,00"
                      required
                      aria-invalid={Boolean(payRateFormErrors.hourlyRate)}
                      aria-describedby={payRateFormErrors.hourlyRate ? 'hourly-rate-error' : undefined}
                      value={payRateForm.hourlyRate}
                      onChange={(event) => {
                        setPayRateForm({ ...payRateForm, hourlyRate: event.target.value })
                        setPayRateFormErrors((errors) => ({ ...errors, hourlyRate: undefined }))
                      }}
                    />
                    {payRateFormErrors.hourlyRate && <small id="hourly-rate-error" className="field-error">{payRateFormErrors.hourlyRate}</small>}
                  </label>
                  <label>
                    <span>Data obowiązywania</span>
                    <input
                      type="date"
                      required
                      aria-invalid={Boolean(payRateFormErrors.effectiveFrom)}
                      aria-describedby={payRateFormErrors.effectiveFrom ? 'effective-date-error' : undefined}
                      value={payRateForm.effectiveFrom}
                      onChange={(event) => {
                        setPayRateForm({ ...payRateForm, effectiveFrom: event.target.value })
                        setPayRateFormErrors((errors) => ({ ...errors, effectiveFrom: undefined }))
                      }}
                    />
                    {payRateFormErrors.effectiveFrom && <small id="effective-date-error" className="field-error">{payRateFormErrors.effectiveFrom}</small>}
                  </label>
                  <label>
                    <span>Waluta</span>
                    <input
                      type="text"
                      autoComplete="off"
                      maxLength={3}
                      required
                      aria-invalid={Boolean(payRateFormErrors.currency)}
                      aria-describedby={payRateFormErrors.currency ? 'currency-error' : undefined}
                      value={payRateForm.currency}
                      onChange={(event) => {
                        setPayRateForm({ ...payRateForm, currency: event.target.value.toUpperCase() })
                        setPayRateFormErrors((errors) => ({ ...errors, currency: undefined }))
                      }}
                    />
                    {payRateFormErrors.currency && <small id="currency-error" className="field-error">{payRateFormErrors.currency}</small>}
                  </label>
                </div>
                <button type="submit" disabled={isSavingPayRate}>
                  {isSavingPayRate ? 'Zapisywanie…' : 'Dodaj stawkę'}
                </button>
                {payRateMessage && (
                  <p className={`inline-message ${payRateMessage.kind}`} role={payRateMessage.kind === 'error' ? 'alert' : 'status'}>
                    {payRateMessage.text}
                  </p>
                )}
              </form>

              {payRatesState === 'ready' && payRates.length > 0 && (
                <details className="rate-history">
                  <summary>Historia stawek ({payRates.length})</summary>
                  <ol>
                    {payRates.map((rate) => (
                      <li key={rate.id}>
                        <span>od {formatCalendarDate(rate.effective_from)}</span>
                        <strong>{formatHourlyRate(rate.hourly_rate, rate.currency)}</strong>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </section>
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
                <span>Oryginalna data i godzina: <strong>{formatEventDateTime(correctionOriginalTimestamp ?? correctionForm.event.event_timestamp, correctionAuditShowsOffsets)}</strong></span>
                {correctionForm.event.is_timestamp_corrected && (
                  <span>Aktualna korekta: <strong>{formatEventDateTime(correctionForm.event.event_timestamp, correctionAuditShowsOffsets)}</strong></span>
                )}
              </div>
            ) : (
              <p className="correction-context">Lokalizacja: <strong>{getLocationDisplayName(applicationSettings, correctionForm.location)}</strong></p>
            )}
            <label>
              <span>Data i godzina</span>
              <input
                type="datetime-local"
                min="2000-01-01T00:00"
                max="2100-12-31T23:59"
                step="60"
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
            <div className="pay-total">
              <span>Wynagrodzenie</span>
              <strong>{payState === 'ready' && paySummary ? formatMoney(paySummary.total_pay, paySummary.currency) : '—'}</strong>
              <small>{payState === 'loading' ? 'Ładowanie…' : usedRateDescription}</small>
            </div>
            <div><span>Średnio dziennie</span><strong>{formatDuration(averageDayDuration)}</strong></div>
            <div><span>Problemy</span><strong className={visibleProblemCount > 0 ? 'problem-count' : ''}>{visibleProblemCount}</strong></div>
          </section>

          {payState === 'error' && (
            <div className="pay-error" role="alert">
              <span>{payError || 'Nie udało się pobrać danych o wynagrodzeniu.'}</span>
              <button type="button" onClick={refreshPaySummary}>Spróbuj ponownie</button>
            </div>
          )}
          {payState === 'ready' && paySummary && paySummary.rates_used.length > 0 && (
            <details className="rates-used">
              <summary>Stawki użyte w miesiącu ({paySummary.rates_used.length})</summary>
              <ul>
                {paySummary.rates_used.map((rate) => (
                  <li key={rate.id}>
                    <span>od {formatCalendarDate(rate.effective_from)}</span>
                    <strong>{formatHourlyRate(rate.hourly_rate, rate.currency)}</strong>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {visibleDays.length === 0 && <p className="message">Brak zdarzeń w tym miesiącu.</p>}
          <div className="days">
            {visibleDays.map((day) => {
              const dayProblemCount = countActionableProblems(day.items, activeSessionContext)
              return (
                <details className={`day${dayProblemCount > 0 ? ' has-warning' : ''}`} key={day.date}>
                  <summary className="day-heading">
                    <div className="day-title">
                      <h3>{formatDay(day.date)}</h3>
                      {dayProblemCount > 0 && (
                        <span className="day-warning">⚠ Wymaga uwagi ({dayProblemCount})</span>
                      )}
                      <DayOverview
                        day={day}
                        settings={applicationSettings}
                        showIgnoredEvents={showIgnoredEvents}
                        activeSessionContext={activeSessionContext}
                      />
                    </div>
                    <div className="day-totals">
                      <span>Czas: <strong>{formatDuration(day.total_duration_seconds)}</strong></span>
                      <span>
                        Wynagrodzenie:{' '}
                        <strong>
                          {payState === 'ready' && paySummary
                            ? formatMoney(findDailyPay(paySummary.days, day.date), paySummary.currency)
                            : '—'}
                        </strong>
                      </span>
                      <small className="disclosure-label">Szczegóły</small>
                    </div>
                  </summary>
                  <div className="day-details">
                    <div className="sessions">
                      {day.items.map((item) => (
                        <SessionItem
                          key={`${item.status}-${item.events.map((event) => event.id).join('-')}`}
                          item={item}
                          actions={eventActions}
                          locationName={getLocationDisplayName(applicationSettings, item.location)}
                          activeSessionContext={activeSessionContext}
                        />
                      ))}
                      {day.ignored_events
                        .filter((event) => isEventVisible(event, showIgnoredEvents))
                        .map((event) => (
                          <article className="session ignored-session" key={`ignored-${event.id}`}>
                            <div className="session-details">
                              <EventRow event={event} actions={eventActions} />
                              <span className="location">{getLocationDisplayName(applicationSettings, event.location)}</span>
                            </div>
                          </article>
                        ))}
                    </div>
                  </div>
                </details>
              )
            })}
          </div>
        </>}
      </section>
    </main>
  )
}

export default App

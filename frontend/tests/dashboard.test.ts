import {
  DASHBOARD_POLL_INTERVAL_MS,
  LIVE_TIMER_INTERVAL_MS,
  advanceElapsedSeconds,
  advanceTodayEffectiveDuration,
  dashboardStatusPresentation,
  formatDashboardDuration,
  formatLiveTimer,
  loadDashboard,
} from '../src/dashboard.js'
import type { FetchLike } from '../src/pay.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(dashboardStatusPresentation.working.label, 'W PRACY', 'working status is presented in Polish')
assertEqual(dashboardStatusPresentation.outside.label, 'POZA PRACĄ', 'outside status is presented in Polish')
assertEqual(
  dashboardStatusPresentation.ambiguous.label,
  'STATUS NIEJEDNOZNACZNY',
  'ambiguous status does not guess that a shift is running',
)
assertEqual(
  advanceTodayEffectiveDuration(
    {
      date: '2026-09-07',
      completed_duration_seconds: 14400,
      running_duration_seconds: 3600,
      effective_duration_seconds: 18000,
    },
    3600,
    3610,
  ),
  18010,
  'today effective duration advances with a same-day running shift',
)
assertEqual(
  advanceTodayEffectiveDuration(
    {
      date: '2026-09-07',
      completed_duration_seconds: 14400,
      running_duration_seconds: null,
      effective_duration_seconds: 14400,
    },
    3600,
    3610,
  ),
  14400,
  'a cross-midnight running shift is not reassigned to today',
)

assertEqual(formatLiveTimer(0), '00:00', 'zero timer is formatted without seconds')
assertEqual(formatLiveTimer(2 * 3600 + 14 * 60 + 59), '02:14', 'live timer shows completed minutes only')
assertEqual(formatDashboardDuration(8 * 3600 + 18 * 60), '8 godz. 18 min', 'dashboard duration is readable')
assertEqual(formatDashboardDuration(59), '0 min', 'subminute dashboard duration does not expose seconds')
assertEqual(formatDashboardDuration(119), '1 min', 'dashboard duration does not round up')
assertEqual(
  advanceElapsedSeconds(3600, 1_000_000, 1_005_999),
  3605,
  'local timer advances from the backend baseline without another API response',
)
assertEqual(DASHBOARD_POLL_INTERVAL_MS, 30_000, 'dashboard uses modest 30-second polling')
assertEqual(LIVE_TIMER_INTERVAL_MS, 1_000, 'internal live elapsed time remains second-precise')

let requestCount = 0
let requestedUrl = ''
let receivedSignal: AbortSignal | null | undefined
const controller = new AbortController()
const fetcher: FetchLike = async (url, init) => {
  requestCount += 1
  requestedUrl = url
  receivedSignal = init?.signal
  return {
    ok: true,
    status: 200,
    json: async () => ({
      status: 'working',
      generated_at: '2026-09-07T12:00:00Z',
      current_session: {
        entry_timestamp: '2026-09-07T13:00:00+02:00',
        entry_timestamp_utc: '2026-09-07T11:00:00Z',
        elapsed_seconds: 3600,
      },
      today: {
        date: '2026-09-07',
        completed_duration_seconds: 14400,
        running_duration_seconds: 3600,
        effective_duration_seconds: 18000,
      },
      month: {
        year: 2026,
        month: 9,
        completed_duration_seconds: 14400,
        work_days: 1,
        pay: '200.00',
        currency: 'PLN',
      },
    }),
  }
}

const loaded = await loadDashboard(fetcher, '/local', 'Europe/Warsaw', controller.signal)
assertEqual(requestedUrl, '/local/api/dashboard?timezone=Europe%2FWarsaw', 'dashboard sends the browser timezone')
assertEqual(receivedSignal === controller.signal, true, 'dashboard request receives stale-request cancellation')
assertEqual(loaded.status, 'working', 'typed dashboard response is returned')
advanceElapsedSeconds(loaded.current_session?.elapsed_seconds ?? 0, 1_000_000, 1_010_000)
assertEqual(requestCount, 1, 'advancing the live timer does not poll the API every second')

const workAndPayState = { work: { total: 14400 }, pay: { total: '200.00' } }
const failedFetcher: FetchLike = async () => ({ ok: false, status: 503, json: async () => ({}) })
let dashboardFailed = false
try {
  await loadDashboard(failedFetcher, '', 'Europe/Warsaw')
} catch {
  dashboardFailed = true
}
assertEqual(dashboardFailed, true, 'dashboard API failure is isolated')
assertEqual(workAndPayState, { work: { total: 14400 }, pay: { total: '200.00' } }, 'dashboard failure preserves work and pay data')

console.log('Dashboard helper tests passed.')

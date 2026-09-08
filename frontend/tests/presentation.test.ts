import {
  compactTimestampOffset,
  countActionableProblems,
  dayOverviewPresentation,
  formatDuration,
  formatEventDateTime,
  formatEventTime,
  formatSessionRange,
  isActionableProblem,
  isPendingCurrentSession,
  timestampOffset,
  type ActiveSessionContext,
  type WorkItemPresentation,
} from '../src/presentation.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(formatDuration(8 * 3600 + 12 * 60 + 59), '8h 12m', 'history duration hides seconds')
assertEqual(formatDuration(59), '0m', 'subminute history duration does not round up')
assertEqual(formatDuration(119), '1m', 'history duration shows completed minutes only')
assertEqual(formatDuration(null), '—', 'missing duration remains distinguishable')
assertEqual(timestampOffset('2026-09-01T19:22:25+02:00'), '+02:00', 'ordinary offset is extracted')
assertEqual(timestampOffset('2026-09-01T17:22:25Z'), '+00:00', 'UTC designator is normalized')
assertEqual(
  timestampOffset('2026-09-01T19:22:25+05:30:45'),
  '+05:30:45',
  'positive offset seconds remain available for comparison',
)
assertEqual(
  timestampOffset('2026-09-01T19:22:25-03:12:30'),
  '-03:12:30',
  'negative offset seconds remain available for comparison',
)
assertEqual(
  timestampOffset('2026-09-01T19:22:25+05:30:45.125'),
  '+05:30:45.125',
  'fractional offset seconds remain available for comparison',
)
assertEqual(
  compactTimestampOffset('2026-09-01T19:22:25+05:30:45.125'),
  '+05:30',
  'display offset remains minute-precision',
)
assertEqual(
  formatEventTime('2026-09-01T19:22:25+02:00'),
  '19:22',
  'ordinary history timestamp hides seconds and its redundant offset',
)
assertEqual(
  formatEventDateTime('2026-09-02T06:15:51+02:00'),
  '2026-09-02 06:15',
  'ordinary correction audit keeps the date but hides seconds and offset',
)
assertEqual(
  formatEventDateTime('2026-10-25T02:30:45+01:00', true),
  '2026-10-25 02:30 +01:00',
  'an ambiguous correction audit can retain its compact explicit offset',
)
assertEqual(
  formatSessionRange(
    '2026-10-25T02:30:15+02:00',
    '2026-10-25T02:30:45+01:00',
  ),
  '02:30 +02:00 → 02:30 +01:00',
  'DST session keeps compact offsets without seconds or UTC prefixes',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T22:00:37+02:00',
    '2026-09-02T06:15:51+02:00',
  ),
  '22:00 → 02.09 06:15',
  'cross-midnight session makes the exit date explicit',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T08:00:32+02:00',
    '2026-09-01T20:44:51+02:00',
  ),
  '08:00 → 20:44',
  'ordinary session remains compact',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T08:00:32+05:30:45',
    '2026-09-01T20:44:51+05:30:45',
  ),
  '08:00 → 20:44',
  'identical source offsets remain hidden',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T08:00:32+02:00',
    '2026-09-01T20:44:51+01:00',
  ),
  '08:00 +02:00 → 20:44 +01:00',
  'different ordinary offsets remain visible and compact',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T08:00:32+05:30:15',
    '2026-09-01T20:44:51+05:30:45',
  ),
  '08:00 +05:30 → 20:44 +05:30',
  'offsets differing only in seconds still trigger offset-changing presentation',
)

const inactiveContext: ActiveSessionContext = {
  dashboardStatus: 'outside',
  currentEntryTimestampUtc: null,
  runningToday: false,
}
const activeContext: ActiveSessionContext = {
  dashboardStatus: 'working',
  currentEntryTimestampUtc: '2026-09-08T06:30:00Z',
  runningToday: true,
}
const activeMissingExit: WorkItemPresentation = {
  status: 'missing_exit',
  events: [{ event_type: 'entry', event_timestamp_utc: '2026-09-08T06:30:00Z' }],
}

assertEqual(
  dayOverviewPresentation({ status: 'valid', events: [] }, inactiveContext),
  { showRange: true, showDuration: true, showWarning: false, statusLabel: null },
  'valid overview presents its complete interval without a warning',
)
assertEqual(
  dayOverviewPresentation({ status: 'unusually_long_session', events: [] }, inactiveContext),
  {
    showRange: true,
    showDuration: true,
    showWarning: true,
    statusLabel: 'Podejrzanie długa sesja',
  },
  'long-session overview keeps its interval, duration, and warning',
)
assertEqual(
  formatSessionRange(
    '2026-09-01T08:00:00+02:00',
    '2026-09-02T01:30:00+02:00',
  ),
  '08:00 → 02.09 01:30',
  'long-session overview can show its complete cross-day range',
)
assertEqual(formatDuration(17 * 3600 + 30 * 60), '17h 30m', 'long-session overview shows duration')
for (const status of [
  'duplicate_entry',
  'orphan_exit',
  'ambiguous_timestamp',
] as const) {
  assertEqual(
    dayOverviewPresentation({ status, events: [] }, inactiveContext),
    {
      showRange: false,
      showDuration: false,
      showWarning: true,
      statusLabel: status === 'duplicate_entry'
        ? 'Niejednoznaczne wejście'
        : status === 'orphan_exit'
          ? 'Wyjście bez wejścia'
          : 'Sprzeczne zdarzenia o tej samej godzinie',
    },
    `${status} overview does not invent a complete interval`,
  )
}

assertEqual(
  dayOverviewPresentation(activeMissingExit, activeContext),
  {
    showRange: true,
    showDuration: false,
    showWarning: false,
    statusLabel: 'Trwająca zmiana',
  },
  'the server-confirmed active missing exit is presented without invented duration',
)
assertEqual(isPendingCurrentSession(activeMissingExit, activeContext), true, 'exact active session is pending')
assertEqual(isActionableProblem(activeMissingExit, activeContext), false, 'exact active session is not actionable')

const equivalentOffsetItem: WorkItemPresentation = {
  status: 'missing_exit',
  events: [{ event_type: 'entry', event_timestamp_utc: '2026-09-08T08:30:00+02:00' }],
}
assertEqual(
  isPendingCurrentSession(equivalentOffsetItem, activeContext),
  true,
  'equivalent ISO forms are compared as the same UTC instant',
)

const differentEntryItem: WorkItemPresentation = {
  status: 'missing_exit',
  events: [{ event_type: 'entry', event_timestamp_utc: '2026-09-08T07:30:00Z' }],
}
assertEqual(
  dayOverviewPresentation(differentEntryItem, activeContext),
  {
    showRange: false,
    showDuration: false,
    showWarning: true,
    statusLabel: 'Brak wyjścia',
  },
  'a nonmatching missing exit remains an actionable warning',
)
assertEqual(isActionableProblem(differentEntryItem, activeContext), true, 'different entry is actionable')

const ambiguousContext: ActiveSessionContext = {
  dashboardStatus: 'ambiguous',
  currentEntryTimestampUtc: null,
  runningToday: false,
}
assertEqual(
  countActionableProblems([activeMissingExit, differentEntryItem], ambiguousContext),
  2,
  'an ambiguous dashboard keeps both open entries actionable',
)
assertEqual(
  isActionableProblem(activeMissingExit, inactiveContext),
  true,
  'an outside dashboard keeps a missing exit actionable',
)

const orphanExit: WorkItemPresentation = { status: 'orphan_exit', events: [] }
assertEqual(
  countActionableProblems([activeMissingExit, orphanExit], activeContext),
  1,
  'only the matching active item is excluded from problem counts',
)
assertEqual(
  countActionableProblems([activeMissingExit, differentEntryItem], activeContext),
  1,
  'a second nonmatching missing exit stays actionable',
)

const noRunningTodayContext: ActiveSessionContext = {
  ...activeContext,
  runningToday: false,
}
assertEqual(
  isPendingCurrentSession(activeMissingExit, noRunningTodayContext),
  false,
  'missing running-today confirmation fails safe after dashboard day rollover',
)

const invalidTimestampItem: WorkItemPresentation = {
  status: 'missing_exit',
  events: [{ event_type: 'entry', event_timestamp_utc: 'not-a-timestamp' }],
}
assertEqual(
  isPendingCurrentSession(invalidTimestampItem, activeContext),
  false,
  'an invalid entry timestamp fails safe without throwing',
)

const timezoneMismatchItem = {
  ...activeMissingExit,
  local_date: '2026-09-09',
}
assertEqual(
  isPendingCurrentSession(timezoneMismatchItem, activeContext),
  true,
  'classification follows dashboard UTC identity rather than item local date',
)

assertEqual(
  isPendingCurrentSession(
    {
      status: 'missing_exit',
      events: [
        { event_type: 'entry', event_timestamp_utc: '2026-09-08T06:30:00Z' },
        { event_type: 'entry', event_timestamp_utc: '2026-09-08T06:31:00Z' },
      ],
    },
    activeContext,
  ),
  false,
  'an item with multiple entries is never treated as the active session',
)
assertEqual(
  isPendingCurrentSession(
    {
      status: 'missing_exit',
      events: [
        { event_type: 'entry', event_timestamp_utc: '2026-09-08T06:30:00Z' },
        { event_type: 'exit', event_timestamp_utc: '2026-09-08T07:30:00Z' },
      ],
    },
    activeContext,
  ),
  false,
  'an item containing an exit is never treated as the active session',
)
for (const status of [
  'duplicate_entry',
  'orphan_exit',
  'ambiguous_timestamp',
  'unusually_long_session',
] as const) {
  assertEqual(
    isActionableProblem({ status, events: [] }, activeContext),
    true,
    `${status} remains actionable while another session is active`,
  )
}

console.log('Presentation helper tests passed.')

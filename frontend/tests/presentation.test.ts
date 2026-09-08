import { formatDuration, formatEventDateTime, formatEventTime } from '../src/presentation.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(formatDuration(8 * 3600 + 12 * 60 + 59), '8h 12m', 'history duration hides seconds')
assertEqual(formatDuration(59), '0m', 'subminute history duration does not round up')
assertEqual(formatDuration(119), '1m', 'history duration shows completed minutes only')
assertEqual(formatDuration(null), '—', 'missing duration remains distinguishable')
assertEqual(
  formatEventTime('2026-09-01T19:22:25+02:00'),
  '19:22 UTC+02:00',
  'history timestamp hides seconds and keeps its offset',
)
assertEqual(
  formatEventDateTime('2026-09-02T06:15:51+02:00'),
  '2026-09-02 06:15 UTC+02:00',
  'correction audit timestamp keeps the date but hides seconds',
)
assertEqual(
  formatEventTime('2026-09-01T19:22:25+05:30:45'),
  '19:22 UTC+05:30',
  'a subminute source offset cannot expose seconds in presentation',
)

console.log('Presentation helper tests passed.')

import {
  formatDuration,
  formatEventDateTime,
  formatEventTime,
  formatSessionRange,
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

console.log('Presentation helper tests passed.')

import {
  extractOffsetFromIso,
  formatLocalDateTimeWithOffset,
  getPossibleOffsetsForLocalDateTime,
  localDateTimeToOffsetIso,
  resolveCorrectionTimestamp,
  timestampToLocalInput,
} from '../src/corrections.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(
  formatLocalDateTimeWithOffset('2026-09-07T08:00', -120),
  '2026-09-07T08:00:00+02:00',
  'formats a positive local UTC offset',
)
assertEqual(
  formatLocalDateTimeWithOffset('2026-09-07T08:00:30', 330),
  '2026-09-07T08:00:30-05:30',
  'formats a negative local UTC offset',
)
const localDate = new Date(2026, 8, 7, 8, 7, 32)
const localSourceTimestamp = formatLocalDateTimeWithOffset(
  '2026-09-07T08:07:32',
  localDate.getTimezoneOffset(),
)
assertEqual(timestampToLocalInput(localSourceTimestamp ?? ''), '2026-09-07T08:07:32', 'uses browser-local input fields')
assertEqual(formatLocalDateTimeWithOffset('2026-02-31T08:00', -60), null, 'rejects impossible dates')
assertEqual(localDateTimeToOffsetIso('not-a-date'), null, 'rejects malformed local timestamps')

const localTimestamp = localDateTimeToOffsetIso('2026-09-07T08:00:00')
assertEqual(localTimestamp, '2026-09-07T08:00:00+02:00', 'normal local time uses its only valid offset')
assertEqual(extractOffsetFromIso('2026-10-25T02:10:00+01:00'), '+01:00', 'extracts an explicit positive offset')
assertEqual(extractOffsetFromIso('2026-10-25T01:10:00Z'), '+00:00', 'normalizes the UTC designator')

const winterTimestamp = '2026-10-25T02:10:00+01:00'
const summerTimestamp = '2026-10-25T02:10:00+02:00'
const repeatedLocalTime = '2026-10-25T02:10:00'
assertEqual(timestampToLocalInput(winterTimestamp), repeatedLocalTime, 'shows the winter occurrence as local wall time')
assertEqual(timestampToLocalInput(summerTimestamp), repeatedLocalTime, 'shows the summer occurrence as local wall time')

const preservedWinterTimestamp = resolveCorrectionTimestamp(repeatedLocalTime, winterTimestamp, repeatedLocalTime)
assertEqual(preservedWinterTimestamp, winterTimestamp, 'preserves +01:00 when an edited timestamp is unchanged')
assertEqual(
  new Date(preservedWinterTimestamp ?? '').getTime(),
  new Date(winterTimestamp).getTime(),
  'preserving +01:00 also preserves the UTC instant',
)
const preservedSummerTimestamp = resolveCorrectionTimestamp(repeatedLocalTime, summerTimestamp, repeatedLocalTime)
assertEqual(preservedSummerTimestamp, summerTimestamp, 'preserves +02:00 when an edited timestamp is unchanged')
assertEqual(
  new Date(preservedSummerTimestamp ?? '').getTime(),
  new Date(summerTimestamp).getTime(),
  'preserving +02:00 also preserves the UTC instant',
)

assertEqual(
  getPossibleOffsetsForLocalDateTime(repeatedLocalTime),
  ['+02:00', '+01:00'],
  'returns both fallback offsets in occurrence order',
)
assertEqual(
  localDateTimeToOffsetIso(repeatedLocalTime, '+01:00'),
  winterTimestamp,
  'emits the selected winter occurrence',
)
assertEqual(
  localDateTimeToOffsetIso(repeatedLocalTime, '+02:00'),
  summerTimestamp,
  'emits the selected summer occurrence',
)
assertEqual(
  resolveCorrectionTimestamp(repeatedLocalTime, winterTimestamp, repeatedLocalTime, '+02:00'),
  summerTimestamp,
  'allows an explicit occurrence change while editing an ambiguous timestamp',
)
assertEqual(
  localDateTimeToOffsetIso(repeatedLocalTime),
  null,
  'does not guess an occurrence for an ambiguous local time',
)
assertEqual(
  localDateTimeToOffsetIso('2026-10-25T04:00:00'),
  '2026-10-25T04:00:00+01:00',
  'uses the new local offset after the fallback transition',
)
assertEqual(
  getPossibleOffsetsForLocalDateTime('2026-03-29T02:30:00'),
  [],
  'recognizes a spring-forward gap as an impossible local time',
)
assertEqual(
  localDateTimeToOffsetIso('2026-03-29T02:30:00'),
  null,
  'rejects an impossible spring-forward local time',
)

console.log('Manual correction helper tests passed.')

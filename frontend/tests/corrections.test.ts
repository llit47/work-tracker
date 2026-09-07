import { formatLocalDateTimeWithOffset, localDateTimeToOffsetIso, timestampToLocalInput } from '../src/corrections.js'

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
if (!localTimestamp || !/[+-]\d{2}:\d{2}$/.test(localTimestamp)) {
  throw new Error('browser-local conversion must include an explicit UTC offset')
}

console.log('Manual correction helper tests passed.')

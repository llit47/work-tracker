import {
  MAX_YEAR,
  MIN_YEAR,
  currentLocalDate,
  currentMonth,
  isSameMonth,
  millisecondsUntilNextLocalDay,
  monthInputValue,
  monthSearch,
  parseMonthInput,
  readMonthFromSearch,
  shiftMonth,
  type MonthSelection,
} from '../src/monthNavigation.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

const fallback: MonthSelection = { year: 2026, month: 9 }

assertEqual(currentMonth(new Date(2026, 8, 6)), fallback, 'uses the local current month')
assertEqual(
  currentLocalDate(new Date(2026, 8, 8, 23, 59)),
  '2026-09-08',
  'formats the browser-local calendar date',
)
assertEqual(
  currentLocalDate(new Date(2026, 8, 9, 0, 0)),
  '2026-09-09',
  'reads the next local date after midnight',
)
assertEqual(
  currentMonth(new Date(2026, 9, 1, 0, 0)),
  { year: 2026, month: 10 },
  'reads the new local month after midnight',
)
assertEqual(
  millisecondsUntilNextLocalDay(new Date(2026, 8, 30, 23, 59, 59, 500)),
  500,
  'schedules refresh at the next local midnight',
)
assertEqual(readMonthFromSearch('', fallback), { selection: fallback, shouldNormalize: false }, 'defaults to current month')
assertEqual(
  readMonthFromSearch('?year=2025&month=12', fallback),
  { selection: { year: 2025, month: 12 }, shouldNormalize: false },
  'reads a valid URL selection',
)
assertEqual(shiftMonth({ year: 2026, month: 9 }, -1), { year: 2026, month: 8 }, 'moves to previous month')
assertEqual(shiftMonth({ year: 2026, month: 9 }, 1), { year: 2026, month: 10 }, 'moves to next month')
assertEqual(shiftMonth({ year: 2026, month: 12 }, 1), { year: 2027, month: 1 }, 'moves from December to January')
assertEqual(shiftMonth({ year: 2026, month: 1 }, -1), { year: 2025, month: 12 }, 'moves from January to December')
assertEqual(shiftMonth({ year: MIN_YEAR, month: 1 }, -1), { year: MIN_YEAR, month: 1 }, 'does not leave API range')
assertEqual(shiftMonth({ year: MAX_YEAR, month: 12 }, 1), { year: MAX_YEAR, month: 12 }, 'does not leave API range')
assertEqual(parseMonthInput('2026-08'), { year: 2026, month: 8 }, 'parses direct month input')
assertEqual(parseMonthInput('1999-12'), null, 'rejects direct input before API range')
assertEqual(monthInputValue({ year: 2026, month: 8 }), '2026-08', 'formats native month input')
assertEqual(monthSearch({ year: 2026, month: 8 }), '?year=2026&month=8', 'formats shareable URL query')
assertEqual(isSameMonth(fallback, { year: 2026, month: 9 }), true, 'identifies current selection')

for (const search of [
  '?year=abc&month=9',
  '?year=2026&month=99',
  '?year=1999&month=1',
  '?year=2101&month=1',
  '?year=2026',
  '?month=9',
]) {
  assertEqual(
    readMonthFromSearch(search, fallback),
    { selection: fallback, shouldNormalize: true },
    `normalizes invalid query ${search}`,
  )
}

console.log('Month navigation tests passed.')

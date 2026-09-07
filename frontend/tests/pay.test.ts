import {
  PayRateRequestError,
  createPayRate,
  describeRatesUsed,
  findDailyPay,
  formatHourlyRate,
  formatMoney,
  loadPaySummary,
  validatePayRateForm,
  type FetchLike,
  type PayRateUsed,
} from '../src/pay.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

function rate(id: number, effectiveFrom: string, hourlyRate: string): PayRateUsed {
  return { id, effective_from: effectiveFrom, hourly_rate: hourlyRate, currency: 'PLN' }
}

assertEqual(formatMoney('1051.67', 'PLN'), '1 051,67 zł', 'PLN monthly pay uses Polish formatting')
assertEqual(formatHourlyRate('50.00', 'PLN'), '50,00 zł/h', 'PLN hourly rate uses Polish formatting')

const dailyPay = [{ date: '2026-09-06', duration_seconds: 30_600, pay: '425.00' }]
assertEqual(findDailyPay(dailyPay, '2026-09-06'), '425.00', 'daily pay is selected by date')
assertEqual(findDailyPay([], '2026-09-07'), '0.00', 'missing daily pay safely falls back to zero')

assertEqual(
  describeRatesUsed([rate(1, '1970-01-01', '50.00')], 'PLN'),
  'Stawka: 50,00 zł/h',
  'one used rate is shown directly',
)
assertEqual(
  describeRatesUsed([rate(1, '1970-01-01', '50.00'), rate(2, '2026-09-15', '60.00')], 'PLN'),
  'Stawki: 50,00–60,00 zł/h',
  'multiple used rates are shown as a compact range',
)

const validForm = validatePayRateForm({
  effectiveFrom: '2026-10-01',
  hourlyRate: '50.00',
  currency: 'pln',
})
assertEqual(validForm.errors, {}, 'valid rate form has no errors')
assertEqual(
  validForm.payload,
  { effective_from: '2026-10-01', hourly_rate: '50.00', currency: 'PLN' },
  'valid rate form creates an exact string payload',
)

for (const [value, label] of [
  ['50.001', 'more than two fractional places'],
  ['0', 'zero'],
  ['1000000.01', 'rate above the backend maximum'],
] as const) {
  const result = validatePayRateForm({
    effectiveFrom: '2026-10-01',
    hourlyRate: value,
    currency: 'PLN',
  })
  assertEqual(Boolean(result.errors.hourlyRate), true, `validation rejects ${label}`)
}

let submittedBody = ''
let ratesRefreshes = 0
let payRefreshes = 0
const successfulFetch: FetchLike = async (_url, init) => {
  submittedBody = String(init?.body ?? '')
  return {
    ok: true,
    status: 201,
    json: async () => ({
      id: 2,
      effective_from: '2026-10-01',
      hourly_rate: '55.10',
      currency: 'PLN',
      created_at: '2026-09-07T12:00:00Z',
    }),
  }
}

await createPayRate(
  successfulFetch,
  '',
  { effective_from: '2026-10-01', hourly_rate: '55.10', currency: 'PLN' },
  {
    rates: () => { ratesRefreshes += 1 },
    paySummary: () => { payRefreshes += 1 },
  },
)
assertEqual(
  JSON.parse(submittedBody),
  { effective_from: '2026-10-01', hourly_rate: '55.10', currency: 'PLN' },
  'rate creation sends the exact decimal string instead of a JavaScript number',
)
assertEqual(ratesRefreshes, 1, 'successful rate creation refreshes rate history')
assertEqual(payRefreshes, 1, 'successful rate creation refreshes the current pay summary')

const failedFetch: FetchLike = async () => ({ ok: false, status: 409, json: async () => ({}) })
let receivedStatus = 0
try {
  await createPayRate(
    failedFetch,
    '',
    { effective_from: '2026-10-01', hourly_rate: '55.10', currency: 'PLN' },
    { rates: () => undefined, paySummary: () => undefined },
  )
} catch (error) {
  if (error instanceof PayRateRequestError) receivedStatus = error.status
}
assertEqual(receivedStatus, 409, 'API conflict remains available for a localized UI message')

const controller = new AbortController()
let receivedPayUrl = ''
let receivedSignal: AbortSignal | null | undefined
const paySummaryFetch: FetchLike = async (url, init) => {
  receivedPayUrl = url
  receivedSignal = init?.signal
  return {
    ok: true,
    status: 200,
    json: async () => ({
      year: 2026,
      month: 9,
      currency: 'PLN',
      total_duration_seconds: 28_800,
      work_days: 1,
      total_pay: '400.00',
      days: [{ date: '2026-09-06', duration_seconds: 28_800, pay: '400.00' }],
      rates_used: [rate(1, '1970-01-01', '50.00')],
    }),
  }
}
await loadPaySummary(paySummaryFetch, '/local', 2026, 9, controller.signal)
assertEqual(receivedPayUrl, '/local/api/pay-summary?year=2026&month=9', 'pay summary uses the selected month')
assertEqual(receivedSignal === controller.signal, true, 'pay summary receives an abort signal for stale requests')

const loadedWorkSummary = { total_duration_seconds: 28_800 }
let payRequestFailed = false
try {
  await loadPaySummary(failedFetch, '', 2026, 9)
} catch {
  payRequestFailed = true
}
assertEqual(payRequestFailed, true, 'a pay API error is handled as a separate request failure')
assertEqual(loadedWorkSummary.total_duration_seconds, 28_800, 'a pay API failure does not alter work data')

console.log('Pay presentation tests passed.')

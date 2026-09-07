export type PayRate = {
  id: number
  effective_from: string
  hourly_rate: string
  currency: string
  created_at: string
}

export type PayRateUsed = {
  id: number
  effective_from: string
  hourly_rate: string
  currency: string
}

export type DailyPaySummary = {
  date: string
  duration_seconds: number
  pay: string
}

export type MonthlyPaySummary = {
  year: number
  month: number
  currency: string
  total_duration_seconds: number
  work_days: number
  total_pay: string
  days: DailyPaySummary[]
  rates_used: PayRateUsed[]
}

export type PayRatePayload = {
  effective_from: string
  hourly_rate: string
  currency: string
}

export type PayRateFormValues = {
  effectiveFrom: string
  hourlyRate: string
  currency: string
}

export type PayRateFormErrors = Partial<Record<keyof PayRateFormValues, string>>

type FetchResponse = {
  ok: boolean
  status: number
  json: () => Promise<unknown>
}

export type FetchLike = (url: string, init?: RequestInit) => Promise<FetchResponse>

export class PayRateRequestError extends Error {
  status: number

  constructor(status: number) {
    super(`Pay-rate request failed with status ${status}`)
    this.name = 'PayRateRequestError'
    this.status = status
  }
}

export class PaySummaryRequestError extends Error {
  status: number

  constructor(status: number) {
    super(`Pay-summary request failed with status ${status}`)
    this.name = 'PaySummaryRequestError'
    this.status = status
  }
}

const MAX_RATE_CENTS = 100_000_000n

function normalizeSpaces(value: string): string {
  return value.replace(/[\u00a0\u202f]/g, ' ')
}

function formatPolishDecimal(amount: string): string | null {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(amount)
  if (!match) return null
  const integer = match[1].replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  const fraction = (match[2] ?? '').padEnd(2, '0').slice(0, 2)
  return `${integer},${fraction}`
}

export function formatMoney(amount: string, currency: string): string {
  if (currency === 'PLN') {
    const formattedAmount = formatPolishDecimal(amount)
    if (formattedAmount) return `${formattedAmount} zł`
  }

  const numericAmount = Number(amount)
  if (!Number.isFinite(numericAmount)) return `${amount} ${currency}`

  try {
    return normalizeSpaces(new Intl.NumberFormat('pl-PL', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(numericAmount))
  } catch {
    return `${amount} ${currency}`
  }
}

export function formatHourlyRate(amount: string, currency: string): string {
  return `${formatMoney(amount, currency)}/h`
}

export function findDailyPay(days: readonly DailyPaySummary[], date: string): string {
  return days.find((day) => day.date === date)?.pay ?? '0.00'
}

export function describeRatesUsed(rates: readonly PayRateUsed[], currency: string): string {
  if (rates.length === 0) return 'Brak wykorzystanej stawki'
  if (rates.length === 1) return `Stawka: ${formatHourlyRate(rates[0].hourly_rate, rates[0].currency)}`

  const sameCurrency = rates.every((rate) => rate.currency === currency)
  const amounts = rates.map((rate) => Number(rate.hourly_rate))
  if (!sameCurrency || currency !== 'PLN' || amounts.some((amount) => !Number.isFinite(amount))) {
    return `Użyto ${rates.length} stawek`
  }

  const minimum = Math.min(...amounts).toFixed(2)
  const maximum = Math.max(...amounts).toFixed(2)
  const minimumFormatted = formatMoney(minimum, currency).replace(/\s?[A-Z]{3}$|\s?zł$/, '')
  const maximumFormatted = formatMoney(maximum, currency)
  return `Stawki: ${minimumFormatted}–${maximumFormatted}/h`
}

function isValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function validatePayRateForm(values: PayRateFormValues): {
  errors: PayRateFormErrors
  payload: PayRatePayload | null
} {
  const errors: PayRateFormErrors = {}
  const rate = values.hourlyRate.trim().replace(',', '.')
  const rateMatch = /^(\d+)(?:\.(\d{1,2}))?$/.exec(rate)

  if (!rate) {
    errors.hourlyRate = 'Podaj stawkę godzinową.'
  } else if (!rateMatch) {
    errors.hourlyRate = 'Podaj dodatnią stawkę z maksymalnie dwoma miejscami po przecinku.'
  } else {
    const wholeCents = BigInt(rateMatch[1]) * 100n
    const fractionalCents = BigInt((rateMatch[2] ?? '').padEnd(2, '0') || '0')
    const totalCents = wholeCents + fractionalCents
    if (totalCents <= 0n) errors.hourlyRate = 'Stawka musi być większa od zera.'
    else if (totalCents > MAX_RATE_CENTS) errors.hourlyRate = 'Stawka nie może przekraczać 1 000 000,00.'
  }

  const effectiveFrom = values.effectiveFrom.trim()
  if (!isValidDate(effectiveFrom)) errors.effectiveFrom = 'Podaj prawidłową datę obowiązywania.'

  const currency = values.currency.trim().toUpperCase()
  if (!/^[A-Z]{3}$/.test(currency)) errors.currency = 'Waluta musi zawierać trzy litery.'

  if (Object.keys(errors).length > 0 || !rateMatch) return { errors, payload: null }

  return {
    errors,
    payload: {
      effective_from: effectiveFrom,
      hourly_rate: `${rateMatch[1]}.${(rateMatch[2] ?? '').padEnd(2, '0')}`,
      currency,
    },
  }
}

export async function createPayRate(
  fetcher: FetchLike,
  apiBase: string,
  payload: PayRatePayload,
  refresh: { rates: () => void; paySummary: () => void },
): Promise<PayRate> {
  const response = await fetcher(`${apiBase}/api/pay-rates`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new PayRateRequestError(response.status)

  const createdRate = await response.json() as PayRate
  refresh.rates()
  refresh.paySummary()
  return createdRate
}

export async function loadPaySummary(
  fetcher: FetchLike,
  apiBase: string,
  year: number,
  month: number,
  signal?: AbortSignal,
): Promise<MonthlyPaySummary> {
  const response = await fetcher(`${apiBase}/api/pay-summary?year=${year}&month=${month}`, { signal })
  if (!response.ok) throw new PaySummaryRequestError(response.status)
  return await response.json() as MonthlyPaySummary
}

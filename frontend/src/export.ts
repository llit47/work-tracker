export type ExportFormat = 'csv' | 'pdf'

export const exportLabels: Record<ExportFormat, string> = {
  csv: 'Pobierz CSV',
  pdf: 'Pobierz PDF',
}

type ExportResponse = {
  ok: boolean
  status: number
  blob: () => Promise<Blob>
}

export type ExportFetch = (
  url: string,
  init?: RequestInit,
) => Promise<ExportResponse>

export class ExportRequestError extends Error {
  status: number

  constructor(status: number) {
    super(`Export request failed with status ${status}`)
    this.name = 'ExportRequestError'
    this.status = status
  }
}

export function monthlyExportUrl(
  apiBase: string,
  format: ExportFormat,
  year: number,
  month: number,
): string {
  return `${apiBase}/api/export/monthly.${format}?year=${year}&month=${month}`
}

export function monthlyExportFilename(
  format: ExportFormat,
  year: number,
  month: number,
): string {
  return `work-tracker-${year}-${String(month).padStart(2, '0')}.${format}`
}

export async function downloadMonthlyExport(
  fetcher: ExportFetch,
  apiBase: string,
  format: ExportFormat,
  year: number,
  month: number,
): Promise<Blob> {
  const response = await fetcher(monthlyExportUrl(apiBase, format, year, month))
  if (!response.ok) throw new ExportRequestError(response.status)
  return response.blob()
}

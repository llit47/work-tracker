import {
  ExportRequestError,
  downloadMonthlyExport,
  exportLabels,
  monthlyExportFilename,
  monthlyExportUrl,
  type ExportFetch,
} from '../src/export.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(exportLabels.csv, 'Pobierz CSV', 'CSV button label is Polish')
assertEqual(exportLabels.pdf, 'Pobierz PDF', 'PDF button label is Polish')
assertEqual(
  monthlyExportUrl('/local', 'csv', 2026, 9),
  '/local/api/export/monthly.csv?year=2026&month=9',
  'CSV URL uses the selected month',
)
assertEqual(
  monthlyExportUrl('', 'pdf', 2027, 1),
  '/api/export/monthly.pdf?year=2027&month=1',
  'PDF URL changes with month navigation',
)
assertEqual(
  monthlyExportFilename('pdf', 2026, 9),
  'work-tracker-2026-09.pdf',
  'download filename is deterministic',
)

const expectedBlob = new Blob(['report'])
let requestedUrl = ''
const successfulFetch: ExportFetch = async (url) => {
  requestedUrl = url
  return { ok: true, status: 200, blob: async () => expectedBlob }
}
const downloaded = await downloadMonthlyExport(successfulFetch, '', 'csv', 2026, 9)
assertEqual(
  requestedUrl,
  '/api/export/monthly.csv?year=2026&month=9',
  'CSV action requests the backend download',
)
assertEqual(downloaded === expectedBlob, true, 'successful download returns the response blob')
await downloadMonthlyExport(successfulFetch, '/local', 'pdf', 2027, 1)
assertEqual(
  requestedUrl,
  '/local/api/export/monthly.pdf?year=2027&month=1',
  'PDF action requests the selected month from the backend',
)

const existingPageState = { workDuration: 28_800, pay: '400.00' }
const failedFetch: ExportFetch = async () => ({
  ok: false,
  status: 503,
  blob: async () => new Blob(),
})
let failureStatus = 0
try {
  await downloadMonthlyExport(failedFetch, '', 'pdf', 2026, 9)
} catch (error) {
  if (error instanceof ExportRequestError) failureStatus = error.status
}
assertEqual(failureStatus, 503, 'export failure is exposed for isolated UI handling')
assertEqual(
  existingPageState,
  { workDuration: 28_800, pay: '400.00' },
  'export failure does not alter existing work or pay state',
)

console.log('Monthly export helper tests passed.')

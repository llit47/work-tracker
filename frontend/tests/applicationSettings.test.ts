import {
  DEFAULT_APPLICATION_SETTINGS,
  getLocationDisplayName,
  loadApplicationSettings,
  saveApplicationSettings,
  validateApplicationTitle,
} from '../src/applicationSettings.js'
import type { FetchLike } from '../src/pay.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

assertEqual(DEFAULT_APPLICATION_SETTINGS.application_title, 'Work Tracker', 'default title is stable')
assertEqual(
  getLocationDisplayName(
    { application_title: 'Work Tracker', locations: [{ location: 'gabinet_zabki', display_name: 'Gabinet Ząbki' }] },
    'gabinet_zabki',
  ),
  'Gabinet Ząbki',
  'configured location alias replaces the technical key',
)
assertEqual(
  getLocationDisplayName(DEFAULT_APPLICATION_SETTINGS, 'unknown_room'),
  'unknown_room',
  'unknown location falls back to its technical key',
)
assertEqual(validateApplicationTitle('   ').value, null, 'empty title is rejected')
assertEqual(validateApplicationTitle('  Czas pracy  ').value, 'Czas pracy', 'title is trimmed')

const responseSettings = {
  application_title: 'Czas pracy Przemek',
  locations: [{ location: 'gabinet_zabki', display_name: 'Gabinet Ząbki' }],
}
let requestedUrl = ''
let requestInit: RequestInit | undefined
const fetcher: FetchLike = async (url, init) => {
  requestedUrl = url
  requestInit = init
  return { ok: true, status: 200, json: async () => responseSettings }
}

assertEqual(await loadApplicationSettings(fetcher, '/local'), responseSettings, 'settings load is typed')
assertEqual(requestedUrl, '/local/api/application-settings', 'settings use the global API endpoint')
assertEqual(
  await saveApplicationSettings(fetcher, '/local', responseSettings),
  responseSettings,
  'saved settings return the authoritative backend state',
)
assertEqual(requestInit?.method, 'PUT', 'settings use an explicit update request')
assertEqual(JSON.parse(String(requestInit?.body)), responseSettings, 'technical location key stays in payload')

console.log('Application settings tests passed.')

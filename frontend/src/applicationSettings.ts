import type { FetchLike } from './pay.js'

export type LocationPresentationSetting = {
  location: string
  display_name: string | null
}

export type ApplicationSettings = {
  application_title: string
  locations: LocationPresentationSetting[]
}

export type ApplicationSettingsFormErrors = {
  applicationTitle?: string
}

export const DEFAULT_APPLICATION_SETTINGS: ApplicationSettings = {
  application_title: 'Work Tracker',
  locations: [{ location: 'gabinet_zabki', display_name: null }],
}

export function getLocationDisplayName(
  settings: ApplicationSettings,
  location: string,
): string {
  return settings.locations.find((item) => item.location === location)?.display_name || location
}

export function validateApplicationTitle(title: string): {
  value: string | null
  errors: ApplicationSettingsFormErrors
} {
  const value = title.trim()
  if (!value) {
    return { value: null, errors: { applicationTitle: 'Podaj nazwę aplikacji.' } }
  }
  if (value.length > 100) {
    return {
      value: null,
      errors: { applicationTitle: 'Nazwa aplikacji może mieć maksymalnie 100 znaków.' },
    }
  }
  return { value, errors: {} }
}

export async function loadApplicationSettings(
  fetcher: FetchLike,
  apiBase: string,
  signal?: AbortSignal,
): Promise<ApplicationSettings> {
  const response = await fetcher(`${apiBase}/api/application-settings`, { signal })
  if (!response.ok) throw new Error(`Application settings request failed with status ${response.status}`)
  return await response.json() as ApplicationSettings
}

export async function saveApplicationSettings(
  fetcher: FetchLike,
  apiBase: string,
  settings: ApplicationSettings,
): Promise<ApplicationSettings> {
  const response = await fetcher(`${apiBase}/api/application-settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!response.ok) throw new Error(`Application settings request failed with status ${response.status}`)
  return await response.json() as ApplicationSettings
}

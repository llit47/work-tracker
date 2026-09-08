export type ThemePreference = 'auto' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'work-tracker.theme'

type StorageReader = Pick<Storage, 'getItem'>
type StorageWriter = Pick<Storage, 'setItem'>

export function readThemePreference(storage: StorageReader): ThemePreference {
  try {
    const stored = storage.getItem(THEME_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' || stored === 'auto' ? stored : 'auto'
  } catch {
    return 'auto'
  }
}

export function writeThemePreference(
  storage: StorageWriter,
  preference: ThemePreference,
): void {
  try {
    storage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // The theme remains active for this tab when storage is unavailable.
  }
}

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  if (preference === 'auto') return systemPrefersDark ? 'dark' : 'light'
  return preference
}

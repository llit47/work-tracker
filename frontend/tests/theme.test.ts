import {
  THEME_STORAGE_KEY,
  readThemePreference,
  resolveTheme,
  writeThemePreference,
} from '../src/theme.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

function memoryStorage(initialValues: Record<string, string> = {}) {
  const values = new Map(Object.entries(initialValues))
  return {
    getItem(key: string) { return values.get(key) ?? null },
    setItem(key: string, value: string) { values.set(key, value) },
  }
}

const storage = memoryStorage()
assertEqual(readThemePreference(storage), 'auto', 'theme defaults to Auto')
assertEqual(resolveTheme('auto', false), 'light', 'Auto follows a light system theme')
assertEqual(resolveTheme('auto', true), 'dark', 'Auto follows a dark system theme')
assertEqual(resolveTheme('light', true), 'light', 'Jasny overrides the system theme')
assertEqual(resolveTheme('dark', false), 'dark', 'Ciemny overrides the system theme')
writeThemePreference(storage, 'dark')
assertEqual(storage.getItem(THEME_STORAGE_KEY), 'dark', 'theme preference is persisted locally')
assertEqual(readThemePreference(storage), 'dark', 'stored theme preference is restored')
assertEqual(readThemePreference(memoryStorage({ [THEME_STORAGE_KEY]: 'invalid' })), 'auto', 'invalid value falls back to Auto')

console.log('Theme helper tests passed.')

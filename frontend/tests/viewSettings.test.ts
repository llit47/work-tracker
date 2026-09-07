import {
  SHOW_IGNORED_EVENTS_STORAGE_KEY,
  getVisibleDays,
  hasStandaloneUndoAction,
  isEventVisible,
  readShowIgnoredEventsPreference,
  writeShowIgnoredEventsPreference,
} from '../src/viewSettings.js'

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`)
  }
}

function memoryStorage(initialValues: Record<string, string> = {}) {
  const values = new Map(Object.entries(initialValues))
  return {
    getItem(key: string) {
      return values.get(key) ?? null
    },
    setItem(key: string, value: string) {
      values.set(key, value)
    },
  }
}

const normalEvent = { id: 1, is_ignored: false }
const ignoredEvent = { id: 2, is_ignored: true, is_manual: false }
const manualEvent = { id: 3, is_ignored: false, is_manual: true }
const correctedEvent = { id: 4, is_ignored: false, is_timestamp_corrected: true }

assertEqual(isEventVisible(ignoredEvent, false), false, 'ignored events are hidden by default')
assertEqual(isEventVisible(ignoredEvent, true), true, 'the view preference reveals ignored events')
assertEqual(isEventVisible(normalEvent, false), true, 'normal events remain visible')
assertEqual(isEventVisible(manualEvent, false), true, 'manual events remain visible')
assertEqual(isEventVisible(correctedEvent, false), true, 'timestamp-corrected events remain visible')
assertEqual(hasStandaloneUndoAction(ignoredEvent), true, 'revealed ignored events retain their undo control')

const normalDay = { date: '2026-09-06', items: [normalEvent], ignored_events: [] }
const ignoredOnlyDay = { date: '2026-09-07', items: [], ignored_events: [ignoredEvent] }
assertEqual(getVisibleDays([normalDay, ignoredOnlyDay], false), [normalDay], 'ignored-only days are hidden by default')
assertEqual(
  getVisibleDays([normalDay, ignoredOnlyDay], true),
  [normalDay, ignoredOnlyDay],
  'ignored-only days are restored with the preference',
)

const storage = memoryStorage()
assertEqual(readShowIgnoredEventsPreference(storage), false, 'the first visit defaults to hidden ignored events')
writeShowIgnoredEventsPreference(storage, true)
assertEqual(
  storage.getItem(SHOW_IGNORED_EVENTS_STORAGE_KEY),
  'true',
  'the enabled preference is persisted',
)
assertEqual(readShowIgnoredEventsPreference(storage), true, 'the enabled preference is restored after reload')
writeShowIgnoredEventsPreference(storage, false)
assertEqual(readShowIgnoredEventsPreference(storage), false, 'the disabled preference is restored after reload')

const unavailableStorage = {
  getItem() {
    throw new Error('storage unavailable')
  },
  setItem() {
    throw new Error('storage unavailable')
  },
}
assertEqual(readShowIgnoredEventsPreference(unavailableStorage), false, 'unavailable storage uses the safe default')
writeShowIgnoredEventsPreference(unavailableStorage, true)

console.log('View settings tests passed.')

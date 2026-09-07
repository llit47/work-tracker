export const SHOW_IGNORED_EVENTS_STORAGE_KEY = 'work-tracker.showIgnoredEvents'

type StorageReader = Pick<Storage, 'getItem'>
type StorageWriter = Pick<Storage, 'setItem'>

type VisibleEvent = {
  is_ignored: boolean
}

type VisibleDay = {
  items: readonly unknown[]
  ignored_events: readonly unknown[]
}

type UndoableEvent = {
  is_manual: boolean
  is_ignored: boolean
}

export function readShowIgnoredEventsPreference(storage: StorageReader): boolean {
  try {
    return storage.getItem(SHOW_IGNORED_EVENTS_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeShowIgnoredEventsPreference(storage: StorageWriter, value: boolean): void {
  try {
    storage.setItem(SHOW_IGNORED_EVENTS_STORAGE_KEY, String(value))
  } catch {
    // The view setting remains usable for this tab when storage is unavailable.
  }
}

export function isEventVisible(event: VisibleEvent, showIgnoredEvents: boolean): boolean {
  return !event.is_ignored || showIgnoredEvents
}

export function getVisibleDays<T extends VisibleDay>(days: readonly T[], showIgnoredEvents: boolean): T[] {
  return days.filter((day) => day.items.length > 0 || (showIgnoredEvents && day.ignored_events.length > 0))
}

export function hasStandaloneUndoAction(event: UndoableEvent): boolean {
  return event.is_manual || event.is_ignored
}

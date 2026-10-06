export const SHOW_IGNORED_EVENTS_STORAGE_KEY = 'work-tracker.showIgnoredEvents'

type StorageReader = Pick<Storage, 'getItem'>
type StorageWriter = Pick<Storage, 'setItem'>

type VisibleEvent = {
  is_ignored: boolean
}

type VisibleDay = {
  date: string
  items: readonly { status: string }[]
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
  return days
    .map((day) => ({
      ...day,
      items: day.items.filter((item) => isWorkItemVisible(item, showIgnoredEvents)),
    }))
    .filter((day) => day.items.length > 0 || (showIgnoredEvents && day.ignored_events.length > 0))
    .sort((left, right) => right.date.localeCompare(left.date))
}

export function isWorkItemVisible(item: { status: string }, showIgnoredEvents: boolean): boolean {
  return item.status !== 'suppressed_short_visit' || showIgnoredEvents
}

export function hasStandaloneUndoAction(event: UndoableEvent): boolean {
  return event.is_manual || event.is_ignored
}

export type BackgroundRefresh = {
  refresh: () => Promise<void>
  dispose: () => void
}

export function createBackgroundRefresh<T>(
  load: (signal: AbortSignal) => Promise<T>,
  onLoaded: (data: T) => void,
): BackgroundRefresh {
  let disposed = false
  let inFlight: Promise<void> | null = null
  let activeController: AbortController | null = null

  return {
    refresh() {
      if (disposed) return Promise.resolve()
      if (inFlight) return inFlight

      const controller = new AbortController()
      activeController = controller
      inFlight = Promise.resolve()
        .then(() => load(controller.signal))
        .then((data) => {
          if (!disposed) onLoaded(data)
        })
        .catch(() => {
          // Keep the rendered data on failure; a later poll can try again.
        })
        .finally(() => {
          inFlight = null
          activeController = null
        })
      return inFlight
    },
    dispose() {
      disposed = true
      activeController?.abort()
    },
  }
}

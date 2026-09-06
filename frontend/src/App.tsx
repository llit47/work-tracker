import { useEffect, useState } from 'react'

type WorkEvent = {
  id: number
  event_type: 'entry' | 'exit'
  location: string
  event_timestamp: string
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? ''

function formatMonth(date: Date) {
  return new Intl.DateTimeFormat('pl-PL', { month: 'long', year: 'numeric' }).format(date).toUpperCase()
}

function formatEventDate(timestamp: string) {
  const [year, month, day] = timestamp.slice(0, 10).split('-')
  return `${day}.${month}.${year}`
}

function formatEventTime(timestamp: string) {
  return timestamp.slice(11, 16)
}

function App() {
  const now = new Date()
  const [events, setEvents] = useState<WorkEvent[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetch(`${apiBase}/api/work-events?year=${now.getFullYear()}&month=${now.getMonth() + 1}`)
        if (!response.ok) throw new Error('API request failed')
        setEvents(await response.json())
        setState('ready')
      } catch {
        setState('error')
      }
    }
    void load()
  }, [])

  return (
    <main className="page">
      <section className="card" aria-live="polite">
        <h1>Work Tracker</h1>
        <h2>{formatMonth(now)}</h2>
        {state === 'loading' && <p className="message">Ładowanie zdarzeń…</p>}
        {state === 'error' && <p className="message error">Nie udało się pobrać danych. Sprawdź połączenie z API.</p>}
        {state === 'ready' && events.length === 0 && <p className="message">Brak zdarzeń w tym miesiącu.</p>}
        {state === 'ready' && events.length > 0 && <table>
          <thead><tr><th>Data</th><th>Godzina</th><th>Zdarzenie</th></tr></thead>
          <tbody>{events.map((event) => {
            const entering = event.event_type === 'entry'
            return <tr key={event.id}>
              <td data-label="Data">{formatEventDate(event.event_timestamp)}</td>
              <td data-label="Godzina">{formatEventTime(event.event_timestamp)}</td>
              <td data-label="Zdarzenie"><span className={`event ${entering ? 'entry' : 'exit'}`}>{entering ? 'WEJŚCIE' : 'WYJŚCIE'}</span></td>
            </tr>
          })}</tbody>
        </table>}
      </section>
    </main>
  )
}

export default App

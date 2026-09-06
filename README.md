# Work Tracker

Prosta aplikacja działająca lokalnie w sieci LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy. Backend przyjmuje zabezpieczone webhooki Home Assistanta, zapisuje każde zdarzenie w SQLite i udostępnia API. Frontend React wyświetla zdarzenia bieżącego miesiąca.

Nie konfiguruje publicznego dostępu, domeny, proxy, tunelu ani HTTPS.

## Wymagania

- Python 3.11+
- Node.js 20+ i npm

## Instalacja i konfiguracja

1. Skopiuj konfigurację i ustaw własny losowy sekret o długości co najmniej 32 znaków:

   ```bash
   cp .env.example backend/.env
   # edytuj backend/.env, przede wszystkim WEBHOOK_TOKEN
   ```

2. Zainstaluj backend:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   ```

3. Utwórz bazę przez migrację:

   ```bash
   alembic upgrade head
   ```

4. W drugim terminalu przygotuj frontend:

   ```bash
   cd frontend
   cp .env.example .env
   npm install
   ```

## Uruchomienie lokalne

Terminal backendu (z aktywnym środowiskiem wirtualnym):

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal frontendu:

```bash
cd frontend
npm run dev
```

Otwórz adres wyświetlony przez Vite (zwykle `http://localhost:5173`).

### Uruchomienie w zaufanej sieci LAN

Powyższa komenda nasłuchuje tylko na komputerze lokalnym i jest właściwa do pracy developerskiej. Gdy Home Assistant działa na innym hoście LAN, uruchom backend na wszystkich interfejsach serwera:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Nie oznacza to publicznego dostępu: nie konfiguruj przekierowania portów, publicznego IP ani tunelu. Dostęp powinien być ograniczony do zaufanej sieci LAN przez konfigurację sieci serwera. Home Assistant nie wymaga CORS; CORS dotyczy wyłącznie przeglądarkowego frontendu. Jeśli frontend Vite działa pod innym adresem LAN, dodaj jego pełny origin do `CORS_ORIGINS`.

## Migracje

Po aktywowaniu virtualenv w `backend/`:

```bash
alembic upgrade head
alembic revision --autogenerate -m "opis zmiany"
```

## Testy

```bash
cd backend
source .venv/bin/activate
pytest
```

Frontend można sprawdzić komendą:

```bash
cd frontend
npm run build
```

## Endpointy

- `POST /api/webhook/home-assistant` — przyjmuje JSON webhooka; wymaga nagłówka `X-Webhook-Token`.
- `GET /api/work-events?year=2026&month=9` — zwraca chronologicznie zdarzenia dla wskazanego miesiąca kalendarzowego w offsetcie przekazanym przez Home Assistanta.
- `GET /api/health` — prosty status API.

Przykładowy webhook:

```bash
curl -X POST http://127.0.0.1:8000/api/webhook/home-assistant \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Token: TWOJ_SEKRET' \
  -d '{"event":"entry","location":"gabinet_zabki","timestamp":"2026-09-06T08:14:32+02:00","source":"home_assistant"}'
```

Format lokalizacji to małe litery, cyfry i `_`, np. `gabinet_zabki`; dzięki temu można później dodać kolejne lokalizacje bez zmiany modelu.

`source` w obecnej wersji musi mieć wartość `home_assistant`. Zdarzenia zapisują oryginalny ISO-8601 timestamp wraz z offsetem oraz osobny, znormalizowany timestamp UTC. Ten drugi będzie podstawą przyszłego liczenia czasu pracy, a pierwszy zachowuje lokalną datę i godzinę zdarzenia również przy zmianie czasu letniego/zimowego.

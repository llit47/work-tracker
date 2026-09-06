# Dziennik projektu — Work Tracker

## Cel projektu

Lokalna aplikacja LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy wysyłanych w przyszłości przez Home Assistant.

## Aktualny stan

Podstawowa wersja jest zaimplementowana: bezpiecznie odbiera webhooki, waliduje je, zapisuje wszystkie zdarzenia do SQLite i wyświetla zdarzenia bieżącego miesiąca w prostym interfejsie React. Zawiera zestaw 9 testów backendu, w tym sortowanie w czasie zmiany czasu letniego/zimowego.

## Ukończone etapy

- Struktura projektu z rozdzielonym `backend/` i `frontend/`.
- Backend FastAPI, SQLAlchemy, SQLite i Alembic.
- Migracja tworząca tabelę zdarzeń oraz indeksy.
- Zabezpieczony tokenem endpoint webhooka i endpoint odczytu miesiąca.
- Widok bieżącego miesiąca z obsługą ładowania, braku danych i błędu.
- Testy backendu oraz dokumentacja uruchomienia.
- Review modelu czasu, tokenu, CORS i działania w LAN.

## Aktualnie wykonywany etap

Etap 1 — podstawowe rejestrowanie i prezentacja zdarzeń — został zaimplementowany i zweryfikowany testami backendu. Przed wdrożeniem należy uruchomić frontendowy build w środowisku z Node/npm.

## Następne kroki

- Ustalić docelowy sposób uruchomienia w LAN na serwerze/VM/kontenerze.
- Skonfigurować Home Assistant do wysyłania webhooków po decyzji o adresie LAN.
- Dopiero w kolejnych etapach: obliczanie czasu pracy, widok poprzednich miesięcy, korekty ręczne i rozszerzenia lokalizacji.

## Architektura

- **Backend:** FastAPI w Pythonie; logika API jest w `backend/app/`.
- **Frontend:** React + Vite w `frontend/`.
- **Baza danych:** lokalny plik SQLite, tworzony przez Alembic.
- **API:** REST pod `/api`.
- **Komunikacja z Home Assistant:** przyszły Home Assistant wyśle `POST` z nagłówkiem `X-Webhook-Token`; sama integracja nie jest obecnie konfigurowana.

## Endpointy API

- `POST /api/webhook/home-assistant`
- `GET /api/work-events?year=YYYY&month=MM`
- `GET /api/health`

## Model danych

Tabela `work_events` przechowuje wszystkie otrzymane zdarzenia: `id`, `event_type`, `location`, `event_timestamp`, `received_at`, `source` oraz techniczne `event_timestamp_utc`. Baza dodatkowo wymusza, że `event_type` jest `entry` albo `exit`.

SQLite nie zachowuje niezawodnie stref czasowych w natywnym typie daty, dlatego timestampy są świadomie przechowywane jako tekst ISO-8601. `event_timestamp` zachowuje oryginalny timestamp Home Assistanta wraz z offsetem i wyznacza lokalny miesiąc kalendarzowy. `received_at` rejestruje niezależnie moment dotarcia żądania w UTC. `event_timestamp_utc` jest zawsze znormalizowany do UTC i służy do poprawnego sortowania oraz przyszłych obliczeń czasu pracy, także przy zmianie czasu. Indeksy istnieją dla timestampu zdarzenia, jego wartości UTC oraz lokalizacji.

## Konfiguracja

W `backend/.env` wymagane jest:

- `WEBHOOK_TOKEN` — losowy token wymagany przez webhook, co najmniej 32 znaki.

Opcjonalne wartości:

- `DATABASE_URL` — domyślnie `sqlite:///./work_tracker.db`.
- `CORS_ORIGINS` — adresy lokalnego serwera Vite.

`frontend/.env` może ustawić `VITE_API_BASE_URL`, domyślnie `http://localhost:8000` zgodnie z plikiem przykładowym.

## Uruchamianie

```bash
cp .env.example backend/.env
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

W osobnym terminalu:

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Gdy Home Assistant działa na osobnym hoście zaufanej sieci LAN, backend należy uruchomić z `--host 0.0.0.0`. Nie należy otwierać portu poza LAN ani konfigurować tunelu. CORS nie dotyczy webhooka Home Assistanta; `CORS_ORIGINS` ustawia się wyłącznie dla originu frontendu uruchomionego w przeglądarce.

## Testowanie

```bash
cd backend
source .venv/bin/activate
pytest
```

```bash
cd frontend
npm run build
```

## Home Assistant

Integracja z Home Assistant nie została jeszcze skonfigurowana. Backend jest przygotowany do przyjmowania webhooków.

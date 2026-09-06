# Dziennik projektu — Work Tracker

## Cel projektu

Lokalna aplikacja LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy wysyłanych w przyszłości przez Home Assistant.

## Aktualny stan

Podstawowa wersja aplikacji jest zaimplementowana. Dodano instalację i aktualizację dla Debiana/Ubuntu: trwałe dane poza repozytorium, usługę systemd i produkcyjne serwowanie zbudowanego frontendu przez FastAPI.

## Ukończone etapy

- Struktura projektu z rozdzielonym `backend/` i `frontend/`.
- Backend FastAPI, SQLAlchemy, SQLite i Alembic.
- Migracja tworząca tabelę zdarzeń oraz indeksy.
- Zabezpieczony tokenem endpoint webhooka i endpoint odczytu miesiąca.
- Widok bieżącego miesiąca z obsługą ładowania, braku danych i błędu.
- Testy backendu oraz dokumentacja uruchomienia.
- Review modelu czasu, tokenu, CORS i działania w LAN.
- Interaktywny instalator oraz updater z backupem SQLite.
- Automatyczny rollback kodu/unitu, a po rozpoczęciu migracji również bazy danych.
- Dedykowany użytkownik i utwardzona usługa systemd.
- Manifest wykrywający nowe wymagane ustawienia.
- Produkcyjne serwowanie `frontend/dist` przez FastAPI.
- Jawne pakowanie backendu oraz kontrolowane, tylko do odczytu uprawnienia kodu dla użytkownika usługi.

## Aktualnie wykonywany etap

Etap wdrożenia na Debianie/Ubuntu jest zaimplementowany i podlega kolejnym testom na świeżym Debianie 13 LXC. Kod pozostaje własnością roota, a użytkownik `work-tracker` otrzymuje grupowy dostęp do odczytu i wykonania potrzebny do migracji oraz uruchomienia usługi. Updater i rollback ponownie stosują ten sam model po przebudowie plików.

## Następne kroki

- Ustalić docelowy sposób uruchomienia w LAN na serwerze/VM/kontenerze.
- Skonfigurować Home Assistant do wysyłania webhooków po decyzji o adresie LAN.
- Przetestować pełną instalację na docelowym kontenerze LXC z systemd.
- Dopiero w kolejnych etapach: obliczanie czasu pracy, widok poprzednich miesięcy, korekty ręczne i rozszerzenia lokalizacji.

## Architektura

- **Backend:** FastAPI w Pythonie; logika API jest w `backend/app/`.
- **Frontend:** React + Vite w `frontend/`; po buildzie FastAPI serwuje `frontend/dist` na `/`.
- **Baza danych:** lokalny plik SQLite, tworzony przez Alembic.
- **API:** REST pod `/api`.
- **Komunikacja z Home Assistant:** przyszły Home Assistant wyśle `POST` z nagłówkiem `X-Webhook-Token`; sama integracja nie jest obecnie konfigurowana.
- **Deployment:** `install.sh`, `update.sh`, manifest konfiguracji i unit systemd dla Debiana/Ubuntu.

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

Instalacja systemowa używa `/etc/work-tracker/work-tracker.env`. Klucze `APP_HOST`, `APP_PORT`, `WEBHOOK_TOKEN` i `DATABASE_URL` są wymagane; `CORS_ORIGINS` jest opcjonalny. Definicje są utrzymywane w `deploy/config.manifest`, co pozwala updaterowi wykrywać nowe wymagane wartości bez zmieniania istniejących wpisów.

## Wdrożenie Debian/Ubuntu

```bash
curl -fsSL https://raw.githubusercontent.com/llit47/work-tracker/main/install.sh | sudo bash
```

Kod jest instalowany w `/opt/work-tracker`, konfiguracja w `/etc/work-tracker`, baza w `/var/lib/work-tracker`, a backupy w `/var/backups/work-tracker`. Proces działa jako użytkownik `work-tracker` i automatycznie startuje przez systemd.

Aktualizacja:

```bash
sudo /opt/work-tracker/update.sh
```

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
bash -n ../install.sh ../update.sh ../deploy/common.sh ../deploy/tests/config_update_test.sh
../deploy/tests/config_update_test.sh
```

```bash
cd frontend
npm run build
```

## Home Assistant

Integracja z Home Assistant nie została jeszcze skonfigurowana. Backend jest przygotowany do przyjmowania webhooków.

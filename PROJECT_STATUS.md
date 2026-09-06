# Work Tracker — project status

## Current status

- **Phase 0 — Deployment and Home Assistant ingestion: DONE**
- **Phase 1 — Work-time calculation: NEXT**

Szczegółowy zakres etapów i kryteria ukończenia znajdują się w `ROADMAP.md`.

## What currently works

- Backend FastAPI odbiera zabezpieczone tokenem webhooki Home Assistant.
- Eventy `entry` i `exit` są walidowane i zapisywane w SQLite.
- API udostępnia eventy wskazanego miesiąca oraz endpoint health check.
- Frontend pokazuje eventy bieżącego miesiąca.
- Home Assistant wysyła eventy przez `rest_command`; automatyzacje wejścia i wyjścia ze strefy są skonfigurowane.
- Ręczny test Home Assistant → API → baza → frontend zakończył się powodzeniem.

Aktualne endpointy:

- `POST /api/webhook/home-assistant`
- `GET /api/work-events?year=YYYY&month=MM`
- `GET /api/health`

## Production/deployment state

- Środowisko: Debian 13 LXC na Proxmox, wyłącznie w sieci LAN.
- Proces: `work-tracker.service`; autostart po restarcie LXC i restart po awarii.
- Instalacja: `install.sh`.
- Aktualizacja: `update.sh` z backupem SQLite i rollbackiem.
- Kod: `/opt/work-tracker`.
- Konfiguracja: `/etc/work-tracker/work-tracker.env`.
- Baza: `/var/lib/work-tracker/work_tracker.db`.
- Backupy: `/var/backups/work-tracker`.

## Current data model / important assumptions

- Raw events z Home Assistant są źródłem prawdy (`source of truth`).
- Logika czasu pracy nie może niszczyć ani nadpisywać raw events.
- Raw event zawiera m.in. `id`, `event_type`, `location`, `event_timestamp`, `event_timestamp_utc`, `received_at` i `source`.
- Jedyna aktywna lokalizacja to `gabinet_zabki`.
- Obsługiwane typy eventów to `entry` i `exit`.
- `event_timestamp` pochodzi z Home Assistant i zachowuje lokalny offset.
- `event_timestamp_utc` przechowuje odpowiadającą mu chwilę UTC.
- `received_at` oznacza czas odebrania webhooka przez backend.

## Next implementation target

**Phase 1 — Work-time calculation**

Najbliższy PR funkcjonalny powinien dotyczyć wyłącznie przekształcenia raw events w sesje pracy, dzienne czasy oraz miesięczne podsumowanie, łącznie z jawną obsługą anomalii. Szczegółowe acceptance criteria są w `ROADMAP.md`.

## Known intentional limitations

- jedna praca i jedna aktywna lokalizacja,
- brak ręcznych korekt,
- brak wyliczania wynagrodzenia,
- brak logowania użytkownika,
- brak eksportów,
- brak publicznego dostępu do aplikacji.

## Recent milestones

- PR #1 — `feat: initial local Work Tracker`
- PR #2 — `feat: add Debian installer and safe updater`
- PR #3 — `fix: make fresh Debian installation work`
- PR #4 — `fix: allow service user to run deployed application`

## Maintenance rule

Każdy PR realizujący roadmapę powinien aktualizować ten plik oraz status właściwego etapu w `ROADMAP.md`. Nie należy rozpoczynać następnej fazy bez jawnie określonego zakresu PR.

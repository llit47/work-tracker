# Work Tracker — project status

## Current status

- **Phase 0 — Deployment and Home Assistant ingestion: DONE**
- **Phase 1 — Work-time calculation: DONE**
- **Phase 2 — Monthly navigation and summaries: DONE**
- **Phase 3 — Manual corrections: DONE**
- **Phase 4 — Pay calculation: NEXT**

Szczegółowy zakres etapów i kryteria ukończenia znajdują się w `ROADMAP.md`.

## What currently works

- Backend FastAPI odbiera zabezpieczone tokenem webhooki Home Assistant.
- Eventy `entry` i `exit` są walidowane i zapisywane w SQLite.
- API udostępnia eventy wskazanego miesiąca oraz endpoint health check.
- Frontend pozwala przechodzić między miesiącami, wybrać konkretny miesiąc i wrócić do miesiąca bieżącego.
- Backend wylicza sesje, dzienne sumy, miesięczny czas, liczbę dni pracy i anomalie bez zmiany raw events.
- Frontend pokazuje podsumowanie wybranego miesiąca, średni czas dziennie oraz sesje i problemy pogrupowane według dni.
- Wybrany miesiąc jest zapisany jako `/?year=YYYY&month=MM`; odświeżenie i Back/Forward zachowują właściwy widok.
- Błędne parametry URL wracają deterministycznie do bieżącego miesiąca, a błędy API można ponowić bez przeładowania strony.
- Użytkownik może skorygować timestamp raw eventu, zignorować raw event oraz ręcznie dodać brakujące wejście lub wyjście.
- Każdą korektę można cofnąć; frontend po mutacji pobiera nowe podsumowanie z backendu.
- Frontend rozróżnia zdarzenia Home Assistant, zdarzenia skorygowane, zignorowane i dodane ręcznie.
- Home Assistant wysyła eventy przez `rest_command`; automatyzacje wejścia i wyjścia ze strefy są skonfigurowane.
- Ręczny test Home Assistant → API → baza → frontend zakończył się powodzeniem.

Aktualne endpointy:

- `POST /api/webhook/home-assistant`
- `GET /api/work-events?year=YYYY&month=MM`
- `GET /api/work-summary?year=YYYY&month=MM`
- `PUT /api/work-events/{raw_event_id}/timestamp-correction`
- `PUT /api/work-events/{raw_event_id}/ignore`
- `POST /api/manual-events`
- `GET /api/corrections`
- `DELETE /api/corrections/{correction_id}`
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
- Sesje są parowane per lokalizacja po kolejności `(event_timestamp_utc, id)` i przypisywane do lokalnej daty `entry`.
- Sesja przechodząca przez północ lub granicę miesiąca w całości należy do dnia i miesiąca wejścia.
- Tylko sesje `valid` zwiększają dzienne i miesięczne sumy oraz liczbę dni pracy.
- Próg podejrzanie długiej sesji wynosi ponad 16 godzin; dokładnie 16 godzin pozostaje poprawne.
- Wykrywane anomalie: `missing_exit`, `duplicate_entry`, `orphan_exit`, `unusually_long_session` i `ambiguous_timestamp`.
- Korekty są przechowywane oddzielnie w `work_event_corrections`; `work_events` pozostaje niezmiennym audytem danych Home Assistant.
- `work_event_corrections` zawiera `id`, `correction_type`, `created_at`, `updated_at`, opcjonalny `raw_event_id` oraz pola efektywnego eventu: `event_type`, `event_timestamp`, `event_timestamp_utc` i `location`.
- Typy korekt to `timestamp_override`, `ignore_event` i `manual_event`.
- Dla pojedynczego raw eventu może istnieć najwyżej jedna aktywna korekta: timestamp albo ignorowanie.
- Effective event stream powstaje z raw events i korekt, a następnie trafia do niezmienionego algorytmu Phase 1.
- Manualne i skorygowane timestampy zachowują offset, a ich chwile UTC są przechowywane osobno.
- Undo usuwa rekord `work_event_corrections`; nigdy nie usuwa ani nie modyfikuje raw eventu.
- Migracja Alembic `20260907_02` dodaje wyłącznie strukturę korekt i zachowuje dane `work_events`.

## Next implementation target

**Phase 4 — Pay calculation**

Najbliższy PR funkcjonalny powinien dotyczyć stawek i miesięcznego wynagrodzenia z mechanizmem zachowującym historyczne rozliczenia. Szczegółowy zakres znajduje się w `ROADMAP.md`.

## Known intentional limitations

- jedna praca i jedna aktywna lokalizacja,
- brak wyliczania wynagrodzenia,
- brak logowania użytkownika,
- brak eksportów,
- brak publicznego dostępu do aplikacji.

## Recent milestones

- PR #1 — `feat: initial local Work Tracker`
- PR #2 — `feat: add Debian installer and safe updater`
- PR #3 — `fix: make fresh Debian installation work`
- PR #4 — `fix: allow service user to run deployed application`
- PR #5 — `docs: add project roadmap and update project status`
- PR #6 — `feat: add work-time calculation`
- PR #7 — `feat: add monthly navigation` (ten PR)

## Maintenance rule

Każdy PR realizujący roadmapę powinien aktualizować ten plik oraz status właściwego etapu w `ROADMAP.md`. Nie należy rozpoczynać następnej fazy bez jawnie określonego zakresu PR.

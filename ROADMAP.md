# Work Tracker — roadmap

Każdy PR implementujący element roadmapy powinien:

1. aktualizować `PROJECT_STATUS.md`,
2. aktualizować status odpowiedniego etapu w `ROADMAP.md`,
3. nie implementować kolejnych etapów bez wyraźnego zakresu PR.

Statusy używane w dokumencie: `DONE`, `NEXT`, `PLANNED`, `DEFERRED`.

## Phase 0 — Deployment and Home Assistant ingestion

**Status: DONE**

Zakończony zakres:

- [x] Backend FastAPI i frontend React/Vite.
- [x] SQLite, SQLAlchemy i migracje Alembic.
- [x] `POST /api/webhook/home-assistant` z autoryzacją przez `X-Webhook-Token`.
- [x] Walidacja i trwały zapis eventów `entry` oraz `exit` wraz z timestampem HA i czasem odbioru.
- [x] Lista eventów bieżącego miesiąca na frontendzie.
- [x] Instalator dla Debiana/Ubuntu oraz updater z backupem i rollbackiem.
- [x] Uruchamianie przez `work-tracker.service` z autostartem i restartem po awarii.
- [x] Uprawnienia pozwalające użytkownikowi usługi czytać i uruchamiać aplikację bez prawa zapisu do kodu.
- [x] Rzeczywista instalacja zweryfikowana na Debian 13 LXC w Proxmox.
- [x] Integracja Home Assistant przez `rest_command` oraz automatyzacje wejścia i wyjścia ze strefy.
- [x] Ręczny test całej ścieżki Home Assistant → API → SQLite → frontend.

## Phase 1 — Work-time calculation

**Status: NEXT**

Cel: przekształcić surowe eventy w czytelne sesje pracy i podsumowania bez modyfikowania danych źródłowych.

Przykład:

```text
07:58 entry
16:13 exit
=> 8 h 15 min
```

Planowany zakres:

- parowanie `entry → exit` w sesje pracy,
- czas pracy danego dnia,
- suma czasu pracy w miesiącu,
- liczba dni pracy,
- prezentacja sesji i dni na frontendzie,
- zachowanie wszystkich raw events bez modyfikacji,
- jawna obsługa anomalii:
  - brak `exit`,
  - dwa `entry` pod rząd,
  - dwa `exit` pod rząd,
  - eventy odebrane lub zapisane poza kolejnością,
  - zmiana dnia i sesja przechodząca przez północ.

Poza zakresem Phase 1:

- ręczne korekty,
- wynagrodzenia i stawki,
- eksporty,
- logowanie użytkownika,
- druga praca lub drugie miejsce pracy.

### Acceptance criteria

- [ ] Poprawna para `entry → exit` tworzy jedną sesję z prawidłowym czasem trwania.
- [ ] Czas trwania jest liczony na podstawie jednoznacznych chwil UTC, z zachowaniem oryginalnych timestampów i offsetów do prezentacji.
- [ ] Wyniki obejmują czas każdego dnia, sumę miesiąca oraz liczbę dni pracy.
- [ ] Eventy są przetwarzane chronologicznie; kolejność odbioru HTTP nie zmienia wyniku.
- [ ] Każdy typ anomalii jest wykrywany i widoczny, bez zgadywania brakującego czasu pracy.
- [ ] Reguła dla sesji przechodzącej przez północ jest jawnie opisana i pokryta testami.
- [ ] Frontend pokazuje sesje/dni i podsumowanie bieżącego miesiąca oraz zachowuje obsługę ładowania, braku danych i błędu.
- [ ] Testy obejmują zwykłe sesje, anomalie, eventy poza kolejnością, północ oraz zmianę czasu letniego/zimowego.
- [ ] Rekordy raw events nie są aktualizowane ani usuwane przez logikę wyliczeń.
- [ ] `PROJECT_STATUS.md` i status Phase 1 zostają zaktualizowane po zakończeniu etapu.

## Phase 2 — Monthly navigation and summaries

**Status: PLANNED**

- poprzedni i następny miesiąc,
- wybór miesiąca,
- miesięczne podsumowanie,
- liczba godzin,
- liczba dni,
- średni czas pracy dziennie.

## Phase 3 — Manual corrections

**Status: PLANNED**

- dodawanie brakującego `entry` lub `exit`,
- korekta godziny,
- usunięcie logiczne lub oznaczenie błędnego zdarzenia,
- jasne rozróżnienie danych automatycznych i ręcznych,
- audytowalna historia zmian.

Raw events otrzymane z Home Assistant nie mogą być bezpowrotnie nadpisywane. Korekty powinny być osobną, możliwą do prześledzenia warstwą danych.

## Phase 4 — Pay calculation

**Status: PLANNED**

- stawka godzinowa,
- miesięczne wynagrodzenie,
- historia stawek albo równoważny mechanizm zapobiegający zmianie historycznych rozliczeń po zmianie stawki.

Zakres nadal zakłada jedną lokalizację i jedną pracę.

## Phase 5 — Dashboard and live shift

**Status: PLANNED**

- status `W PRACY` / `POZA PRACĄ`,
- dzisiejsze wejście,
- dzisiejsze wyjście,
- bieżący czas pracy dla otwartej sesji,
- podsumowanie miesiąca,
- czytelniejszy dashboard.

## Phase 6 — Export

**Status: PLANNED**

- CSV,
- PDF,
- raport miesięczny,
- Excel — opcjonalnie w przyszłości.

## Phase 7 — Authentication and hardening

**Status: PLANNED**

- prosty login,
- brak publicznej rejestracji,
- jeden użytkownik na początek,
- przygotowanie pod ewentualny późniejszy dostęp spoza LAN.

Ten etap nie oznacza decyzji o publicznym wystawieniu aplikacji. Obecnie Work Tracker pozostaje usługą LAN-only.

## Deferred / not planned now

**Status: DEFERRED**

- druga praca,
- wiele miejsc pracy (`multiple workplaces`),
- obsługa wielu użytkowników,
- wdrożenie dostępne z publicznego Internetu,
- zaawansowane role i uprawnienia,
- integracje payroll/accounting.

> **Multiple workplaces / second job is intentionally deferred and should not be implemented unless the roadmap is explicitly changed.**

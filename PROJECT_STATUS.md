# Work Tracker — project status

## Current status

- **Phase 0 — Deployment and Home Assistant ingestion: DONE**
- **Phase 1 — Work-time calculation: DONE**
- **Phase 2 — Monthly navigation and summaries: DONE**
- **Phase 3 — Manual corrections: DONE**
- **Phase 4 — Pay calculation: DONE**
  - **Phase 4A — backend pay-rate history and pay calculation: DONE**
  - **Phase 4B — frontend pay presentation and rate management: DONE**
- **Phase 5 — Dashboard and live shift: DONE**
- **Phase 6 — Export: DONE**
- **Phase 7 — Interactive Home Assistant event confirmation: IN PROGRESS**
  - **Phase 7A — Backend integration: DONE**
  - **Phase 7B — Correction input and live-domain behavior: DONE**
  - **Phase 7C — Home Assistant integration and safe rollout: NEXT**
- **Phase 8 — Authentication and hardening: PLANNED**

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
- Kompaktowa sekcja `Ustawienia` zawiera preferencje widoku i pozostaje domyślnie zwinięta.
- Sekcja `Ustawienia → Aplikacja` zapisuje globalną nazwę aplikacji i przyjazne nazwy lokalizacji w backendzie, dzięki czemu są wspólne dla wszystkich urządzeń.
- Każda canonical location może mieć osobno zapisaną opcjonalną IANA timezone. Backend waliduje ją przez `ZoneInfo`, a minimalny formularz `Ustawienia → Aplikacja` pozwala ją ustawić lub wyczyścić bez zgadywania wartości.
- Tytuł strony i karty przeglądarki korzysta z globalnej nazwy aplikacji; identyfikator `gabinet_zabki` pozostaje niezmienionym kluczem technicznym, a UI stosuje skonfigurowaną nazwę wyświetlaną z bezpiecznym fallbackiem do klucza.
- Widok obsługuje motywy `Auto`, `Jasny` i `Ciemny`; wybór jest lokalny dla przeglądarki, a tryb automatyczny reaguje na zmianę systemowego schematu kolorów.
- Ignorowane eventy są domyślnie ukryte; opcja `Pokaż ignorowane wydarzenia` przywraca ich audytowy widok wraz z możliwością cofnięcia korekty i jest zapamiętywana w `localStorage`.
- Backend przechowuje historyczne stawki godzinowe i wylicza dzienne oraz miesięczne wynagrodzenie wyłącznie z poprawnych sesji.
- Domyślna stawka to `50,00 PLN/h` od `1970-01-01`; kolejne stawki nie zmieniają historycznych rozliczeń.
- Frontend pobiera autorytatywne `pay-summary` i pokazuje miesięczne oraz dzienne wynagrodzenie bez przeliczania kwot w React.
- W zwykłym podsumowaniu widoczna jest stawka lub zakres stawek użytych w wybranym miesiącu, a szczegóły pozostają w kompaktowej sekcji.
- Sekcja `Ustawienia → Wynagrodzenie` pokazuje najnowszą stawkę, historię oraz formularz dodania nowej stawki z datą obowiązywania.
- Zmiana miesiąca anuluje nieaktualne żądanie wynagrodzenia; korekty czasu i dodanie stawki odświeżają płace z backendu.
- Błąd API płacowego jest prezentowany niezależnie i nie ukrywa poprawnie pobranego czasu pracy.
- Kompaktowy dashboard pokazuje bieżący status, start i czas otwartej zmiany, dzisiejszy czas oraz zakończony czas i wynagrodzenie bieżącego miesiąca.
- `GET /api/dashboard` wyprowadza live state z effective events i tych samych reguł parowania, które zasilają miesięczne podsumowania.
- Frontend odświeża dashboard co 30 sekund, a czas bieżącej zmiany aktualizuje lokalnie bez ciągłego odpytywania API; użytkownik widzi ukończone minuty.
- Frontend pokazuje `missing_exit` jako `Trwająca zmiana` wyłącznie dla pojedynczego wejścia odpowiadającego tej samej chwili UTC co autorytatywna bieżąca sesja dashboardu. Pozostałe otwarte lub niejednoznaczne wpisy pozostają problemami.
- Pierwszy snapshot oraz istotne zmiany dashboardu odświeżają aktualnie wybrane podsumowanie czasu i płac, także przy zamknięciu sesji na granicy miesięcy; sam postęp timera nie wywołuje tych żądań.
- Stan niejednoznaczny jest pokazywany jawnie dla duplikatów, sprzecznych eventów, wielu otwartych zmian oraz wejścia starszego niż 16 godzin.
- Otwarta zmiana nie tworzy syntetycznego eventu, nie modyfikuje raw events i nie zwiększa wynagrodzenia przed poprawnym zakończeniem.
- Wybrany miesiąc można pobrać jako CSV lub raport PDF bez ponownego wybierania daty.
- CSV jest kodowany jako UTF-8 z BOM, używa separatora `;` dla zgodności z polskim Excelem i prezentuje timestampy oraz czas z dokładnością do ukończonej minuty. Kolumny to: `data`, `wejście`, `wyjście`, `czas`, `lokalizacja`, `status`, `stawka_godzinowa`, `waluta`, `wynagrodzenie`; kolumna `czas_sekundy` nie jest eksportowana. Lokalizacja używa bieżącego aliasu z fallbackiem do klucza technicznego, a aliasy o prefiksie formuły są neutralizowane wyłącznie podczas serializacji CSV.
- PDF zawiera kompaktowe podsumowanie, wszystkie poprawne sesje, użyte stawki, kwoty oraz problemy; używa bieżących aliasów lokalizacji, zwykły miesiąc mieści się na jednej stronie A4, a dłuższe raporty są paginowane.
- PDF jest generowany przez ReportLab z osadzonym fontem Roboto obsługującym polskie znaki; wdrożenie nie wymaga przeglądarki ani ręcznej instalacji fontu.
- Oba formaty powstają z jednego modelu raportu zasilanego przez effective events, kanoniczny kalkulator czasu i historyczny kalkulator płac.
- Dashboard i miesięczne podsumowanie mają bardziej zwarty układ, a dni są domyślnie zwinięte. Nagłówek dnia nadal pokazuje wszystkie sesje, czas, wynagrodzenie, lokalizację i ostrzeżenia; szczegółowe eventy oraz akcje korekt są dostępne po rozwinięciu.
- Zwykłe godziny w interfejsie nie pokazują offsetu UTC; przy zmianie offsetu (np. DST) wyświetlany jest kompaktowy `+HH:MM`, a sesja przez północ pokazuje datę wyjścia.
- Home Assistant wysyła eventy przez `rest_command`; automatyzacje wejścia i wyjścia ze strefy są skonfigurowane.
- Odpowiedź webhooka ingestion zachowuje `id` i `status`, a dodatkowo zwraca event, canonical location, aktualny alias z fallbackiem oraz zapisany timestamp.
- Token-protected `POST /api/webhook/home-assistant/correction` przyjmuje time-only input dla konkretnego raw eventu i używa wspólnej audytowalnej korekty `timestamp_override`.
- Time-only correction jest rozwiązywana w timezone canonical location względem raw UTC instantu, przez sąsiednie daty i jednoznaczny najbliższy kandydat w oknie ±4 godzin; nonexistent/ambiguous DST jest odrzucane bez zgadywania.
- Pojedyncze future effective `entry` pozostaje oczekującym startem: przed godziną dashboard pokazuje `outside`, od wskazanej chwili zwykłe `working`; future `exit` i konflikty nadal fail-safe dają stan niejednoznaczny.
- Actionable notifications, handler akcji i YAML Home Assistanta nie są jeszcze wdrożone ani aktywowane; to zakres Phase 7C.
- Ręczny test Home Assistant → API → baza → frontend zakończył się powodzeniem.

Aktualne endpointy:

- `POST /api/webhook/home-assistant`
- `POST /api/webhook/home-assistant/correction`
- `GET /api/work-events?year=YYYY&month=MM`
- `GET /api/work-summary?year=YYYY&month=MM`
- `PUT /api/work-events/{raw_event_id}/timestamp-correction`
- `PUT /api/work-events/{raw_event_id}/ignore`
- `POST /api/manual-events`
- `GET /api/corrections`
- `DELETE /api/corrections/{correction_id}`
- `GET /api/pay-rates`
- `POST /api/pay-rates`
- `GET /api/application-settings`
- `PUT /api/application-settings`
- `GET /api/pay-summary?year=YYYY&month=MM`
- `GET /api/dashboard?timezone=IANA_TIMEZONE`
- `GET /api/export/monthly.csv?year=YYYY&month=MM`
- `GET /api/export/monthly.pdf?year=YYYY&month=MM`
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
- Migracja Alembic `20260907_03` dodaje `pay_rates` i seeduje jedną stawkę `50,00 PLN/h` od `1970-01-01`, bez modyfikacji eventów ani korekt.
- Migracja Alembic `20260908_04` dodaje izolowane tabele `application_settings` i `location_display_names`, seeduje tytuł `Work Tracker` i nie modyfikuje danych czasu pracy.
- Migracja Alembic `20260909_05` dodaje izolowaną tabelę `location_timezones` bez seedowania timezone i bez modyfikacji eventów, korekt, stawek, tytułu lub aliasów.
- Globalne ustawienia przechowują jeden tytuł aplikacji, opcjonalne aliasy oraz oddzielne opcjonalne IANA timezone canonical locations. Brak aliasu oznacza wyświetlenie identyfikatora technicznego; brak timezone pozostaje jawnym dozwolonym stanem i nie ma fallbacku.
- `pay_rates` zawiera `id`, unikalne `effective_from`, dokładne `hourly_rate`, `currency` oraz `created_at`; stawka jest przechowywana jako kanoniczny zapis dziesiętny, ponieważ SQLite `NUMERIC` używa dla takich wartości binarnego `REAL`.
- Stawka sesji jest wybierana jako najnowsza z `effective_from <=` lokalna data efektywnego wejścia.
- Płaca powstaje wyłącznie z sesji `valid`; czas anomalii nie jest zgadywany ani opłacany.
- Kwoty są liczone z sekund za pomocą `Decimal`, zaokrąglane do dwóch miejsc przez `ROUND_HALF_UP` dopiero dla wyniku dnia i miesiąca oraz zwracane przez API jako stringi.
- Prezentacja UI, PDF i CSV nie pokazuje sekund ani nie zaokrągla czasu do najbliższej minuty; baza, API domenowe, obliczenia czasu i wynagrodzenia zachowują pełną precyzję sekundową.
- Backend nie sumuje sesji rozliczanych w różnych walutach; taki miesiąc zwraca jednoznaczny błąd.
- Dashboard otrzymuje nazwę strefy IANA przeglądarki, aby poprawnie określić lokalne „dzisiaj” i bieżący miesiąc; wszystkie czasy trwania nadal wynikają z chwil UTC.
- Status `working` wymaga dokładnie jednego terminalnego `missing_exit` nie starszego niż 16 godzin. Pojedyncze future `entry` przy jednoznacznym bieżącym stanie `outside` pozostaje oczekującym startem; future `exit`, konfliktujące przyszłe eventy, terminalny `duplicate_entry`, `ambiguous_timestamp`, wiele otwartych wejść lub wejście starsze niż 16 godzin daje status `ambiguous`.
- Dzisiejszy efektywny czas dodaje otwartą zmianę wyłącznie do lokalnej daty jej wejścia. Miesięczna płaca obejmuje tylko zakończone sesje `valid`.
- Eksporty historyczne zachowują własność dnia/miesiąca z `WorkTimeItem.local_date`; nie używają odmiennych reguł strefowych live dashboardu.
- Raport obejmuje wyłącznie effective events, dlatego ignorowanie, korekta timestampu i manualne eventy automatycznie wpływają na eksport bez zmiany raw events.
- Anomalie trafiają do raportu informacyjnie i mają puste pola finansowe w CSV; nie są opłacane ani zamieniane na zgadywane sesje.

## Next implementation target

**Phase 7C — Home Assistant integration and safe rollout**

Backend Phase 7A/7B jest gotowy. Następny etap obejmuje dopiero actionable notifications, handler odpowiedzi, kill switch, testowy push, YAML Home Assistanta i bezpieczną aktywację produkcyjną. Obecne automatyzacje Home Assistanta nie zostały zmienione.

Dotychczasowy etap Authentication and hardening został przesunięty do Phase 8 i ma status `PLANNED`. Nie oznacza to decyzji o wystawieniu aplikacji do publicznego Internetu.

## Known intentional limitations

- jedna praca i jedna aktywna lokalizacja,
- brak edycji i usuwania historycznych stawek,
- brak nadgodzin, dodatków, podatków i przeliczeń walut,
- brak live estymacji wynagrodzenia dla niezakończonej zmiany,
- brak WebSocket/SSE; dashboard celowo korzysta z prostego pollingu,
- brak logowania użytkownika,
- brak eksportu XLSX/Excel,
- brak publicznego dostępu do aplikacji.

## Recent milestones

- PR #1 — `feat: initial local Work Tracker`
- PR #2 — `feat: add Debian installer and safe updater`
- PR #3 — `fix: make fresh Debian installation work`
- PR #4 — `fix: allow service user to run deployed application`
- PR #5 — `docs: add project roadmap and update project status`
- PR #6 — `feat: add work-time calculation`
- PR #7 — `feat: add monthly navigation`
- PR #8 — `feat: add manual work-time corrections`
- PR #9 — `fix: prevent stale entries from contaminating later sessions`
- PR #10 — `feat: add settings panel and hide ignored events`
- PR #11 — `feat: add pay-rate history and backend pay calculation`
- PR #12 — `feat: add pay presentation and rate management`
- PR #13 — `feat: add dashboard and live shift status`
- PR #14 — `feat: add monthly CSV and PDF export`
- PR #15 — `fix: hide seconds in PDF duration display`
- PR #16 — `fix: hide seconds from user-facing output`
- PR #17 — `feat: polish main dashboard and application settings`
- PR #18 — `fix: use location aliases in exports and handle active-day sessions`

## Maintenance rule

Każdy PR realizujący roadmapę powinien aktualizować ten plik oraz status właściwego etapu w `ROADMAP.md`. Nie należy rozpoczynać następnej fazy bez jawnie określonego zakresu PR.

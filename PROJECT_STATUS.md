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
- **Phase 7 — Interactive Home Assistant event confirmation: DONE**
  - **Phase 7A — Backend integration: DONE**
  - **Phase 7B — Correction input and live-domain behavior: DONE**
  - **Phase 7C — Home Assistant integration and safe rollout: DONE**
- **Post-Phase-7 — Short-visit suppression: DONE**
- **Phase 8 — Authentication and hardening: IN PROGRESS**
  - **Phase 8A1 — Identity foundation: DONE**
  - **Phase 8A2 — Server-side sessions and login UX: DONE**
  - **Phase 8A3 — Protect browser UI and API: DONE**
  - **Phase 8B1 — Browser security hardening: NEXT**
  - **Phase 8B2 — Reverse-proxy and Internet-readiness review: PLANNED**
- **Phase 9 — Cloudflare public deployment: PLANNED**
- **Phase 10 — Google Calendar integration: PLANNED**

Szczegółowy zakres etapów i kryteria ukończenia znajdują się w `ROADMAP.md`.

## What currently works

- 8A3: ordinary browser UI/API wymaga loginu. Centralny pure-ASGI guard chroni wszystkie HTTP `/api` i `/api/...`, również nowe trasy/mounty, przed routingiem i walidacją body; invalid/missing/expired/revoked cookie daje spójny JSON HTTP 401.
- Dokładne session-independent wyjątki: `POST /api/auth/login`, `GET /api/auth/me` (własna walidacja 200/401), `POST /api/auth/logout`, `GET /api/health`, `POST /api/webhook/home-assistant` i `POST /api/webhook/home-assistant/correction`. HA wymaga wyłącznie własnego `X-Webhook-Token`, niezależnie od browser cookie. CORS pozostaje zewnętrzną warstwą z jawnymi origins i preflight bez sesji.
- Publiczny statyczny HTML/JS/CSS/assets umożliwia render loginu; dokumentacja/schema FastAPI jest chroniona w `/api/docs`, `/api/redoc`, `/api/openapi.json`. Frontend sprawdza sesję przed montowaniem domain UI i przed ordinary requests.
- Globalny 401 lub logout odmontowuje aplikację, usuwa lokalny stan chronionych danych i zatrzymuje domain polling. Expiry pokazuje „Sesja wygasła. Zaloguj się ponownie.”; nowe logowanie ponownie otwiera aplikację. Wersjonowanie odpowiedzi i dekodowania JSON/blob odrzuca stare dane i stare 401 po nowym loginie. Błąd logout nie przywraca lokalnych danych.
- 8A3 nie zmienia schematu ani konfiguracji wdrożenia; Alembic head pozostaje `20261006_07`. Dane raw events/corrections/pay pozostają bez zmian.
- `user_sessions`: ID, user_id, unikalny SHA-256 tokenu, created_at/last_seen_at/expires_at/revoked_at UTC. Każdy login tworzy nowy token (256 bitów CSPRNG), przechowywany tylko w host-only HttpOnly cookie. Brak transparentnej rotacji to zamierzona polityka 8A2; rotacja/recovery należy do 8B1.
- Sesje: dokładnie 30 dni idle od ostatniego zapisanego `last_seen_at`, maksymalnie 180 dni od początkowego loginu. Wspólny walidator chroni `/auth/me` i ordinary API; ordinary API wyłącznie waliduje sesję bez zmiany `last_seen_at`, a tylko deliberate `/api/auth/me` heartbeat odnawia idle najwyżej raz na godzinę; niezapisana końcowa aktywność może być późniejsza o mniej niż godzinę. Zalogowana widoczna karta sprawdza sesję co 5 minut i po odzyskaniu focus; HA nie dotyka sesji. Hidden domain polling nie odnawia idle. Anonimowy gate nie wykonuje background domain requests.
- Logout odwołuje sesję w bazie i usuwa cookie; expired/revoked/invalid token jest odrzucany. Rehash Argon2 po poprawnym loginie ma warunek zachowania poprzedniego hasha.
- Persistent cookie: HttpOnly, SameSite=Lax, Path=/, lifetime 180 dni, Secure domyślnie true w kodzie. Instalator/updater wymagają jawnego `SESSION_COOKIE_SECURE`: false wyłącznie dla obecnego LAN HTTP, true obowiązkowo dla HTTPS. Migracja `20261006_07` dodaje wyłącznie pustą tabelę sesji.

- Fundament tożsamości: izolowane `users` (ID, unikalny kanoniczny username, Argon2id hash, created_at UTC), bez ról, publicznej rejestracji i bez powiązań z danymi pracy.
- Login ma 3–64 znaki ASCII (pierwszy alfanumeryczny, dalej litery/cyfry/`._-`); otaczające spacje ASCII są usuwane, litery zamieniane na małe. Wspólny helper i ograniczenia SQLite chronią jednoznaczność loginu.
- Jawne `sudo /opt/work-tracker/deploy/create_user.sh NAZWA` tworzy konto z ukrytym dwukrotnym hasłem jako service user, wyłącznie w istniejącej bazie wskazanej przez produkcyjny env/manifest; kontroluje właściciela i uprawnienia. Powtórzenie nie nadpisuje konta.
- `argon2-cffi` zapewnia Argon2id (64 MiB, 3 iteracje, 4 lanes), weryfikację i możliwość późniejszego rehash. Migracja `20261006_06` dodaje tylko pustą tabelę `users`, bez zmiany danych domenowych i bez default credentials.
- Sam 8A1 tworzył wyłącznie fundament identity; obowiązkowe sesje UI/API są teraz aktywowane przez 8A3.
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
- Ignorowane eventy i automatycznie pominięte krótkie wizyty są domyślnie ukryte, również całe dni zawierające wyłącznie takie wpisy. Opcja `Pokaż ignorowane i automatycznie ukryte wydarzenia` przywraca audyt i zachowuje istniejącą preferencję w `localStorage`; ręczne ignorowanie nadal można cofnąć.
- Canonical pairing oznacza zakończone, jednoznaczne pary nietkniętych eventów Home Assistant o dokładnym czasie UTC ≤ 5 minut jako `suppressed_short_visit`. Oba raw eventy i dokładny czas pozostają w API/audycie, bez zmian bazy, backfillu czy automatycznych korekt.
- Krótkie wizyty nie są pracą ani anomalią: nie zwiększają czasu, dni pracy, liczby problemów ani płacy i nie trafiają do CSV/PDF. Po rzeczywistym wyjściu dashboard raportuje `outside`; przed nim działa zwykła otwarta zmiana. Manualna granica lub aktywny timestamp override wyłącza suppression.
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
- Włączenie widoku audytowego i każdy udany poll dashboardu przy włączonym audycie odświeżają dodatkowo tylko `work-summary`, aby pokazać krótkie wizyty zakończone pomiędzy pollami mimo niezmienionego statusu i sum. `pay-summary` oraz zwykły widok zachowują ograniczenie do istotnych zmian dashboardu.
- Odświeżanie audytu działa w tle po załadowaniu miesiąca: zachowuje summary i rozwinięte dni do udanej odpowiedzi, również przy błędzie. Polle podczas trwającego requestu nie zastępują ani nie abortują go; zmiana miesiąca lub jawne retry anuluje nieaktualny request i uruchamia zwykłe ładowanie.
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
- Po prawidłowym zapisaniu odpowiedniego raw eventu produkcyjny Home Assistant wysyła użytkownikowi interaktywne powiadomienie Work Trackera.
- Korektę godziny można wykonać bezpośrednio z powiadomienia; korzysta ona z istniejącej warstwy `timestamp_override`, a raw event pozostaje immutable.
- Future effective entry zachowuje semantykę zaprojektowaną w Phase 7B: przed effective startem nie rozpoczyna naliczania czasu, a od effective startu staje się aktywnym wejściem.
- Warstwa powiadomień Home Assistanta ma skonfigurowany kill switch niezależny od podstawowego ingestion raw events.
- Produkcyjny test end-to-end z rzeczywistego zone triggera zakończył się powodzeniem: raw event został zapisany, interaktywne powiadomienie dotarło na telefon, korekta została przyjęta jako `timestamp_override`, a poprawiony effective state był widoczny w Work Tracker UI.
- Ręczny test Home Assistant → API → baza → frontend zakończył się powodzeniem.

Aktualne endpointy:

- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/logout`
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

- Środowisko: Debian 13 LXC na Proxmox, obecnie wyłącznie w sieci LAN; brak publicznego UI, Cloudflare Tunnel i Google Calendar integration.
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
- Effective event stream powstaje z raw events i korekt, przekazuje pochodzenie i informację o aktywnym timestamp override, a następnie trafia do wspólnego canonical pairing z polityką krótkich wizyt.
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
- Zwykła prezentacja UI, PDF i CSV nie pokazuje sekund ani nie zaokrągla czasu do najbliższej minuty; jawny audyt krótkich wizyt pokazuje dokładny czas w minutach i sekundach. Baza, API domenowe, obliczenia czasu i wynagrodzenia zachowują pełną precyzję sekundową.
- Backend nie sumuje sesji rozliczanych w różnych walutach; taki miesiąc zwraca jednoznaczny błąd.
- Dashboard otrzymuje nazwę strefy IANA przeglądarki, aby poprawnie określić lokalne „dzisiaj” i bieżący miesiąc; wszystkie czasy trwania nadal wynikają z chwil UTC.
- Status `working` wymaga dokładnie jednego terminalnego `missing_exit` nie starszego niż 16 godzin. Pojedyncze future `entry` przy jednoznacznym bieżącym stanie `outside` pozostaje oczekującym startem; future `exit`, konfliktujące przyszłe eventy, terminalny `duplicate_entry`, `ambiguous_timestamp`, wiele otwartych wejść lub wejście starsze niż 16 godzin daje status `ambiguous`.
- Dzisiejszy efektywny czas dodaje otwartą zmianę wyłącznie do lokalnej daty jej wejścia. Miesięczna płaca obejmuje tylko zakończone sesje `valid`.
- Eksporty historyczne zachowują własność dnia/miesiąca z `WorkTimeItem.local_date`; nie używają odmiennych reguł strefowych live dashboardu.
- Raport obejmuje wyłącznie effective events, dlatego ignorowanie, korekta timestampu i manualne eventy automatycznie wpływają na eksport bez zmiany raw events.
- Anomalie trafiają do raportu informacyjnie i mają puste pola finansowe w CSV; nie są opłacane ani zamieniane na zgadywane sesje.

## Next implementation target

**Phase 8B1 — Browser security hardening**

8A1, 8A2 i 8A3 są ukończone. Login jest obowiązkowy dla ordinary UI/API, z centralną default-deny ochroną i sześcioma dokładnymi wyjątkami. Następne prace obejmują full CSRF, rate limiting, response/cache hardening i race-safe transparentną rotację/recovery; żadne z nich nie zostało dodane w 8A3.

Home Assistant pozostaje niezależny: ingestion i korekty wymagają `X-Webhook-Token`, nigdy browser cookie. 8B1 obejmie CSRF, brute-force protection i race-safe transparentną rotację/recovery; 8B2 przygotuje zaufany reverse proxy/HTTPS. Przez cały Phase 8 produkcja pozostaje LAN-only.

Po Phase 8 planowane są kolejno Phase 9 (publiczny HTTPS dostęp do całej aplikacji przez Cloudflare Tunnel pod dedykowaną, jeszcze nieustaloną subdomeną) i Phase 10 (asymetryczna, dwukierunkowa integracja z dedykowanym Google Calendar). To wyłącznie kierunek rozwoju, nie stan wdrożenia. Prywatny origin ma pozostać w LAN bez przekierowania portów; przez cały Phase 8 produkcja pozostaje LAN-only, a publiczny dostęp całego UI może zostać uruchomiony dopiero po końcowym hardening review.

Calendar ma być projekcją poprawnych zakończonych sesji i dodatkowym interfejsem korekt ich godzin, nie źródłem raw events ani niezależną bazą czasu pracy. Nadal obowiązuje `raw events → corrections → effective events → canonical pairing → valid sessions → pay/dashboard/reports`. Zmiana godzin managed Calendar event ma docelowo korzystać z audytowalnej warstwy korekt; szczegóły identyfikacji sesji i ręcznych granic pozostają do zaprojektowania w Phase 10.

## Known intentional limitations

- jedna praca i jedna aktywna lokalizacja,
- brak edycji i usuwania historycznych stawek,
- brak nadgodzin, dodatków, podatków i przeliczeń walut,
- brak live estymacji wynagrodzenia dla niezakończonej zmiany,
- brak WebSocket/SSE; dashboard celowo korzysta z prostego pollingu,
- full CSRF, login rate limiting i transparentna rotacja/recovery sesji pozostają odłożone do 8B1,
- brak eksportu XLSX/Excel,
- brak publicznego dostępu do aplikacji w obecnym wdrożeniu (plan: Phase 9 po Phase 8),
- brak integracji Google Calendar w obecnym wdrożeniu (plan: Phase 10).

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

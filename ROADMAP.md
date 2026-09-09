# Work Tracker — roadmap

Każdy PR implementujący element roadmapy powinien:

1. aktualizować `PROJECT_STATUS.md`,
2. aktualizować status odpowiedniego etapu w `ROADMAP.md`,
3. nie implementować kolejnych etapów bez wyraźnego zakresu PR.

Statusy używane w dokumencie: `DONE`, `IN PROGRESS`, `NEXT`, `PLANNED`, `DEFERRED`.

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

**Status: DONE**

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

- [x] Poprawna para `entry → exit` tworzy jedną sesję z prawidłowym czasem trwania.
- [x] Czas trwania jest liczony na podstawie jednoznacznych chwil UTC, z zachowaniem oryginalnych timestampów i offsetów do prezentacji.
- [x] Wyniki obejmują czas każdego dnia, sumę miesiąca oraz liczbę dni pracy.
- [x] Eventy są przetwarzane chronologicznie; kolejność odbioru HTTP nie zmienia wyniku.
- [x] Każdy typ anomalii jest wykrywany i widoczny, bez zgadywania brakującego czasu pracy.
- [x] Reguła dla sesji przechodzącej przez północ jest jawnie opisana i pokryta testami.
- [x] Frontend pokazuje sesje/dni i podsumowanie bieżącego miesiąca oraz zachowuje obsługę ładowania, braku danych i błędu.
- [x] Testy obejmują zwykłe sesje, anomalie, eventy poza kolejnością, północ oraz zmianę czasu letniego/zimowego.
- [x] Rekordy raw events nie są aktualizowane ani usuwane przez logikę wyliczeń.
- [x] `PROJECT_STATUS.md` i status Phase 1 zostają zaktualizowane po zakończeniu etapu.

## Phase 2 — Monthly navigation and summaries

**Status: DONE**

Zakończony zakres:

- [x] Nawigacja do poprzedniego i następnego miesiąca z poprawną obsługą granic roku.
- [x] Bezpośredni wybór miesiąca i roku bez dodatkowej biblioteki date-picker.
- [x] Szybki powrót do bieżącego miesiąca.
- [x] Adres URL `/?year=YYYY&month=MM` zachowujący wybrany miesiąc po odświeżeniu i umożliwiający udostępnienie widoku.
- [x] Obsługa Back/Forward oraz bezpieczny fallback dla nieprawidłowych parametrów URL.
- [x] Miesięczne podsumowanie czasu, dni pracy, średniego czasu dziennie i liczby problemów.
- [x] Zachowanie widoku dni, sesji i anomalii z Phase 1.
- [x] Stany ładowania, pustego miesiąca, błędu i ponowienia żądania bez prezentowania nieaktualnych danych.
- [x] Ochrona przed nadpisaniem wybranego miesiąca przez spóźnioną odpowiedź API.

### Acceptance criteria

- [x] Widok bez parametrów URL pokazuje bieżący lokalny miesiąc użytkownika.
- [x] Przyciski poprzedniego i następnego miesiąca działają na granicach miesiąca i roku w zakresie API 2000–2100.
- [x] Użytkownik może wybrać konkretny miesiąc oraz wrócić do miesiąca bieżącego.
- [x] Wybrany miesiąc jest synchronizowany z query parameters i odtwarzany po odświeżeniu lub nawigacji Back/Forward.
- [x] Nieprawidłowe parametry są zastępowane bieżącym miesiącem i nie są wysyłane do API.
- [x] Dane pochodzą z `GET /api/work-summary`; frontend nie rekonstruuje sesji z raw events.
- [x] Zmiana miesiąca czyści poprzednie podsumowanie, a nieaktualne requesty są anulowane i ignorowane.
- [x] Widoki loading, error z retry oraz pustego miesiąca są czytelne na desktopie i telefonie.
- [x] Logika obliczeń Phase 1 oraz immutable raw events pozostają bez zmian.

## Phase 3 — Manual corrections

**Status: DONE**

Zakończony zakres:

- [x] Ręczne dodawanie brakującego `entry` lub `exit`.
- [x] Korekta efektywnego timestampu istniejącego raw eventu.
- [x] Ignorowanie błędnego raw eventu bez jego usuwania lub aktualizacji.
- [x] Cofanie korekty przez usunięcie wyłącznie rekordu korekty.
- [x] Oddzielna tabela `work_event_corrections` z jawnymi typami i ograniczeniami integralności.
- [x] Warstwa effective events: raw events + korekty → istniejący kalkulator czasu pracy.
- [x] Widoczne rozróżnienie eventów Home Assistant, skorygowanych, zignorowanych i dodanych ręcznie.
- [x] Korekty uwzględniają offset strefy czasowej oraz mogą przenosić sesje między dniami i miesiącami.
- [x] Korekty od razu wpływają na sesje, anomalie oraz dzienne i miesięczne sumy.

### Acceptance criteria

- [x] Raw events Home Assistant nie są aktualizowane ani usuwane przez żaden endpoint korekt.
- [x] Timestamp correction zachowuje oryginalny timestamp do audytu i używa efektywnego timestampu w obliczeniach.
- [x] Ignored raw event pozostaje widoczny, lecz nie trafia do effective event stream.
- [x] Manual event nie udaje eventu Home Assistant i przechodzi przez zwykłe reguły Phase 1.
- [x] Usunięcie korekty przywraca wynik pozostałych raw events i korekt.
- [x] Nieistniejące raw eventy, błędne typy, naiwne timestampy i sprzeczne korekty są odrzucane.
- [x] Jednoznaczność aktywnej korekty raw eventu jest chroniona w API i bazie danych.
- [x] Przeliczenia po korektach zachowują reguły UTC, próg 16 godzin i przypisanie do efektywnej daty wejścia.
- [x] Migracja zachowuje istniejącą tabelę `work_events` i wszystkie jej dane.
- [x] Frontend oferuje polskie formularze i komunikaty oraz pobiera autorytatywny summary po każdej mutacji.

Raw events otrzymane z Home Assistant nie mogą być bezpowrotnie nadpisywane. Korekty powinny być osobną, możliwą do prześledzenia warstwą danych.

### Post-Phase-3 UI cleanup

- [x] Kompaktowa, domyślnie zwinięta sekcja `Ustawienia`.
- [x] Ignorowane eventy ukryte domyślnie wyłącznie w warstwie prezentacji.
- [x] Zapamiętywane lokalnie ustawienie widoku `Pokaż ignorowane wydarzenia`, pozwalające przywrócić pełny widok audytowy i cofnąć ignorowanie.

## Phase 4 — Pay calculation

**Status: DONE**

### Phase 4A — backend pay-rate history and pay calculation

**Status: DONE**

- [x] Trwała historia stawek z unikalną datą obowiązywania i bez destrukcyjnego nadpisywania wcześniejszych rekordów.
- [x] Domyślna stawka `50,00 PLN/h`, obowiązująca od `1970-01-01`.
- [x] Wybór stawki według lokalnej daty wejścia do poprawnej sesji.
- [x] Dzienne i miesięczne wynagrodzenie liczone z dokładnych sekund i typów `Decimal`.
- [x] Zaokrąglanie wynikowych kwot do dwóch miejsc metodą `ROUND_HALF_UP`.
- [x] API historii stawek oraz backendowego podsumowania wynagrodzenia.
- [x] Brak wynagrodzenia za anomalie i odrzucanie sum obejmujących różne waluty.

### Phase 4B — frontend pay presentation and rate management

**Status: DONE**

- [x] Zarządzanie historią stawek w sekcji `Ustawienia` przez dodawanie kolejnych, niedestrukcyjnych wpisów.
- [x] Prezentacja miesięcznego i dziennego wynagrodzenia z backendowego `pay-summary`.
- [x] Czytelne pokazanie stawek użytych w wybranym miesiącu.
- [x] Kompaktowa informacja o stawce lub zakresie stawek w zwykłym podsumowaniu.
- [x] Niezależne stany ładowania i błędu płac, które nie ukrywają danych czasu pracy.
- [x] Odświeżanie wynagrodzenia po zmianie miesiąca, korekcie czasu i dodaniu historycznej stawki.

Zakres Phase 4A i 4B został zmergowany, wdrożony i ręcznie zweryfikowany na produkcyjnym LXC.

Początkowa implementacja Phase 4 nie obejmuje nadgodzin, dodatków weekendowych, podatków ani premii.

Zakres nadal zakłada jedną lokalizację i jedną pracę.

## Phase 5 — Dashboard and live shift

**Status: DONE**

- [x] Autorytatywny status `W PRACY`, `POZA PRACĄ` lub stan niejednoznaczny wyprowadzany przez backend z effective event stream.
- [x] Godzina rozpoczęcia i lokalnie aktualizowany czas jednoznacznej otwartej zmiany.
- [x] Dzisiejszy zakończony czas oraz efektywny czas z uwzględnieniem poprawnej otwartej zmiany.
- [x] Bieżący miesięczny czas, liczba dni pracy i wynagrodzenie za zakończone sesje.
- [x] `GET /api/dashboard` korzystający z istniejącego parowania i kalkulatora płac.
- [x] Polling backendu co 30 sekund oraz lokalnie aktualizowany timer frontendowy bez zapisywania syntetycznego `exit`; prezentacja pokazuje ukończone minuty.
- [x] Jawne traktowanie duplikatów, sprzecznych timestampów i otwartych zmian ponad 16 godzin jako stanu niejednoznacznego.
- [x] Niezależny błąd dashboardu, który nie ukrywa historii czasu, korekt ani danych płacowych.
- [x] Odświeżanie dashboardu po korektach, ignorowaniu/cofaniu, manualnych eventach i zmianie stawki.

## Phase 6 — Export

**Status: DONE**

- [x] Jeden backendowy model raportu zasilany przez effective events, kanoniczne parowanie oraz historyczne stawki.
- [x] Eksport CSV UTF-8 z BOM i separatorem `;`, zgodny z wybranym miesiącem historycznym; prezentacja czasu ma dokładność ukończonej minuty, a pełne sekundy pozostają w obliczeniach wewnętrznych.
- [x] Raport PDF A4 z podsumowaniem, sesjami, stawkami, kwotami i kompaktową sekcją problemów.
- [x] Wiele sesji jednego dnia pozostaje osobnymi pozycjami raportu.
- [x] Anomalie są widoczne, ale nie zwiększają czasu pracy ani wynagrodzenia.
- [x] Pusty miesiąc zwraca prawidłowy CSV i PDF z zerowym podsumowaniem.
- [x] Przyciski `Pobierz CSV` i `Pobierz PDF` korzystają z miesiąca wybranego w istniejącej nawigacji.
- [x] Standardowy miesiąc mieści się na jednej stronie A4, a większe raporty są automatycznie paginowane z powtarzanym nagłówkiem tabeli.

Eksport zachowuje historyczne reguły `work-summary` i `pay-summary`: sesja należy do daty wejścia zapisanej z oryginalnym/efektywnym offsetem, a czas trwania wynika z chwil UTC. Excel/XLSX pozostaje opcjonalnym, odłożonym rozszerzeniem.

## Phase 7 — Interactive Home Assistant event confirmation

**Status: IN PROGRESS**

Cel: po poprawnym zapisaniu raw eventu `entry` lub `exit` umożliwić użytkownikowi potwierdzenie wykrytej godziny albo jej korektę bezpośrednio w interaktywnym powiadomieniu mobilnym Home Assistanta, bez otwierania Work Trackera lub innej aplikacji.

Backend oraz konfiguracja timezone są gotowe. Interaktywne powiadomienia i
automatyzacja Home Assistanta pozostają do wykonania w Phase 7C.

### Phase 7A — Backend integration

**Status: DONE**

Zrealizowany zakres:

- Istniejący `POST /api/webhook/home-assistant` nadal zapisuje immutable raw event przed uruchomieniem opcjonalnej warstwy powiadomień.
- Odpowiedź webhooka zachowuje backward-compatible pole `id` zapisanego raw eventu i zostaje rozszerzona o dane potrzebne Home Assistantowi do powiadomienia: typ eventu, timestamp, technical location identifier oraz aktualny display alias lokalizacji.
- Home Assistant używa istniejącego pola `id` jako identyfikatora raw eventu; roadmapa nie zakłada breaking rename do `raw_event_id` ani redundantnego drugiego identyfikatora.
- Display alias pochodzi z istniejących application settings i ma fallback do technical location identifier; user-facing nazwa lokalizacji nie jest wpisana na sztywno w Home Assistant.
- Konfiguracja każdej lokalizacji obejmuje osobno canonical technical identifier, opcjonalny user-facing display alias oraz IANA timezone. Location timezone jest trwałą, backend-authoritative konfiguracją przypisaną do canonical location i nie przeciąża znaczenia display aliasu.
- Istniejące application settings zostają rozszerzone tak, aby IANA timezone lokalizacji można było odczytać i skonfigurować obok jej display aliasu. Identyfikator IANA jest walidowany przez backend z użyciem bazy IANA/`ZoneInfo`.
- Display alias zachowuje fallback do technical identifier, natomiast location timezone nie ma zgadywanego fallbacku. Nie pochodzi z Home Assistanta, browser timezone, daty odpowiedzi na powiadomienie ani samego offsetu raw timestampu.
- Phase 7A może wprowadzić małą, addytywną i niedestrukcyjną migrację przechowującą persistent timezone per canonical location; migracja nie zmienia ani nie przepisuje istniejących `work_events` lub `work_event_corrections`.
- Podczas rolloutu dla istniejącej lokalizacji produkcyjnej zostaje skonfigurowane `gabinet_zabki` → `Europe/Warsaw`, bez zmiany canonical identifier i bez zapisywania timezone w raw events.
- Osobny endpoint integracyjny Home Assistanta, zabezpieczony tokenem, umożliwia korektę godziny konkretnego raw eventu.
- Endpoint integracyjny korzysta z istniejącego mechanizmu `timestamp_override`; logika ustawiania timestamp correction jest współdzielona z istniejącą korektą timestampu, a nie zduplikowana.
- Korekta z Home Assistanta nigdy nie aktualizuje ani nie usuwa rekordu `work_events`.
- Potwierdzenie poprawnej godziny nie tworzy korekty ani dodatkowego rekordu audytowego.
- Brak reakcji na powiadomienie nie zmienia raw ani effective event stream.

### Phase 7B — Correction input and live-domain behavior

**Status: DONE**

Zrealizowany zakres:

- Wejście godziny z powiadomienia jest przyjazne dla użytkownika i akceptuje co najmniej formaty `7:45`, `07:45`, `7.45` oraz `07.45`.
- Wpisana godzina jest rozwiązywana względem konkretnego raw eventu, nigdy względem daty ani chwili odpowiedzi na powiadomienie.
- Backend pobiera IANA timezone z backendowej konfiguracji canonical location raw eventu i według tej strefy wyznacza lokalną datę raw instantu. Home Assistant, browser timezone ani offset zapisany w raw timestampie nie zastępują tej konfiguracji.
- Następnie backend rozważa tę lokalną datę oraz sąsiednie daty (`raw_date - 1 day`, `raw_date`, `raw_date + 1 day`) i tworzy dla wpisanej godziny poprawne timezone-aware candidate instants w skonfigurowanej location timezone.
- Nonexistent local time nie tworzy poprawnego kandydata. Dla ambiguous local time backend nie wybiera arbitralnie offsetu; jeżeli po utworzeniu wszystkich poprawnych kandydatów i zastosowaniu pozostałych reguł nie istnieje jeden jednoznaczny poprawny instant, korekta jest odrzucana.
- Backend wybiera jednoznaczny poprawny instant najbliższy raw eventowi, mieszczący się w dozwolonym oknie ±4 godzin. Dzięki temu raw `23:50` z inputem `00:10` oznacza następny dzień `00:10` (+20 minut), a raw `00:10` z inputem `23:50` może oznaczać poprzedni dzień `23:50` (-20 minut).
- Jeżeli location timezone nie jest skonfigurowana, jest nieprawidłowa albo nie istnieje jednoznaczny poprawny candidate instant w dozwolonym oknie, korekta jest odrzucana bez utworzenia lub zmiany danych.
- Ręczna korekta do konkretnej minuty ustawia sekundy na `00`.
- Nieprawidłowe godziny, w tym `24:00`, `7:72` i tekst niebędący godziną, są odrzucane bez utworzenia lub zmiany korekty.
- Korekta z mobilnego powiadomienia może przesunąć timestamp najwyżej o 4 godziny wstecz lub w przyszłość względem raw instantu; większa zmiana jest odrzucana i pozostaje do wykonania w normalnym interfejsie Work Trackera.
- Istniejący konflikt korekty, na przykład `ignore_event`, nie jest automatycznie zastępowany przez timestamp correction.
- Korekta raw `entry` na niedaleką przyszłość jest poprawnym przypadkiem biznesowym, na przykład raw `07:20`, effective `08:00`.
- Przed effective future entry dashboard raportuje `outside`, nie nalicza bieżącego czasu i nie klasyfikuje samej przyszłej godziny jako `ambiguous`.
- Po osiągnięciu effective entry timestamp normalnie powstaje bieżąca otwarta zmiana, bez tworzenia nowego persisted eventu ani syntetycznego `entry` lub `exit`.
- Pozostałe rzeczywiste anomalie zachowują fail-safe behavior zgodny z istniejącymi zasadami dashboardu.

### Phase 7C — Home Assistant integration and safe rollout

**Status: NEXT**

Planowany zakres:

- Home Assistant korzysta z istniejącego pola `id` w odpowiedzi webhooka, aby znać konkretny zapisany raw event; interaktywne powiadomienie może zostać wysłane dopiero po udanym zapisaniu raw eventu.
- User-facing nazwa lokalizacji w powiadomieniu pochodzi z backendowego display aliasu.
- Powiadomienie udostępnia akcję potwierdzenia oraz akcję korekty korzystającą z inline text input, bez konieczności otwierania Work Trackera lub Home Assistanta.
- Odpowiedzi z powiadomień obsługuje osobna automatyzacja lub handler, a nie długotrwałe `wait_for_trigger` w głównej automatyzacji strefowej.
- Action identifiers jednoznacznie wskazują raw event.
- Po odrzuceniu błędnego inputu użytkownik może ponowić próbę z kolejnego inline powiadomienia.
- Notification UX pozostaje opcjonalną warstwą ponad istniejącym ingestion; błąd powiadomienia, brak telefonu, brak reakcji użytkownika lub błąd handlera nie blokuje, nie cofa ani nie modyfikuje prawidłowo zapisanego raw eventu.
- Home Assistant posiada kill switch/helper umożliwiający natychmiastowe wyłączenie wyłącznie interaktywnych powiadomień, bez wyłączania istniejącego zone tracking i webhook ingestion.
- Przed włączeniem interaktywnych powiadomień dla lokalizacji należy zweryfikować, że ma ona poprawnie skonfigurowaną IANA timezone.
- Brak poprawnej location timezone uniemożliwia wyłącznie wymagającą jej time-only correction; nie wyłącza istniejącego raw webhook ingestion ani zwykłego zone tracking.
- Backend jest wdrażany i testowany przed zmianą produkcyjnej automatyzacji strefowej.
- Inline text input jest ręcznie weryfikowany na docelowym telefonie przed podłączeniem realnego zone triggera.
- Właściwy YAML Home Assistanta powstanie dopiero w zadaniu implementacyjnym i nie jest częścią przygotowania roadmapy.

### Acceptance criteria

- [x] Raw Home Assistant events pozostają immutable.
- [ ] Potwierdzenie wykrytej godziny nie tworzy timestamp correction.
- [x] Korekta z powiadomienia korzysta z istniejącego mechanizmu `timestamp_override` i współdzielonej logiki korekt.
- [x] Istniejący webhook zachowuje backward-compatible pole `id`, którego Home Assistant używa jako identyfikatora raw eventu; rozszerzenie odpowiedzi nie wprowadza breaking rename ani redundantnego identyfikatora.
- [x] Location timezone jest trwałą backendową konfiguracją przypisaną do canonical technical location, oddzielną od display aliasu, i może być odczytana oraz skonfigurowana przez application settings.
- [x] Backend waliduje location timezone jako identyfikator IANA z użyciem bazy IANA/`ZoneInfo`; konfiguracja nie ma zgadywanego fallbacku.
- [x] Addytywna migracja location timezone zachowuje istniejące `work_events` i `work_event_corrections` bez zmian.
- [x] Backend normalizuje akceptowany time input do timezone-aware timestampu z sekundami ustawionymi na `00`.
- [x] Dla time-only inputu backend pobiera timezone na podstawie canonical location raw eventu, rozważa lokalną datę raw instantu w tej strefie oraz dzień poprzedni i następny, a następnie wybiera jednoznaczny instant najbliższy raw eventowi w oknie ±4 godzin.
- [x] Offset raw timestampu, browser timezone, Home Assistant ani chwila odpowiedzi na powiadomienie nie zastępują skonfigurowanej location timezone przy tworzeniu kandydatów.
- [x] Data ani chwila odpowiedzi na powiadomienie nigdy nie określa daty korekty.
- [x] Brak lub nieprawidłowa location timezone odrzuca time-only correction bez utworzenia lub zmiany danych.
- [x] Nonexistent i ambiguous local times są obsługiwane fail-safe bez zgadywania; brak jednego jednoznacznego poprawnego candidate instant odrzuca korektę bez zmiany danych.
- [x] Nieprawidłowy input lub brak jednoznacznego poprawnego candidate instant w dozwolonym oknie nie tworzy ani nie zmienia danych.
- [x] Display alias lokalizacji pochodzi z application settings i ma fallback do canonical technical location identifier.
- [x] Zmiana display aliasu nie wymaga edycji automatyzacji Home Assistanta.
- [x] Future effective entry nie nalicza czasu przed swoim timestampem i nie powoduje samoistnie stanu `ambiguous`.
- [ ] Notification failure, brak reakcji lub błąd handlera nie wpływa na zapis raw eventu.
- [ ] Kill switch wyłącza wyłącznie warstwę interaktywnych powiadomień, zachowując zone tracking i webhook ingestion.
- [ ] Poprawna IANA timezone jest zweryfikowana dla lokalizacji przed aktywacją Phase 7C.
- [x] Brak location timezone nie wpływa na podstawowe Home Assistant raw ingestion ani zwykłe zone tracking.
- [x] Funkcja jest pokryta testami backendowymi przed aktywacją integracji Home Assistanta.
- [x] Rollout backendu i jego weryfikacja następują przed zmianą produkcyjnej automatyzacji strefowej.

## Phase 8 — Authentication and hardening

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

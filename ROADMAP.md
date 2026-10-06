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

**Status: DONE**

Cel: po poprawnym zapisaniu raw eventu `entry` lub `exit` umożliwić użytkownikowi potwierdzenie wykrytej godziny albo jej korektę bezpośrednio w interaktywnym powiadomieniu mobilnym Home Assistanta, bez otwierania Work Trackera lub innej aplikacji.

Backend, konfiguracja timezone oraz produkcyjna integracja interaktywnych
powiadomień po stronie Home Assistanta są gotowe.

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

**Status: DONE**

Zrealizowany zakres:

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
- Backend został wdrożony i przetestowany przed zmianą produkcyjnej automatyzacji strefowej.
- Inline text input został ręcznie zweryfikowany na docelowym telefonie podczas rzeczywistego zone triggera.
- Konfiguracja Home Assistanta, handler `mobile_app_notification_action`, `rest_command` i kill switch są utrzymywane ręcznie poza tym repozytorium.

Produkcyjna weryfikacja end-to-end objęła rzeczywisty zone trigger:

`Home Assistant zone event → Work Tracker ingestion → actionable notification → korekta godziny → istniejący timestamp_override → poprawiony effective state widoczny w Work Tracker UI`

W tym przebiegu potwierdzono również zaprojektowane w Phase 7B zachowanie future
effective entry.

### Acceptance criteria

- [x] Raw Home Assistant events pozostają immutable.
- [x] Backendowa semantyka no-op używana przez akcję potwierdzenia nie tworzy timestamp correction.
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
- [x] Warstwa notification pozostaje oddzielona od wcześniejszego zapisu raw eventu i nie zmienia jego semantyki ingestion.
- [x] Kill switch/helper jest skonfigurowany niezależnie od raw ingestion, a notification działa przy konfiguracji pozwalającej na jego wysłanie.
- [x] Poprawna IANA timezone jest zweryfikowana dla lokalizacji przed aktywacją Phase 7C.
- [x] Brak location timezone nie wpływa na podstawowe Home Assistant raw ingestion ani zwykłe zone tracking.
- [x] Funkcja jest pokryta testami backendowymi przed aktywacją integracji Home Assistanta.
- [x] Rollout backendu i jego weryfikacja następują przed zmianą produkcyjnej automatyzacji strefowej.

### Post-Phase-7 validation follow-up

Poniższe dodatkowe ręczne testy regresyjne i operacyjne nie zostały jeszcze
potwierdzone w produkcji. Nie blokują rozpoczęcia Phase 8 i nie zmieniają statusu
wdrożonego Phase 7C; mogą zostać wykonane przy kolejnych naturalnych eventach
produkcyjnych:

- [ ] Kliknąć akcję „Potwierdź” i sprawdzić, że nie powstaje `timestamp_override`.
- [ ] Ustawić kill switch na OFF i sprawdzić, że notification nie przychodzi, podczas gdy raw ingestion nadal działa.
- [ ] Celowo wywołać notification/handler failure albo pozostawić powiadomienie bez reakcji i ręcznie potwierdzić, że raw ingestion pozostaje nienaruszone.

### Post-Phase-7 follow-up — Short-visit suppression

**Status: DONE**

- [x] Wspólna klasyfikacja canonical pairing oznacza jednoznaczną, zakończoną parę nietkniętych eventów Home Assistant o dokładnym czasie UTC ≤ 5 minut jako `suppressed_short_visit`; powyżej progu sesja pozostaje `valid`.
- [x] Manualna granica lub aktywna korekta timestampu dowolnej granicy wyłącza tę politykę. Żadna anomalia nie jest maskowana.
- [x] Oba eventy i dokładne `duration_seconds` pozostają do audytu, bez zmian raw events, automatycznych `ignore_event`, migracji lub backfillu; polityka działa także dla historii.
- [x] Krótka wizyta nie zwiększa czasu pracy, dni, liczby problemów ani płacy; dashboard po rzeczywistym wyjściu wraca do `outside`, a CSV/PDF pomijają ją jako pracę i jako problem.
- [x] Domyślny widok ukrywa wizyty oraz dni zawierające tylko takie wizyty. Zachowana preferencja `Pokaż ignorowane i automatycznie ukryte wydarzenia` przywraca neutralny audyt z eventami, zakresem godzin i czasem w minutach oraz sekundach.
- [x] Włączony audyt odświeża `work-summary` przy kolejnych udanych pollach również dla `outside → outside` z niezmienionym fingerprintem; nie wymusza dodatkowego odświeżenia płac, a zwykły tryb zachowuje dotychczasowe ograniczanie refetches.
- [x] Odświeżanie audytu zachowuje wyrenderowany miesiąc do udanej odpowiedzi i nie zastępuje trwającego requestu przy kolejnych pollach. Zmiana miesiąca lub jawne retry anuluje nieaktualne żądanie; testy pokrywają wolne odpowiedzi, sukces, błąd i ochronę przed spóźnioną odpowiedzią.
- [x] Ingestion i interaktywne powiadomienia nie zmieniają zachowania; otwarte wejście nadal może być trwającą zmianą.
- [x] Testy obejmują progi 299/300/301 s, dokładne sekundy, korekty/manual events, anomalie, eventy poza kolejnością oraz wszystkich odbiorców klasyfikacji.

Przyszła projekcja Google Calendar nadal obejmuje tylko `valid` finalized sessions, więc automatycznie pominięte wizyty nie będą projektowane.

## Phase 8 — Authentication and hardening

**Status: NEXT**

Cel: przygotować całą aplikację i jej dane do bezpiecznego udostępnienia przez Internet. To warunek konieczny Phase 9, a nie samo uruchomienie publicznego dostępu. Produkcja pozostaje LAN-only przez cały Phase 8.

### Architecture and rollout invariants

- Uwierzytelnianie człowieka i integracji maszynowych pozostaje rozdzielone. Sesja użytkownika chroni zwykły UI/browser API, natomiast Home Assistant zachowuje własny `X-Webhook-Token` i nie może wymagać interaktywnego logowania ani cookie użytkownika.
- Phase 8 nie zmienia domenowego pipeline'u czasu pracy: `raw events → corrections → effective events → canonical pairing → valid sessions → pay/dashboard/reports`.
- Ochrona browser API ma być domyślnie zamknięta po aktywacji, z małą jawną listą wyjątków dla endpointów integracyjnych oraz minimalnego health checku. Nie utrzymywać rozproszonej listy "chronionych endpointów", którą łatwo pominąć przy dodawaniu nowych tras.
- Sesje użytkowników mają być server-side i odwoływalne. Przeglądarka przechowuje wyłącznie nieprzewidywalny identyfikator w `HttpOnly` cookie; nie używać długowiecznego bearer JWT ani tokenu auth w `localStorage` jako podstawowego mechanizmu sesji.
- UX ma preferować długowieczne zaufane urządzenia: prawidłowo zalogowany telefon lub komputer powinien pozostawać zalogowany przez okres liczony w miesiącach, z bezpiecznym odnawianiem/rotacją i możliwością unieważnienia sesji po stronie serwera. Dokładne limity idle/absolute lifetime należy ustalić i udokumentować w Phase 8A2 zamiast przypadkowo przyjmować krótki timeout.
- Publiczny tryb musi używać cookies `Secure`; LAN-only development/rollout przed Phase 9 musi mieć jawny, kontrolowany sposób testowania bez obniżania docelowych internetowych defaults.
- Phase 8 jest wdrażany etapowo. 8A1 i 8A2 mają nie przełączać istniejącego browser API na obowiązkowy login. Dopiero osobny 8A3 aktywuje enforcement po przygotowaniu użytkowników, sesji, UI i testów.
- Każda zmiana routingu/authentication musi regresyjnie potwierdzić, że Home Assistant nadal może bez sesji użytkownika zapisać raw `entry`/`exit` oraz wykonać token-protected timestamp correction.
- Nie uruchamiać Cloudflare Tunnel, publicznego DNS ani publicznego hosta w Phase 8. Public exposure jest wyłącznie zakresem Phase 9 po końcowym hardening review.

### Phase 8A1 — Identity foundation

**Status: NEXT**

Zakres:

- Minimalny model użytkownika dla małej, jawnie dopuszczonej grupy; bez publicznej rejestracji i bez rozbudowanego RBAC.
- Bezpieczne hashowanie haseł algorytmem przeznaczonym do password hashing; plaintext passwords ani odwracalne hasła nie mogą trafić do bazy, logów lub repozytorium.
- Kontrolowany bootstrap pierwszego użytkownika/administratora odpowiedni dla obecnego self-hosted deploymentu; sekrety poza Git i checkoutem.
- Migracja wyłącznie addytywna i niedestrukcyjna względem istniejących danych czasu pracy, korekt, stawek i ustawień.
- Testy modelu, walidacji credentials i bootstrapu.
- **Brak enforcementu logowania na istniejącym UI/API w tym kroku.** Produkcyjne zachowanie Work Trackera i Home Assistant ingestion pozostaje takie jak przed 8A1.

### Phase 8A2 — Server-side sessions and login UX

**Status: PLANNED**

Zakres:

- Backendowy login/logout i server-side session store powiązany z użytkownikiem.
- Wysokoentropijny losowy session token; w trwałym store przechowywać reprezentację, która nie ujawnia używalnego bearer tokenu przy samym odczycie bazy.
- Cookie `HttpOnly`, odpowiednie `SameSite`, ścieżka i pozostałe atrybuty sesyjne; docelowo `Secure` w HTTPS.
- Długowieczna sesja dla zaufanego urządzenia, z bezpiecznym odnawianiem/rotacją oraz jawnymi idle/absolute limits dobranymi pod UX "zaloguj raz, używaj miesiącami".
- Możliwość unieważnienia bieżącej sesji i konstrukcja pozwalająca później unieważnić wszystkie sesje użytkownika.
- Minimalny polski ekran logowania i poprawna obsługa stanu unauthenticated/expired session.
- Testy obejmujące utworzenie, odnowienie/rotację, wylogowanie, expiry i odrzucenie nieprawidłowego/revoked tokenu.
- **Nadal bez globalnego enforcementu na istniejącym browser API.** Ten cutover należy do 8A3.

### Phase 8A3 — Protect browser UI and API

**Status: PLANNED**

Zakres:

- Włączenie wymagania poprawnej sesji dla zwykłego UI oraz API odczytującego lub zmieniającego dane pracy, korekty, stawki, raporty i ustawienia.
- Ochrona ma działać default-deny dla browser-facing API, tak aby nowy endpoint nie stał się anonimowy tylko dlatego, że autor zapomniał dopisać osobną dependency.
- Jawna mała lista wyjątków obejmuje wyłącznie wymagane machine endpoints oraz minimalny health check. Home Assistant `POST /api/webhook/home-assistant` i `POST /api/webhook/home-assistant/correction` zachowują własny token i nie akceptują sesji użytkownika jako zamiennika.
- Frontend po `401` przechodzi do login UX bez utraty domenowych danych; zalogowanie przywraca zwykłe działanie dashboardu, miesięcy, korekt, płac, ustawień i eksportów.
- Regresja HA musi być przetestowana bez browser cookie: raw ingestion oraz HA correction nadal działają z prawidłowym `X-Webhook-Token`; brak/nieprawidłowy token nadal jest odrzucany.
- Rollout ma zachować możliwość szybkiego wycofania zmian przez istniejący updater/rollback bez utraty danych.

### Phase 8B1 — Browser security hardening

**Status: PLANNED**

Zakres:

- Ochrona CSRF dla cookie-authenticated operacji zmieniających stan; nie polegać wyłącznie na CORS.
- Spójna polityka CORS dla rzeczywistych originów oraz brak przypadkowego szerokiego credentialed CORS.
- Ochrona logowania przed brute force / credential stuffing z limitem, który nie blokuje normalnego domowego użycia.
- Security-sensitive response headers i sensowne cache policy dla danych prywatnych oraz ekranu logowania.
- Regeneracja/rotacja identyfikatora sesji w odpowiednich punktach, brak session fixation oraz bezpieczne zachowanie logout/expiry.
- Testy negatywne dla nieautoryzowanego odczytu, mutacji, CSRF i prób logowania.

### Phase 8B2 — Reverse-proxy and Internet-readiness review

**Status: PLANNED**

Zakres:

- Dostosowanie do docelowej architektury reverse proxy: poprawne rozpoznawanie HTTPS, hosta i adresu klienta wyłącznie z zaufanej ścieżki proxy; bez bezwarunkowego ufania klientowskim `Forwarded` / `X-Forwarded-*`.
- Weryfikacja trusted hosts/proxies, generowania redirectów/URL-i, `Secure` cookies i semantyki client IP w planowanym przepływie Cloudflare Tunnel.
- Sprawdzenie, że endpointy integracyjne zachowują oddzielne uwierzytelnianie i nie stają się publicznie użyteczne przez sam fakt dodania reverse proxy.
- Końcowy security-focused review całego Phase 8 i regresja pełnego UI/API oraz Home Assistant ingestion przed rozpoczęciem Phase 9.
- Produkcja nadal pozostaje LAN-only do czasu jawnego rollout Phase 9.

### Acceptance criteria

- [ ] Istnieje mała, jawnie kontrolowana baza użytkowników bez publicznej rejestracji i bez zbędnego RBAC.
- [ ] Hasła są przechowywane wyłącznie jako bezpieczne password hashes; sekrety i dane sesyjne nie trafiają do repozytorium ani logów.
- [ ] Sesje są server-side, losowe i odwoływalne; przeglądarka nie przechowuje podstawowego auth tokenu w `localStorage`.
- [ ] Zaufane urządzenie może pozostawać zalogowane przez długi okres zgodnie z udokumentowaną polityką sesji, bez częstego wymuszania ponownego loginu, przy zachowaniu możliwości revocation i bezpiecznej rotacji.
- [ ] Cały zwykły UI i browser API wymagają poprawnej sesji; brak anonimowego odczytu lub mutacji danych pracy i płac.
- [ ] Ochrona browser API jest default-deny, a machine-auth exceptions są jawne i minimalne.
- [ ] Home Assistant ingestion i HA correction pozostają niezależne od sesji użytkownika, nadal wymagają własnego `X-Webhook-Token` i są pokryte testami regresyjnymi po aktywacji auth.
- [ ] CSRF, CORS, brute-force protection, session fixation/rotation, logout/expiry i security headers są zweryfikowane testami.
- [ ] Cookies i obsługa sesji mają bezpieczną semantykę w docelowym HTTPS/reverse-proxy układzie; klient nie może sam spoofować zaufanych forwarded headers.
- [ ] Migracje Phase 8 są addytywne/niedestrukcyjne dla istniejących raw events, korekt, stawek, ustawień i historii.
- [ ] Publiczny Tunnel/DNS nie jest aktywowany w Phase 8. Dopiero po końcowym security review i spełnieniu acceptance criteria można rozpocząć Phase 9.

## Phase 9 — Cloudflare public deployment

**Status: PLANNED**

Cel: udostępnić **całą** aplikację Work Tracker przez HTTPS pod dedykowaną subdomeną domeny obsługiwanej przez Cloudflare. Nazwa subdomeny nie jest jeszcze ustalona. Phase 9 następuje po ukończeniu Phase 8; obecny origin pozostaje prywatny.

```text
Internet → Cloudflare (DNS/HTTPS) → Cloudflare Tunnel → prywatny origin Work Trackera w LAN
```

### Phase 9A — Private-origin routing and protection

- Cloudflare Tunnel i DNS dla dedykowanego publicznego hostname; bez router port-forwardingu i bez bezpośredniego publicznego wystawiania LXC.
- HTTPS dla użytkowników; poprawne przekazywanie informacji o schemacie, hoście i adresie klienta przez zaufany proxy path, tak by sesyjne cookies i logi zachowały właściwą semantykę.
- Application authentication z Phase 8 chroni cały zwykły UI/API. Cloudflare Access może być dodatkową warstwą ochrony, a nie substytutem uwierzytelniania aplikacji.
- Zachowanie prywatnej ścieżki LAN dla Home Assistant ingestion niezależnie od publicznego UI; sam publiczny hostname nie powinien automatycznie rozszerzać dostępu do token-protected endpointów integracyjnych. Decyzję o routingu i regułach dla nich trzeba zweryfikować przy wdrożeniu.
- Projekt routingu powinien później dopuścić bardzo wąski, niezależnie uwierzytelniany Google webhook z Phase 10 bez interaktywnego loginu ani wyłączenia ochrony całej aplikacji.

### Phase 9B — Safe rollout and rollback

- Sekrety i tokeny Cloudflare Tunnel poza Git i poza checkoutem; jawne wymagania konfiguracyjne dopiero w implementacyjnym PR, zgodnie z istniejącym deployment manifest i regułami uprawnień.
- Walidacja dostępu, sesji, HTTPS, ograniczenia originu i niezmienionego działania Home Assistant w LAN przed aktywacją publicznego hostname.
- Stopniowa aktywacja z możliwością szybkiego wyłączenia publicznego routingu, zachowaniem backupów SQLite, czystego update/rollback i bez utraty danych.

### Acceptance criteria

- [ ] Phase 8 jest ukończona przed publicznym udostępnieniem całego UI/API.
- [ ] Dedykowany hostname działa przez Cloudflare Tunnel i HTTPS; origin nie ma bezpośredniej publicznej ekspozycji ani router port-forwardingu.
- [ ] Autoryzacja aplikacji, HTTPS-aware cookies, trusted proxy/client IP i ewentualna dodatkowa ochrona Access są sprawdzone end-to-end.
- [ ] Istniejące Home Assistant ingestion i korekty w LAN działają bez zależności od publicznego UI.
- [ ] Publiczny routing można wycofać bez zmiany lub utraty raw events, korekt, płac i konfiguracji.

## Phase 10 — Google Calendar integration

**Status: PLANNED**

Cel: asymetryczna integracja dwukierunkowa po publicznym wdrożeniu HTTPS z Phase 9. Work Tracker pozostaje jedynym źródłem prawdy o czasie pracy: `raw events → corrections → effective events → canonical pairing → valid sessions → pay/dashboard/reports`. Google Calendar jest projekcją **valid finalized sessions** oraz ograniczonym interfejsem zmiany godzin zarządzanych wydarzeń, nie równoległą bazą czasu pracy. Nie tworzy ani nie przepisuje `work_events`.

Docelowo właściciel tworzy dedykowany kalendarz na swoim zwykłym koncie Google i udostępnia go Google service account z prawem do zarządzania wydarzeniami. Może także udostępnić go osobom mającym oglądać lub edytować godziny. Nie zakładamy kalendarza należącego do service account. Credentials/private key service account, Cloudflare Tunnel credentials i sekrety kanałów/webhooków pozostają poza Git i checkoutem; przyszły deployment manifest ma jawnie obsługiwać wymagane sekrety.

### Phase 10A — Stable identity and outbound projection

- Jedna poprawna zakończona sesja `entry 07:58 → exit 16:12` odpowiada jednemu managed Google event `07:58–16:12`, nie dwóm osobnym eventom `entry`/`exit`. Otwarta zmiana bez istniejącej, kanonicznie poprawnej granicy `exit` (raw lub manualnej) nie tworzy sztucznego zakończonego wydarzenia ani syntetycznego `exit`.
- Outbound korzysta z tego samego effective-event stream i canonical pairing co work-summary, pay, dashboard i raporty. Tylko sesje `valid` mogą być projektowane; manual events mogą być granicami takich sesji.
- Zaprojektować trwałą tożsamość/link sesji z obiema granicami, niezależną od zmiennych start/end timestampów i uwzględniającą zarówno raw, jak i `manual_event` boundary. Obecny `WorkTimeItem` nie ma trwałego session ID, a manual effective event nie ma `raw_event_id`; nowy model relacji jest wymaganiem do rozstrzygnięcia, nie częścią obecnego schematu.
- Po stronie Work Trackera zapisać `google_event_id` i stan synchronizacji; po stronie managed Google event umieścić prywatne, wersjonowane extended properties potwierdzające zarządzanie przez WT i pozwalające odtworzyć powiązanie. Nie identyfikować eventu jedynie po godzinach.
- Create/update tego samego managed event po korekcie; undo przywraca aktualny effective stream. Ignore, manual event i zmiana parowania uruchamiają reconciliation do aktualnego zbioru valid sessions. Usunięcie/anomalia sesji nie upoważnia Calendar do zmiany raw danych.
- Awaria Google API nigdy nie blokuje ani nie cofa HA ingestion, korekty, zapisu czasu pracy ani płac. Planować trwałą asynchroniczną warstwę sync (np. SQLite outbox/job queue z retry) oraz okresowe porównywanie valid WT sessions ↔ managed Google events.

### Phase 10B — Narrow inbound time editing

- Inbound dotyczy wyłącznie istniejącego, zweryfikowanego managed event: zmiana startu odpowiada korekcie jego `entry`, zmiana końca korekcie `exit`, zmiana obu wymaga atomowej walidacji i zapisu obu granic. Porównywać pełne timezone-aware UTC instants, nie stringi ani same minuty; po korektach ponownie zastosować canonical pairing/fail-safe rules.
- Dla granicy powiązanej z raw eventem używać istniejącego audytowalnego `timestamp_override`, nigdy nie aktualizować `work_events`. Brak różnicy czasu to no-op; powrót dokładnie do raw instantu powinien usuwać istniejący timestamp override (undo), o ile nie ma konfliktującego typu korekty. Konflikty (np. `ignore_event`) nie są automatycznie zastępowane.
- Pełny timezone-aware timestamp z Google nie podlega HA-specific oknu ±4 godzin, które dotyczy wyłącznie time-only correction UX. Nadal musi przejść walidację domenową. Jeśli zmiana zrywa powiązanie granic, zmienia canonical pairing albo nie daje valid session, początkowa bezpieczna polityka to odrzucenie/odwrócenie edycji Calendar bez zgadywania nowej sesji; szczegóły rozstrzygnąć przed implementacją.
- Dla sesji z manual-event boundary outbound jest możliwy, lecz pierwsza wersja inbound może edytować tylko te granice, które wskazują raw event. Edycję manual boundary należy jawnie odrzucić/odwrócić albo pozostawić unsupported, dopóki nie powstanie niedestrukcyjna, audytowalna semantyka. Nie udawać, że `timestamp_override` działa na `manual_event`.
- Zmiany title, description i color nie zmieniają danych pracy. Zwykły event utworzony w Calendar nie tworzy work session. Usunięcie managed event nie oznacza `ignore_event` ani usunięcia czasu pracy; reconciliation powinien móc go odtworzyć.
- Zaprojektować wiarygodny audyt pochodzenia **nowych** korekt (`web`, `home_assistant`, `google_calendar`), z `legacy/unknown` dla historycznych rekordów bez danych o pochodzeniu. Przed implementacją ustalić, czy `origin` oznacza źródło ostatniej aktywnej wartości, czy potrzebny jest osobny append-only audit log zmian i undo. Nie przypisywać historycznym rekordom fikcyjnych autorów; Google API nie musi pozwolić ustalić konkretnej osoby edytującej event.

### Phase 10C — Change detection, security and recovery

- Google Calendar `events.watch` / push notifications przez bardzo wąski publiczny HTTPS callback (np. logicznie `/api/integrations/google-calendar/webhook`) dostępny przez Cloudflare Tunnel, następnie pobranie zmian przez Calendar API z incremental `syncToken`. Push jest tylko sygnałem zmiany, nie pełnym stanem eventu.
- Trwale przechowywać wymagane channel ID, resource ID, token, wygaśnięcie i sync metadata; odnawiać wygasające watch channels, obsługiwać unieważnienie `syncToken` przez bezpieczny pełny sync i uruchamiać okresowe reconciliation po utraconych powiadomieniach.
- Normalny UI/API pozostaje chroniony według Phase 8/9. Jeśli Cloudflare Access jest użyty, wyłącznie callback path może mieć ściśle ograniczony wyjątek od interaktywnego Access; nie wolno wyłączać ochrony całej aplikacji. Sam callback musi weryfikować własny zapisany channel/resource/token i odrzucać nieznane lub błędne powiadomienia; przejście przez Cloudflare nie jest autoryzacją.
- Sync jest idempotentny i zapobiega pętli: po Google `16:12 → 15:45` i zapisaniu efektywnej korekty `15:45`, outbound porównuje aktualne UTC instants i wykonuje no-op, jeśli managed event już odpowiada WT.
- Rollout/rollback bez utraty raw events lub korekt; failures, ponowienia, duplikaty i opóźnione powiadomienia nie mogą blokować domenowego zapisu ani powodować oscylacji danych.

### Acceptance criteria

- [ ] Właścicielem dedykowanego kalendarza jest zwykłe konto użytkownika; service account ma tylko wymagany dostęp, a jego credentials i pozostałe sekrety są poza checkoutem.
- [ ] Po udanym sync każda valid finalized session ma dokładnie jedno trwałe powiązanie z managed Google event, również przy korekcie godzin; open shift i anomalie nie są publikowane jako zakończone sesje.
- [ ] Outbound działa z effective stream/canonical pairing, obejmuje poprawne sesje z manual events i nie wpływa na zapis domenowy przy awarii Google API.
- [ ] Edycja start/end wspieranych raw boundaries tworzy, aktualizuje lub cofa wyłącznie audytowalne korekty, atomowo przy zmianie obu; raw events pozostają immutable. Manual boundaries mają jawną bezpieczną politykę bez destrukcyjnego obejścia.
- [ ] Zmiany nieczasowe i zwykłe eventy Calendar nie tworzą czasu pracy; delete managed event nie ignoruje raw eventu, a reconciliation potrafi odtworzyć projekcję.
- [ ] Callback jest osiągalny przez HTTPS bez interaktywnego logowania Google, lecz wąsko routowany i samodzielnie uwierzytelniany; pełny UI/API pozostaje chroniony.
- [ ] Watch renewal, incremental sync, reset po nieprawidłowym sync tokenie, retry, reconciliation, idempotencja i ochrona przed pętlą są pokryte testami.
- [ ] Pochodzenie nowych korekt jest audytowalne bez fałszowania historii lub deklarowania nieznanej tożsamości edytora Google jako pewnej.

Materiały do weryfikacji w implementacyjnym PR: [Google push notifications](https://developers.google.com/workspace/calendar/api/guides/push), [Google incremental sync](https://developers.google.com/workspace/calendar/api/guides/sync), [Google extended properties](https://developers.google.com/workspace/calendar/api/guides/extended-properties), [Cloudflare Tunnel routing](https://developers.cloudflare.com/tunnel/routing/) i [Cloudflare Access policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/).

## Deferred / not planned now

**Status: DEFERRED**

- druga praca,
- wiele miejsc pracy (`multiple workplaces`),
- obsługa wielu użytkowników poza małą, jawnie dopuszczoną grupą z Phase 8,
- zaawansowane role i uprawnienia,
- integracje payroll/accounting.

> **Multiple workplaces / second job is intentionally deferred and should not be implemented unless the roadmap is explicitly changed.**

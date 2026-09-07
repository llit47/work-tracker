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
- [x] Polling backendu co 30 sekund oraz sekundowy timer frontendowy bez zapisywania syntetycznego `exit`.
- [x] Jawne traktowanie duplikatów, sprzecznych timestampów i otwartych zmian ponad 16 godzin jako stanu niejednoznacznego.
- [x] Niezależny błąd dashboardu, który nie ukrywa historii czasu, korekt ani danych płacowych.
- [x] Odświeżanie dashboardu po korektach, ignorowaniu/cofaniu, manualnych eventach i zmianie stawki.

## Phase 6 — Export

**Status: NEXT**

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

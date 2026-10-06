# Work Tracker

Prosta aplikacja obecnie działająca lokalnie w sieci LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy. Backend przyjmuje zabezpieczone webhooki Home Assistanta, zapisuje każde zdarzenie w SQLite i wylicza sesje pracy bez zmiany danych źródłowych. Frontend React pokazuje bieżący status pracy, miesięczne sesje i anomalie, korekty, wynagrodzenie oraz eksporty CSV/PDF.

Obecny instalator i obecne wdrożenie nie konfigurują publicznego dostępu, domeny, proxy, tunelu ani HTTPS.

## Planned direction (not implemented)

Po ukończeniu authentication/hardening (Phase 8) planowany jest dostęp do całej aplikacji pod dedykowaną, jeszcze nieustaloną subdomeną: Internet → Cloudflare → Cloudflare Tunnel → prywatny origin Work Trackera w LAN (Phase 9), bez przekierowania portów routera i bez bezpośredniego wystawienia LXC. Następnie planowana jest integracja z dedykowanym Google Calendar (Phase 10): projekcja wyłącznie poprawnych zakończonych sesji oraz ograniczone korekty godzin zarządzanych wydarzeń z powrotem w Work Trackerze. Calendar nie będzie źródłem raw events. Szczegóły i kryteria etapów są w `ROADMAP.md`; poniższe instrukcje dotyczą wyłącznie aktualnego wdrożenia LAN-only.

## Quick install

Na minimalnym Debianie lub Ubuntu trzeba najpierw zainstalować `curl`. Jeśli jesteś zalogowany jako `root`:

```bash
apt update && apt install -y curl
curl -fsSL https://raw.githubusercontent.com/llit47/work-tracker/main/install.sh | bash
```

Jeśli pracujesz jako zwykły użytkownik z dostępem do `sudo`:

```bash
sudo apt update
sudo apt install -y curl
curl -fsSL https://raw.githubusercontent.com/llit47/work-tracker/main/install.sh | sudo bash
```

Skrypt pobierany przez `curl` jest wykonywany jako root. Bezpieczniejszy wariant pozwalający najpierw przeczytać skrypt:

```bash
curl -fsSL https://raw.githubusercontent.com/llit47/work-tracker/main/install.sh -o /tmp/work-tracker-install.sh
less /tmp/work-tracker-install.sh
sudo bash /tmp/work-tracker-install.sh
```

Instalator nie nadpisze istniejącego `/opt/work-tracker`. Instaluje wymagane pakiety, tworzy użytkownika systemowego, buduje frontend, wykonuje migracje i uruchamia usługę systemd.

## First configuration

Instalator pyta interaktywnie o:

- adres nasłuchu, domyślnie `0.0.0.0`,
- port backendu, domyślnie `8000`,
- token webhooka (minimum 32 znaki) lub zgodę przez pozostawienie pustej wartości na jego bezpieczne wygenerowanie,
- originy CORS, jeśli frontend ma działać z innego originu,
- `SESSION_COOKIE_SECURE`: `false` wyłącznie dla obecnego zaufanego LAN HTTP; `true` obowiązkowo dla HTTPS.

Wygenerowany token nie jest wyświetlany. Zostaje zapisany w `/etc/work-tracker/work-tracker.env`, dostępnym tylko dla roota i grupy usługi. Baza zawsze znajduje się poza repozytorium pod `/var/lib/work-tracker/work_tracker.db`.

## Access in LAN

Przy domyślnym `APP_HOST=0.0.0.0` aplikacja nasłuchuje na interfejsach serwera i jest dostępna pod:

```text
http://<adres-IP-serwera-w-LAN>:8000
```

FastAPI serwuje zbudowany frontend z `frontend/dist`, więc osobny Vite, nginx ani reverse proxy nie są potrzebne. CORS nie dotyczy webhooka Home Assistanta; jest potrzebny tylko wtedy, gdy przeglądarkowy frontend pochodzi z innego originu.

Instalator nie otwiera firewalla, nie konfiguruje routera, publicznego IP, tunelu ani HTTPS. Dostęp należy pozostawić wyłącznie w zaufanej sieci LAN.

## Update

```bash
sudo /opt/work-tracker/update.sh
```

Updater wymaga czystego repozytorium i dostępu do gałęzi `main`. Przed zmianami wykonuje spójny backup SQLite, następnie pobiera kod przez fast-forward, aktualizuje zależności, instaluje frontend według `package-lock.json`, wykonuje build i migracje oraz restartuje usługę. W razie błędu po zmianie commita automatycznie przywraca poprzedni commit, zależności, frontend i unit systemd. Jeżeli migracja już się rozpoczęła, najpierw zatrzymuje usługę i odtwarza bazę z backupu. Backup nie jest usuwany. Przy niepełnym rollbacku updater nie próbuje uruchamiać usługi i wyświetla wyraźne ostrzeżenia oraz instrukcje diagnostyczne; ostrzega też osobno, jeśli samego zatrzymania usługi nie udało się potwierdzić.

Nowe wymagane ustawienia są definiowane w `deploy/config.manifest`. Updater dopisuje wyłącznie brakujące wymagane klucze, pyta o ich wartości i pokazuje bezpieczne wartości domyślne. Nie zmienia istniejących wartości, nie usuwa starszych lub nieznanych wpisów i nigdy nie wypisuje sekretów. Jeśli nie ma nowych wymaganych kluczy, aktualizacja nie zadaje pytań konfiguracyjnych.

## Utworzenie pierwszego użytkownika (Phase 8A1)

Po instalacji lub aktualizacji, która wykonała migrację `20261006_06`, uruchom
na serwerze w interaktywnym terminalu:

```bash
sudo /opt/work-tracker/deploy/create_user.sh wlasciciel
```

Polecenie uruchamia zainstalowany moduł `app.manage create-user` jako użytkownik
systemowy `work-tracker`, również gdy wywołuje je root. Wyświetla docelową bazę
i dwukrotnie prosi o hasło z ukrytym wpisywaniem. Hasło musi mieć 15–1024 znaki;
nie jest przycinane ani normalizowane, może zawierać spacje i Unicode, ale nie
znak NUL. Nie podawaj hasła w argumentach, zmiennych środowiskowych ani pliku.
Brak terminala, różne hasła lub nieprawidłowy input przerywają operację.

Nazwa użytkownika ma 3–64 znaki ASCII: litery, cyfry, `.`, `_`, `-`, zaczyna się
literą lub cyfrą. Otaczające spacje ASCII są usuwane, litery są zamieniane na
małe. `Wlasciciel` i `wlasciciel` oznaczają ten sam unikalny login. Inne białe
znaki oraz znaki spoza ASCII są odrzucane. Te same reguły obowiązują przy logowaniu przez `normalize_username`.

Moduł czyta `DATABASE_URL` wyłącznie z `/etc/work-tracker/work-tracker.env` i
wymaga wspieranej przez obecny manifest wartości
`sqlite:////var/lib/work-tracker/work_tracker.db`. Ignoruje odziedziczony
`DATABASE_URL` i developerski `.env`; nie korzysta z domyślnej lokalnej bazy.
Otwiera SQLite w trybie `mode=rw`: brak istniejącego pliku kończy się błędem,
bez utworzenia innej bazy. Wymaga właściciela i grupy `work-tracker` dla bazy
oraz katalogu danych, bez zapisu grupy i bez dostępu innych użytkowników
(standardowo katalog `0750`, baza `0640`). Nie naprawia uprawnień automatycznie;
przy błędzie operator powinien sprawdzić je przed ponowieniem. Zapis odbywa się
z `umask 0027` jako użytkownik usługi, więc root nie tworzy sidecarów SQLite.

Kolejnego użytkownika tworzysz tą samą komendą z inną nazwą. Ponowienie dla
istniejącego loginu zwraca błąd i nie zmienia hasła ani rekordu użytkownika.
Instalator, updater i start aplikacji nie tworzą kont ani nie pytają o hasła.
W bazie `users` są tylko ID, kanoniczny login, hash i czas utworzenia UTC;
nie ma roli administratora ani powiązań z danymi pracy. Hasła są hashowane
przez `argon2-cffi` (Argon2id, profil RFC 9106 low-memory: 64 MiB, 3 iteracje,
4 lanes). Biblioteka tworzy losowy salt i zapisuje parametry w hashu;
`password_needs_rehash` umożliwia aktualizację parametrów po poprawnej
weryfikacji podczas loginu; warunkowy zapis nie nadpisuje równoległej zmiany hasha. Zależność jest instalowana przez zwykłe `pip install backend`
w instalatorze/updaterze. Nie potrzeba nowego sekretu ani peppera.

## Obowiązkowe logowanie i sesje (Phase 8A3)

Zwykły UI i API wymagają poprawnej sesji. Anonimowa przeglądarka pobiera publiczny frontend shell (HTML/JS/CSS/assets), następnie sprawdza `/api/auth/me` i pokazuje tylko kompaktowy polski formularz logowania. Dashboard, miesięczne dane pracy/płac i ustawienia są montowane i pobierane dopiero po potwierdzeniu sesji albo udanym loginie. Nie ma rejestracji ani opcji „zapamiętaj mnie”: każdy poprawny login tworzy trwałą sesję zaufanego urządzenia. W `Ustawienia → Konto` można wylogować bieżące urządzenie.

Centralny pure-ASGI guard chroni każde HTTP `/api` oraz `/api/...` przed routingiem, także przyszłe endpointy i mounty. Brak osobnych dependencies dopisywanych do każdej trasy. Brak/invalid/expired/revoked cookie daje JSON HTTP 401, bez przekierowania do HTML i bez odczytu body. Jedyna lista wyjątków (`SESSION_INDEPENDENT_ENDPOINTS` w `backend/app/auth.py`) zawiera dokładne pary metody i ścieżki:

- `POST /api/auth/login` — ustanowienie sesji;
- `GET /api/auth/me` — samodzielnie waliduje sesję (200/401);
- `POST /api/auth/logout` — idempotentne odwołanie sesji (204);
- `GET /api/health` — minimalny health check;
- `POST /api/webhook/home-assistant` — wyłącznie własny `X-Webhook-Token`;
- `POST /api/webhook/home-assistant/correction` — wyłącznie własny `X-Webhook-Token`.

Wyjątki nie obejmują innych metod, podścieżek ani końcowego `/`. Sesja browser nie zastępuje tokenu HA i nie jest dodatkowym wymaganiem dla HA. Statyczne pliki poza `/api` pozostają anonimowo dostępne, aby zawsze można było załadować ekran logowania. Dokumentacja FastAPI (`/api/docs`, `/api/redoc`, `/api/openapi.json`) również wymaga sesji. Nowe domain endpoints muszą znajdować się w `/api/...` i automatycznie dziedziczą ochronę.

Reload przywraca sesję przez `/api/auth/me`; zalogowana widoczna karta sprawdza ją co 5 minut i po odzyskaniu focus. Wszystkie ordinary API calls (również mutacje i eksporty) wysyłają cookie przez `credentials: include`. HTTP 401 z dowolnego domain requestu albo `/auth/me` oznacza globalną utratę sesji: UI wraca do loginu z „Sesja wygasła. Zaloguj się ponownie.”, usuwa lokalny stan pracy/płac/ustawień i zatrzymuje polling dashboardu. Dane w bazie pozostają nienaruszone. Wersjonowanie obejmuje odpowiedzi i odczyt JSON/blob, aby stare odpowiedzi nie przywróciły danych ani nie wylogowały nowego loginu. Logout natychmiast ukrywa dane; błąd sieci zgłasza nieudane odwołanie sesji, bez przywracania lokalnego widoku. Błąd sieci przy sprawdzaniu już aktywnej sesji pozostawia aktualny widok do ponowienia; pierwsze nieudane sprawdzenie nie otwiera aplikacji.

Przed rolloutem operator powinien potwierdzić istniejące konto i poprawny login oraz jawne `SESSION_COOKIE_SECURE=false` dla obecnego LAN HTTP. Po aktualizacji sprawdź: anonimowy shell → login → dashboard → logout → login oraz oba webhooki HA bez browser cookie. 8A3 nie wymaga migracji ani nowej konfiguracji; head pozostaje `20261006_07`. Istniejący updater/backup/rollback pozostaje bez zmian; wycofanie do 8A2 ponownie otworzy ordinary API dla LAN.

Sesja wygasa dokładnie po **30 dniach od zapisanego `last_seen_at`** albo po **180 dniach od początkowego loginu**, zależnie od tego, co nastąpi wcześniej; na samej granicy jest już nieważna. Activity update nie przedłuża 180 dni. Udany `/auth/me` i ordinary authenticated API zapisują activity najwyżej raz na godzinę; warunkowy UPDATE nie cofa czasu i nie wskrzesza expired/revoked sesji. Coalescing oznacza, że ostatnie żądanie przed zamknięciem karty może być późniejsze od `last_seen_at` o mniej niż godzinę. HA i pozostałe session-independent wyjątki nie zapisują activity. Ukryta karta nie wykonuje okresowych sprawdzeń.

Token ma 256 bitów CSPRNG entropy (`secrets.token_urlsafe(32)`); `user_sessions` przechowuje tylko SHA-256, ID, FK do użytkownika i UTC created_at/last_seen_at/expires_at/revoked_at. Nie potrzeba sekretu podpisującego cookie. Każdy poprawny login tworzy nowy token i osobną sesję. Token jest stabilny do logout/expiry; brak okresowej rotacji to świadoma, kompletna polityka 8A2. Transparentna rotacja z race-safe recovery będzie projektowana w 8B1. Logout zapisuje revocation i usuwa cookie; inne urządzenia pozostają zalogowane. Indeks user_id umożliwia późniejsze bulk revocation. Nie ma background cleanup; nieważne rekordy pozostają w bazie i nie dają dostępu.

Host-only cookie `work_tracker_session` ma `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=15552000` i `Expires` odpowiadające absolute expiry. Cookie może pozostać w przeglądarce po wcześniejszym idle expiry/revocation, ale serwer go nie akceptuje. `/auth/me` nie usuwa cookie, aby spóźniona odpowiedź nie skasowała świeżego loginu. Login ma ten sam błąd dla błędnego hasła i nieistniejącego konta; brak konta wykonuje dummy Argon2 verification. Hasła/tokens nie trafiają do browser storage, URL-i ani logów aplikacji.

Kod domyślnie używa `SESSION_COOKIE_SECURE=true`. Instalator i updater wymagają jawnej wartości w `/etc/work-tracker/work-tracker.env`: podczas obecnego LAN-only HTTP rollout wybierz `false` (domyślna propozycja manifestu dla tego wdrożenia). Updater dopisuje wyłącznie brakujący klucz i zachowuje istniejącą wartość. Developerski `.env.example` również jawnie ustawia `false`. **Każde przyszłe HTTPS/Internet wdrożenie musi ustawić `true` przed udostępnieniem.** Aplikacja nie wyznacza Secure z `X-Forwarded-*`; trusted proxy handling należy do 8B2. Ten etap nie włącza proxy, tunelu ani publicznego routingu.

Produkcja używa same-origin FastAPI/frontend. Istniejący Vite z `VITE_API_BASE_URL=http://localhost:8000` używa wszystkich API requests z `credentials: include` i jawnych `CORS_ORIGINS`; wildcard i origin `null` są odrzucane. Używaj tego samego hostname dla Vite i API (np. `localhost` po obu stronach, albo `127.0.0.1` po obu stronach), ponieważ SameSite=Lax nie służy do cross-site cookies. Przy frontendzie LAN ustaw oba adresy na ten sam hostname/IP, różniący się portem, i dodaj dokładny origin Vite do CORS. Zbudowany frontend production pozostawia `VITE_API_BASE_URL` puste.

Migracja `20261006_07` jest addytywna: dodaje pustą tabelę sesji, bez zmiany istniejących użytkowników lub danych domenowych. Updater zachowuje backup/rollback; starszy kod nie potrzebuje tabeli sesji. Następny etap to 8B1: full CSRF, rate limiting i race-safe transparentna rotacja/recovery oraz dalsze browser hardening, a trusted proxy review — 8B2.

## Logs/status/restart

```bash
sudo systemctl status work-tracker
sudo journalctl -u work-tracker -f
sudo systemctl restart work-tracker
```

## Backup

Updater zapisuje backupy jako `/var/backups/work-tracker/work_tracker-<timestamp-UTC>.db`. Pliki są własnością roota i mają restrykcyjne uprawnienia. Backupy nie są automatycznie usuwane.

## File locations

- Kod: `/opt/work-tracker/`
- Konfiguracja: `/etc/work-tracker/work-tracker.env`
- Baza SQLite: `/var/lib/work-tracker/work_tracker.db`
- Backupy: `/var/backups/work-tracker/`
- Unit systemd: `/etc/systemd/system/work-tracker.service`

Kod w `/opt/work-tracker` pozostaje własnością `root`, a grupa `work-tracker` ma dostęp tylko do odczytu i przechodzenia przez katalogi. Katalogi mają tryb `0750`, zwykłe pliki `0640`, a pliki wykonywalne `0750`. Metadata `.git` pozostają prywatne dla roota. Aplikacja zapisuje dane wyłącznie w `/var/lib/work-tracker`.

## Uninstall

Poniższe polecenia usuwają usługę i kod, ale celowo zachowują konfigurację, bazę i backupy:

```bash
sudo systemctl disable --now work-tracker
sudo rm /etc/systemd/system/work-tracker.service
sudo systemctl daemon-reload
sudo rm -r /opt/work-tracker
sudo userdel work-tracker
```

Katalogi `/etc/work-tracker`, `/var/lib/work-tracker` i `/var/backups/work-tracker` należy usunąć osobno tylko po świadomej decyzji, że dane nie będą już potrzebne.

## Wymagania

- Python 3.11+
- Node.js 18+ i npm

## Instalacja developerska

1. Skopiuj konfigurację i ustaw własny losowy sekret o długości co najmniej 32 znaków:

   ```bash
   cp .env.example backend/.env
   # edytuj backend/.env, przede wszystkim WEBHOOK_TOKEN
   ```

2. Zainstaluj backend:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   ```

3. Utwórz bazę przez migrację:

   ```bash
   alembic upgrade head
   ```

4. W drugim terminalu przygotuj frontend:

   ```bash
   cd frontend
   cp .env.example .env
   npm install
   ```

## Uruchomienie lokalne

Terminal backendu (z aktywnym środowiskiem wirtualnym):

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal frontendu:

```bash
cd frontend
npm run dev
```

Otwórz adres wyświetlony przez Vite (zwykle `http://localhost:5173`).

### Uruchomienie w zaufanej sieci LAN

Powyższa komenda nasłuchuje tylko na komputerze lokalnym i jest właściwa do pracy developerskiej. Gdy Home Assistant działa na innym hoście LAN, uruchom backend na wszystkich interfejsach serwera:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Nie oznacza to publicznego dostępu: nie konfiguruj przekierowania portów, publicznego IP ani tunelu. Dostęp powinien być ograniczony do zaufanej sieci LAN przez konfigurację sieci serwera. Home Assistant nie wymaga CORS; CORS dotyczy wyłącznie przeglądarkowego frontendu. Jeśli frontend Vite działa pod innym adresem LAN, dodaj jego pełny origin do `CORS_ORIGINS`.

## Migracje

Po aktywowaniu virtualenv w `backend/`:

```bash
alembic upgrade head
alembic revision --autogenerate -m "opis zmiany"
```

## Testy

```bash
cd backend
source .venv/bin/activate
pytest
bash -n ../install.sh ../update.sh ../deploy/common.sh ../deploy/update_rollback.sh ../deploy/tests/*.sh
../deploy/tests/config_update_test.sh
../deploy/tests/update_rollback_test.sh
../deploy/tests/backend_package_install_test.sh
../deploy/tests/application_permissions_test.sh
sudo ../deploy/tests/service_user_runtime_test.sh
```

Frontend (w tym testy wyrenderowanej bramki logowania w jsdom przez Vite) można sprawdzić komendą:

```bash
cd frontend
npm test
npm run build
```

## Endpointy

Poza sześcioma wyjątkami opisanymi powyżej wszystkie endpointy API wymagają session cookie. Klient skryptowy ordinary API musi najpierw zalogować się przez `/api/auth/login` i zachować cookie (np. `curl -c` / `curl -b` z prywatnym plikiem cookie poza checkoutem). Nie przekazuj hasła ani tokenu w URL.

- `POST /api/auth/login` — JSON username/password, nowa trwała sesja i HttpOnly cookie; ogólny błąd HTTP 401 dla nieprawidłowych credentials.
- `GET /api/auth/me` — username i absolute expires_at bieżącej sesji; invalid/expired/revoked cookie zwraca HTTP 401. Używa wspólnego walidatora z guardem ordinary API.
- `POST /api/auth/logout` — HTTP 204, server-side revocation i usunięcie cookie; bez ważnej sesji również bezpieczny no-op.

- `POST /api/webhook/home-assistant` — przyjmuje JSON webhooka, zapisuje immutable raw event i zwraca jego `id`, status oraz dane prezentacyjne; wymaga nagłówka `X-Webhook-Token`.
- `POST /api/webhook/home-assistant/correction` — ustawia audytowalną korektę godziny konkretnego raw eventu na podstawie time-only inputu; wymaga nagłówka `X-Webhook-Token`.
- `GET /api/work-events?year=2026&month=9` — zwraca chronologicznie zdarzenia dla wskazanego miesiąca kalendarzowego w offsetcie przekazanym przez Home Assistanta.
- `GET /api/work-summary?year=2026&month=9` — wylicza sesje, dni, miesięczny czas pracy i anomalie na podstawie effective events oraz zwraca metadane audytowe korekt i automatycznie pominięte krótkie wizyty (`suppressed_short_visit`).
- `PUT /api/work-events/{raw_event_id}/timestamp-correction` — tworzy lub aktualizuje korektę timestampu raw eventu.
- `PUT /api/work-events/{raw_event_id}/ignore` — wyłącza raw event z effective event stream bez modyfikowania źródłowego rekordu.
- `POST /api/manual-events` — dodaje ręczne `entry` albo `exit` jako rekord korekty.
- `GET /api/corrections` — zwraca aktualne korekty.
- `DELETE /api/corrections/{correction_id}` — cofa korektę bez zmiany raw eventu.
- `GET /api/pay-rates` — zwraca historyczne stawki godzinowe w kolejności obowiązywania.
- `POST /api/pay-rates` — dodaje nową historyczną stawkę bez nadpisywania wcześniejszych okresów.
- `GET /api/application-settings` — zwraca globalny tytuł aplikacji, aliasy i opcjonalne IANA timezone canonical locations.
- `PUT /api/application-settings` — transakcyjnie aktualizuje globalne ustawienia; pominięta timezone zachowuje poprzednią wartość, a jawne `null` ją usuwa.
- `GET /api/pay-summary?year=2026&month=9` — zwraca autorytatywne dzienne i miesięczne wynagrodzenie za poprawne sesje.
- `GET /api/dashboard?timezone=Europe/Warsaw` — zwraca autorytatywny live status oraz podsumowanie dnia i bieżącego miesiąca w podanej strefie IANA.
- `GET /api/export/monthly.csv?year=2026&month=9` — pobiera historyczny raport miesiąca jako CSV UTF-8 z BOM i separatorem `;`.
- `GET /api/export/monthly.pdf?year=2026&month=9` — pobiera historyczny raport miesiąca jako PDF A4.
- `GET /api/health` — prosty status API.

Przykładowy webhook:

```bash
curl -X POST http://127.0.0.1:8000/api/webhook/home-assistant \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Token: TWOJ_SEKRET' \
  -d '{"event":"entry","location":"gabinet_zabki","timestamp":"2026-09-06T08:14:32+02:00","source":"home_assistant"}'
```

Po poprawnym zapisie webhook zwraca nadal istniejące pola `id` i `status` oraz
dodatkowy kontekst wykorzystywany przez produkcyjną integrację interaktywnych
powiadomień:

```json
{
  "id": 123,
  "status": "accepted",
  "event": "entry",
  "location": "gabinet_zabki",
  "location_display_name": "ARTE Stomatologia",
  "timestamp": "2026-09-09T07:20:14+02:00"
}
```

`location_display_name` pochodzi z bieżących application settings i bez aliasu
jest równy canonical `location`. Zapis i odpowiedź ingestion nie wymagają
skonfigurowanej timezone.

Format lokalizacji to małe litery, cyfry i `_`, np. `gabinet_zabki`; dzięki temu można później dodać kolejne lokalizacje bez zmiany modelu.

`source` w obecnej wersji musi mieć wartość `home_assistant`. Zdarzenia zapisują oryginalny ISO-8601 timestamp wraz z offsetem oraz osobny, znormalizowany timestamp UTC. Chwila UTC jest podstawą bieżącego liczenia czasu trwania, a pierwszy timestamp zachowuje lokalną datę, godzinę i offset również przy zmianie czasu letniego/zimowego.

Skonfigurowane nazwy wyświetlane lokalizacji są używane w zwykłym UI oraz w nowo generowanych CSV/PDF, z fallbackiem do identyfikatora technicznego. Identyfikator zapisany w zdarzeniach nie jest zmieniany. Alias zaczynający się od `=`, `+`, `-` lub `@` jest neutralizowany apostrofem wyłącznie w komórce CSV, aby arkusz kalkulacyjny nie wykonał go jako formuły; UI, PDF i zapisane ustawienie zachowują oryginalny tekst.

W `Ustawienia → Aplikacja` każda znana canonical location ma również opcjonalne
pole IANA timezone, np. `Europe/Warsaw`. Backend waliduje identyfikator przez
`ZoneInfo`. Pusta timezone jest dozwolona i nie jest zgadywana z przeglądarki,
offsetu raw eventu ani Home Assistanta. Istniejącą lokalizację produkcyjną
`gabinet_zabki` należy skonfigurować ręcznie po wdrożeniu migracji.

Przykładowa korekta godziny z warstwy integracyjnej Home Assistanta:

```bash
curl -X POST http://127.0.0.1:8000/api/webhook/home-assistant/correction \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Token: TWOJ_SEKRET' \
  -d '{"raw_event_id":123,"time":"7.45"}'
```

```json
{
  "raw_event_id": 123,
  "correction_id": 45,
  "event": "entry",
  "location": "gabinet_zabki",
  "location_display_name": "ARTE Stomatologia",
  "original_timestamp": "2026-09-09T07:20:14+02:00",
  "effective_timestamp": "2026-09-09T07:45:00+02:00"
}
```

`correction_id: null` oznacza, że po obsłużeniu żądania nie istnieje aktywna
korekta timestampu, ponieważ requested effective instant jest dokładnie równy
immutable raw instantowi. W takim przypadku backend nie tworzy no-op
`timestamp_override`, a istniejący `timestamp_override` usuwa jako undo warstwy
korekty. Porównanie odbywa się między pełnymi UTC instants, więc raw
`07:20:14` i resolved `07:20:00` nadal są różne i tworzą korektę.

Pole `time` akceptuje `H:MM`, `HH:MM`, `H.MM` i `HH.MM` z zewnętrznym
whitespace. Backend wyznacza datę wyłącznie względem raw UTC instantu i IANA
timezone jego canonical location, uwzględnia dzień poprzedni/bieżący/następny,
oba `fold` DST oraz okno ±4 godzin. Brak lub błędna timezone, nonexistent lub
nierozstrzygalny ambiguous local time, błędny format i wyjście poza okno zwracają
HTTP 422 bez zmiany danych. Błędy integracyjne mają stabilny kod w
`detail.code`, m.in. `invalid_time`, `location_timezone_missing`,
`location_timezone_invalid`, `time_not_resolvable`, `time_out_of_range`,
`correction_conflict` i `raw_event_not_found`.

W produkcyjnym Home Assistant po prawidłowym zapisaniu odpowiedniego raw eventu
wysyłane jest actionable notification. Odpowiedź użytkownika może wywołać korektę
godziny przez powyższy endpoint; korekta korzysta z audytowalnego
`timestamp_override`, a raw event pozostaje niezmieniony. Integracja została
zweryfikowana end-to-end na rzeczywistym zone triggerze, również dla future
effective entry, i ma niezależny kill switch dla warstwy powiadomień. Konfiguracja
Home Assistanta jest utrzymywana ręcznie poza tym repozytorium.

Frontend pokazuje `missing_exit` jako „Trwająca zmiana” tylko wtedy, gdy jego wejście odpowiada tej samej chwili UTC co jednoznaczna sesja zwrócona przez dashboard. Pierwszy snapshot i późniejsze istotne zmiany dashboardu odświeżają aktualnie wybrane podsumowanie czasu i płac; sam upływ czasu bieżącej zmiany nie powoduje dodatkowych żądań miesięcznych.

### Automatyczne pomijanie krótkich wizyt

Jednoznaczna zakończona para `entry → exit`, która byłaby `valid`, jest klasyfikowana jako `suppressed_short_visit`, jeśli obie granice pochodzą z nietkniętych eventów Home Assistant, a dokładna różnica chwil UTC wynosi **≤ 5 minut**. Dokładnie 5:00 jest pomijane, a każdy czas powyżej 5 minut pozostaje zwykłą sesją; próg nie korzysta z zaokrąglonych minut. Manualny event lub aktywna korekta timestampu dowolnej granicy wyłącza automatyczne pomijanie. Anomalie zawsze pozostają widoczne.

Taka wizyta zachowuje oba eventy i `duration_seconds` w `work-summary`, lecz nie jest pracą ani anomalią: nie zwiększa sum czasu, `work_days`, `anomaly_count` ani wynagrodzenia. CSV/PDF pomijają ją zarówno w sesjach, jak i problemach. Reguła działa przy odczycie także istniejącej historii, bez migracji, backfillu, automatycznych `ignore_event` lub zmiany raw events. Cofnięcie ręcznej korekty przywraca klasyfikację aktualnego strumienia raw events.

W normalnym widoku miesiąca wizyty i dni zawierające wyłącznie pominięte wpisy są ukryte. `Ustawienia → Widok → Pokaż ignorowane i automatycznie ukryte wydarzenia` przywraca neutralny audyt: oba eventy, zakres godzin oraz dokładny czas w minutach i sekundach (wyjątek od zwykłej prezentacji bez sekund). Istniejąca preferencja przeglądarki pozostaje zachowana.

Włączenie audytu oraz każdy udany poll dashboardu przy włączonym audycie odświeżają `work-summary` w tle, także gdy status i sumy pozostają bez zmian po krótkiej wizycie między pollami. Obecny widok i rozwinięte dni pozostają wyrenderowane; dopiero udana odpowiedź podmienia summary, a błąd zachowuje ostatnie dane do kolejnej próby. Kolejny poll podczas trwającego odświeżenia nie rozpoczyna nowego requestu ani nie anuluje poprzedniego. Zmiana miesiąca lub jawne retry anuluje nieaktualne odświeżenie i korzysta ze zwykłego loading state. Ten dodatkowy refresh nie pobiera `pay-summary`. Zwykły widok nadal ogranicza odświeżenia miesięczne do istotnych zmian dashboardu; polling pozostaje co 30 sekund.

Ingestion i interaktywne powiadomienia Home Assistanta działają bez opóźnień i zmian. Po `entry` dashboard nadal może raportować `working`; dopiero rzeczywisty `exit` kończy wizytę i pozwala ją pominąć, ze statusem `outside`. Planowana integracja Google Calendar będzie projektować tylko `valid` finalized sessions, więc nie obejmie tych wizyt.

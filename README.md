# Work Tracker

Prosta aplikacja obecnie działająca lokalnie w sieci LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy. Backend przyjmuje zabezpieczone webhooki Home Assistanta, zapisuje każde zdarzenie w SQLite i wylicza sesje pracy bez zmiany danych źródłowych. Frontend React pokazuje bieżący status pracy, miesięczne sesje i anomalie, korekty, wynagrodzenie oraz eksporty CSV/PDF.

Instalator domyślnie uruchamia LAN HTTP; opcjonalnie przygotowuje prywatny HTTPS/split ingress. Nie konfiguruje publicznego dostępu, domeny ani tunelu.

## Planned direction (not implemented)

Po ukończeniu authentication/hardening (Phase 8) planowany jest dostęp do całej aplikacji pod dedykowaną, jeszcze nieustaloną subdomeną: Internet → Cloudflare → Cloudflare Tunnel → prywatny origin Work Trackera w LAN (Phase 9), bez przekierowania portów routera i bez bezpośredniego wystawienia LXC. Następnie planowana jest integracja z dedykowanym Google Calendar (Phase 10): projekcja wyłącznie poprawnych zakończonych sesji oraz ograniczone korekty godzin zarządzanych wydarzeń z powrotem w Work Trackerze. Calendar nie będzie źródłem raw events. Szczegóły i kryteria etapów są w `ROADMAP.md`; standardowa instalacja poniżej dotyczy LAN-only; osobny profil przygotowania 8B2 nie aktywuje publicznego dostępu.

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

Instalator najpierw proponuje `lan` (domyślny) lub `https` (przygotowanie).
HTTPS również pozostawia działający LAN do osobnego, kontrolowanego cutoveru.
Następnie pyta interaktywnie o:

- adres nasłuchu, domyślnie `0.0.0.0`,
- port backendu, domyślnie `8000`,
- token webhooka (minimum 32 znaki) lub zgodę przez pozostawienie pustej wartości na jego bezpieczne wygenerowanie,
- `BROWSER_ALLOWED_ORIGINS`: jawną niepustą listę pełnych origins przeglądarki (scheme, host, port), bez ścieżek i wildcardów; brak proponowanej szerokiej wartości domyślnej,
- originy CORS, jeśli frontend ma działać z innego originu,
- `SESSION_COOKIE_SECURE`: `false` dla zaufanego LAN HTTP; w trybie przygotowania HTTPS instalator zachowuje `false` do osobnego cutoveru, który ustawi `true`.

Wygenerowany token nie jest wyświetlany. Zostaje zapisany w `/etc/work-tracker/work-tracker.env`, dostępnym tylko dla roota i grupy usługi. Baza zawsze znajduje się poza repozytorium pod `/var/lib/work-tracker/work_tracker.db`.

## Access in LAN

Przy domyślnym `APP_HOST=0.0.0.0` aplikacja nasłuchuje na interfejsach serwera i jest dostępna pod:

```text
http://<adres-IP-serwera-w-LAN>:8000
```

W trybie LAN FastAPI serwuje zbudowany frontend z `frontend/dist`; osobny Vite ani Nginx nie są potrzebne. W docelowym profilu HTTPS/split Nginx jest wymaganym elementem prywatnego ingressu. CORS nie dotyczy webhooka Home Assistanta; jest potrzebny tylko wtedy, gdy przeglądarkowy frontend pochodzi z innego originu.

Instalator nie otwiera firewalla i nie konfiguruje routera, publicznego IP ani tunelu. Dostęp należy pozostawić wyłącznie w zaufanej sieci LAN.

## Update

**Pierwsze przejście z `5d974b1` do 9B2 wymaga [procedury first-upgrade](docs/deployment/phase9b2-cutover.md#pierwsza-aktualizacja-z-5d974b1).** Nie uruchamiaj wtedy starego `update.sh`: jego kopia w `/tmp` nie instaluje nowego guarda przed migracją. Operator wybiera przejrzany pełny SHA i zaufany ref; bootstrap sprawdza rzeczywisty stary checkout, instaluje trwały checker poza nim, weryfikuje guard systemd i przekazuje PID/blokadę nowemu updaterowi. Nie wystarczy skopiować samego nowego `update.sh` do starej instalacji.

Poniższa zwykła komenda służy instalacjom, które już mają mechanizm 9B2:

```bash
sudo /opt/work-tracker/update.sh
```

Updater wymaga czystego repozytorium i dostępu do gałęzi `main`. Wykonuje spójny online backup SQLite, pobiera kod przez fast-forward, aktualizuje zależności i buduje frontend. Następnie zatrzymuje usługę, potwierdza brak jej PID, zapisuje drugi checkpoint i wywołuje Alembic. **Nigdy automatycznie nie zastępuje bieżącej SQLite ani WAL/SHM historyczną kopią.** Zapisane po backupie eventy, korekty, użytkownicy, sesje i historia limitera pozostają w bieżącej bazie.

Błąd **przed wywołaniem Alembic** pozwala przywrócić poprzedni kod, zależności, frontend i unit, zachowując bieżącą DB. Błąd **od próby wywołania Alembic**, także po migracji no-op lub po restarcie, oznacza `UPDATE REQUIRES MANUAL RECOVERY`: usługa jest zatrzymywana, kod kandydata i wszystkie kopie pozostają, a trwały guard blokuje start starej/niezweryfikowanej wersji po reboot. Nie ma automatycznego downgrade. Procedura zachowania danych i naprawy: [runbook 9B2](docs/deployment/phase9b2-cutover.md#odzyskiwanie-po-nieudanej-aktualizacji).

Dla aktywnego splitu updater czeka na HTTP backendu i oba listenery proxy, domyślnie **60 s**; można podać `sudo /opt/work-tracker/update.sh --readiness-timeout 120` (zakres **5–300 s**). Oczekiwanie obejmuje brak procesu/socketu oraz niepełne argv/env przed exec i zmianę UID workera Nginx; wymagane są dwa zgodne obrazy PID/workerów. Trwale zły runtime kończy się odmową w tym samym budżecie, a obcy listener, zły bind lub obcy UID są odrzucane od razu. Konfiguracja jest sprawdzana przed oczekiwaniem, a pełna kontrola TLS/ACL/uprawnień po gotowości — statyczne błędy bezpieczeństwa nie są ponawiane. Timeout rozróżnia niedostępne usługi i utrzymującą się niezgodność runtime. Na wolniejszym systemie wybierz np. 120 s po weryfikacji 9C. `is-active` nie dowodzi gotowości HTTP. Limit obejmuje oczekiwanie readiness, nie build, migracje ani końcowe kontrole bezpieczeństwa.

Zaplanuj okno bez zdarzeń HA: odbiór webhooków jest przerwany od zatrzymania backendu przez migracje do gotowości usług; błąd wymagający naprawy wydłuża tę przerwę. **HA nie musi ponawiać żądań. Zachowanie zaakceptowanych zapisów nie gwarantuje przyjęcia żądania wysłanego podczas niedostępności.**

Instalacje z trwałym checkerem utworzonym przez wcześniejszy bootstrap 9B2 po udanym update wykonują osobną operację `sudo python3 /opt/work-tracker/deploy/update_safety.py migrate-checker`. Nowy kod akceptuje katalogowaną v1, więc stary staged updater może zakończyć swoją transakcję. Dopiero po jego zakończeniu operacja pod wspólną blokadą wymienia checker na v2. Nie uruchamiaj jej przy aktywnym/niedokończonym update. [Migracja i odzyskanie po przerwaniu](docs/deployment/phase9b2-cutover.md#migracja-trwałego-checkera-v1--v2).

Nowe wymagane ustawienia są definiowane w `deploy/config.manifest`. Updater dopisuje wyłącznie brakujące wymagane klucze, pyta o ich wartości i pokazuje bezpieczne wartości domyślne. Nie zmienia istniejących wartości, nie usuwa starszych lub nieznanych wpisów i nigdy nie wypisuje sekretów. Jeśli nie ma nowych wymaganych kluczy, aktualizacja nie zadaje pytań konfiguracyjnych.

### Pierwsza aktualizacja do 8B1-A i zmiana adresu

Przed aktualizacją istniejącego LXC dopisz w `/etc/work-tracker/work-tracker.env`
origin faktycznie używany do otwierania aplikacji, przykładowo:

```dotenv
BROWSER_ALLOWED_ORIGINS=http://10.123.45.10:8000
CORS_ORIGINS=
SESSION_COOKIE_SECURE=false
```

Podstaw własny stabilny adres LAN lub DNS (np. `http://work-tracker.home.arpa:8000`).
Lista jest rozdzielana przecinkami. Nie wpisuj adresu nasłuchu `0.0.0.0`, ścieżki,
wildcardu ani `null`. Backend i deploy używają tego samego walidatora;
scheme/host i domyślne porty są kanonizowane. `CORS_ORIGINS` musi być podzbiorem
`BROWSER_ALLOWED_ORIGINS`; dla produkcyjnego same-origin pozostaje puste.
Vite wymaga dodania jego originu do obu list, np. `http://localhost:5173`.

Brak klucza powoduje pytanie updatera, bez zgadywania adresu. Brak odpowiedzi,
nieprawidłowa wartość albo zbyt szeroki CORS przerywa aktualizację przed zmianą
zależności, migracją i restartem nowej wersji. Działa dotychczasowy rollback kodu,
frontendu i unitu; przed rozpoczęciem migracji baza z nowymi eventami pozostaje
nietknięta. Backend bez poprawnej konfiguracji nie startuje. Rollback do 8A3
usuwa nową ochronę mutacji, więc służy wyłącznie awaryjnemu użyciu w izolowanym LAN.

Po udanym update przeładuj stronę, aby pobrać klienta ze znacznikiem żądań;
stary klient otrzyma 403 przy mutacji. Sprawdź login/logout, heartbeat po kliknięciu
w widocznej karcie, korektę, eksporty i oba webhooki HA bez browser cookie.
Po zmianie DHCP/hosta/portu uaktualnij obie listy i zrestartuj usługę, sprawdź nowy
adres, potem usuń stary origin. Zmiana hosta wymaga nowego loginu; w razie zmiany
adresu backendu popraw również URL integracji HA. Brak nowego originu daje 403.
8B1-A nie wymaga migracji, tabeli ani nowego sekretu. 8B1-B dodaje migrację
`20261025_08`; jest to bieżący head Alembic.

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

Wyjątki nie obejmują innych metod, podścieżek ani końcowego `/`. Sesja browser nie zastępuje tokenu HA i nie jest dodatkowym wymaganiem dla HA. Statyczne pliki poza `/api` pozostają anonimowo dostępne, aby zawsze można było załadować ekran logowania. OpenAPI JSON (`/api/openapi.json`) wymaga sesji. Interaktywny HTML Swagger/ReDoc jest wyłączony, również w development, ponieważ wymagał CDN/inline niezgodnych z CSP. Nowe domain endpoints muszą znajdować się w `/api/...` i automatycznie dziedziczą ochronę.

Reload przywraca sesję przez read-only `/api/auth/me`; zalogowana widoczna karta sprawdza ją co 5 minut. Focus/powrót do aplikacji i interakcje użytkownika wysyłają osobny `POST /api/auth/heartbeat`, najwyżej raz na 5 minut. Sam polling, także widocznej bezczynnej karty, nie odnawia idle. Wszystkie ordinary API calls (również mutacje i eksporty) wysyłają cookie przez `credentials: include`. HTTP 401 z dowolnego domain requestu, `/auth/me` albo heartbeat oznacza globalną utratę sesji: UI wraca do loginu z „Sesja wygasła. Zaloguj się ponownie.”, usuwa lokalny stan pracy/płac/ustawień i zatrzymuje polling dashboardu. Dane w bazie pozostają nienaruszone. Wersjonowanie obejmuje odpowiedzi i odczyt JSON/blob, aby stare odpowiedzi nie przywróciły danych ani nie wylogowały nowego loginu. Logout natychmiast ukrywa dane; błąd sieci zgłasza nieudane odwołanie sesji, bez przywracania lokalnego widoku. Błąd sieci przy sprawdzaniu już aktywnej sesji pozostawia aktualny widok do ponowienia; pierwsze nieudane sprawdzenie nie otwiera aplikacji. Heartbeat nie odtwarza automatycznie nieudanego żądania.

Przed rolloutem operator powinien potwierdzić istniejące konto, poprawny login, jawne origins oraz `SESSION_COOKIE_SECURE=false` dla obecnego LAN HTTP. Procedura pierwszego update do 8B1-A znajduje się powyżej; obecna produkcja pozostaje LAN-only.

Sesja wygasa dokładnie po **30 dniach od zapisanego `last_seen_at`** albo po **180 dniach od początkowego loginu**, zależnie od tego, co nastąpi wcześniej; na samej granicy jest już nieważna. Activity update nie przedłuża 180 dni. Zwykłe protected API requests i `GET /api/auth/me` wyłącznie walidują sesję i **nie zmieniają `last_seen_at`**. `/me` nie wykonuje cleanup/rotacji ani żadnego Set-Cookie. Wyłącznie udany chroniony `POST /api/auth/heartbeat` zapisuje activity najwyżej raz na godzinę; warunkowy UPDATE nie cofa czasu i nie wskrzesza expired/revoked sesji. Coalescing oznacza, że ostatnie żądanie przed zamknięciem karty może być późniejsze od `last_seen_at` o mniej niż godzinę. HA i pozostałe session-independent wyjątki nie zapisują activity. Ukryta karta nie wysyła heartbeat; background domain polling nie odnawia idle.

Token ma 256 bitów CSPRNG entropy (`secrets.token_urlsafe(32)`); `user_sessions` przechowuje tylko SHA-256, ID, FK do użytkownika i UTC created_at/last_seen_at/expires_at/revoked_at. Nie potrzeba sekretu podpisującego cookie. Każdy poprawny login tworzy nowy token i osobną sesję. Token jest stabilny do logout/expiry; okresowa rotacja/recovery pozostaje poza obowiązkowym planem. Logout zapisuje revocation i usuwa cookie; inne urządzenia pozostają zalogowane. Indeks user_id umożliwia późniejsze bulk revocation. Nie ma background cleanup; nieważne rekordy pozostają w bazie i nie dają dostępu.

Host-only cookie `work_tracker_session` ma `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=15552000` i `Expires` odpowiadające absolute expiry. Cookie może pozostać w przeglądarce po wcześniejszym idle expiry/revocation, ale serwer go nie akceptuje. `/auth/me` nie usuwa cookie, aby spóźniona odpowiedź nie skasowała świeżego loginu. Login ma ten sam błąd dla błędnego hasła i nieistniejącego konta; brak konta wykonuje dummy Argon2 verification. Hasła/tokens nie trafiają do browser storage, URL-i ani logów aplikacji.

Kod domyślnie używa `SESSION_COOKIE_SECURE=true`. Instalator i updater wymagają jawnej wartości w `/etc/work-tracker/work-tracker.env`: podczas obecnego LAN-only HTTP rollout wybierz `false` (domyślna propozycja manifestu dla tego wdrożenia). Updater dopisuje wyłącznie brakujący klucz i zachowuje istniejącą wartość. Developerski `.env.example` również jawnie ustawia `false`. **Każde przyszłe HTTPS/Internet wdrożenie musi ustawić `true` przed udostępnieniem.** Aplikacja nie wyznacza Secure z `X-Forwarded-*`. Opcjonalny profil 8B2 opisano poniżej; zwykły update nie włącza proxy, tunelu ani publicznego routingu.

Produkcja używa same-origin FastAPI/frontend. Istniejący Vite z `VITE_API_BASE_URL=http://localhost:8000` używa wszystkich API requests z `credentials: include` i jawnych `CORS_ORIGINS`; wildcard i origin `null` są odrzucane. Używaj tego samego hostname dla Vite i API (np. `localhost` po obu stronach, albo `127.0.0.1` po obu stronach), ponieważ SameSite=Lax nie służy do cross-site cookies. Przy frontendzie LAN ustaw oba adresy na ten sam hostname/IP, różniący się portem, i dodaj dokładny origin Vite do CORS. Zbudowany frontend production pozostawia `VITE_API_BASE_URL` puste.

Migracja `20261006_07` jest addytywna: dodaje pustą tabelę sesji, bez zmiany istniejących użytkowników lub danych domenowych. Updater zachowuje backup/rollback; starszy kod nie potrzebuje tabeli sesji. 8B1-A nie zmienia schematu. 8B1-B dodaje wyłącznie tabelę `login_attempts` (opis poniżej); profil trusted ingress/Access 8B2 opisano poniżej, aktywacja należy do Phase 9.

### Ochrona mutacji i odpowiedzi (8B1-A)

Każda browser mutacja `/api` (wszystkie metody poza GET/HEAD/OPTIONS, w tym
login/logout/heartbeat i przyszłe trasy/mounty) wymaga jednego dozwolonego
`Origin` oraz dokładnie `X-Work-Tracker-Request: 1`. Frontend dodaje znacznik
centralnie. Brak/null/zły Origin lub znacznik daje 403; nie ma fallbacku do Host,
Referer, forwarded ani Sec-Fetch headers. Dla operacji z body wymagany jest
`application/json`, opcjonalnie `charset=UTF-8`; simple types i nieobsługiwane
kodowania dają 415. Poprawny preflight OPTIONS nadal działa bez sesji.
Skryptowy klient browser API musi wysyłać te same nagłówki i zachowywać cookie.
Wyłącznie dwa dokładne webhooki HA są zwolnione z tej polityki i nadal wymagają
własnego `X-Webhook-Token`, niezależnie od browser cookie/Origin/znacznika.

Login ma strumieniowy limit **16384 bajtów body** przed JSON i Argon2id,
niezależnie od Content-Length; przekroczenie daje 413. Hasło po unescape
nadal ma 15–1024 punkty kodowe Unicode, bez trim/normalizacji, NUL i samotnych
surrogate; również 1024 emoji zapisane jako pary escape mieszczą się w limicie.
422/401 nie odbijają hasła ani całego inputu.

Pozostałe browser mutations mają limit **1048576 bajtów (1 MiB)**, również
dla body przesyłanego fragmentami i bez względu na Content-Length. Limit jest
sprawdzany przed dopisaniem fragmentu do bufora; przekroczenie daje 413 przed
walidacją JSON i wykonaniem operacji. Buforowanie pozwala odrzucić wadliwy JSON
również w operacjach ignorujących body. Limit nie obejmuje obu webhooków HA.

Odpowiedzi otrzymują CSP `default-src 'self'; script-src 'self'; style-src 'self';
object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` oraz
`X-Frame-Options: DENY`. Auth, prywatne API, CSV/PDF, login HTML i błędy mają
`Cache-Control: no-store`. Bez HSTS/wymuszania HTTPS w obecnym LAN HTTP.

### Limity logowania (8B1-B)

`POST /api/auth/login` ma dwa niezależne ruchome okna **(teraz − 900 s, teraz]**:
8 zakończonych, zapisanych błędów na kanoniczny login oraz 30 dopuszczonych
prób na IP. Sukces nie zeruje błędów i również zużywa IP. Nieznane konta mają
te same limity, dummy Argon2id i ogólne komunikaty; nie ma informacji, który
limit zadziałał. Błędne hasła poza kontraktem długości/Unicode zwracają ogólne
401 bez Argon2id, ale zapisują błąd. Odrzucenia HTTP/body/JSON
403/413/415/422 nie są próbami credentials.

- **401**: nieprawidłowe dane logowania.
- **429**: wyczerpane konto lub IP, bez weryfikacji hasła; `Retry-After` to
  zaokrąglona w górę liczba sekund (minimum 1) do zwolnienia wszystkich
  blokujących limitów po zapisaniu próby IP. Przy 9 błędach konta muszą wygasnąć
  dwa najstarsze. Ponawianie zablokowanych prób IP nie przesuwa deadline.
- **503**: brak slotu weryfikacji, pełna tabela, brak poprawnego peer IP,
  błąd SQLite (w tym busy/niepewny commit) lub utrata rekordu trwającej próby.
  Bez cookie; nie wykonywać automatycznych ponowień. Blokada konta i brak slotu
  zużywają IP, ale nie zapisują błędu hasła. Pełna tabela/błąd DB/niepoprawny IP
  nie pozwalają przyjąć nowej próby. Przy pełnej tabeli 503 ma pierwszeństwo.

Jedna tabela `login_attempts`: `id INTEGER PRIMARY KEY AUTOINCREMENT`,
`login_key` (64-znakowy SHA-256 ASCII `login:` + kanoniczny login, albo
SHA-256 `invalid-username`), `ip_key` (IPv4 /32, IPv6 /64, mapped IPv4 → IPv4),
`attempted_at` UTC i nullable `failed_at` UTC. Indeksy: `(ip_key, attempted_at)`,
`(login_key, failed_at)`, `attempted_at`. Bez FK do kont, haseł, tokenów i body.
Sprawdzenie/zapis IP i pułapu 4096 rekordów następuje w krótkiej transakcji
`BEGIN IMMEDIATE`. Cleanup na starcie i przy loginie usuwa rekordy dopiero,
gdy obie daty nie należą już do aktywnego okna; nie usuwa przyszłych dat po
cofnięciu zegara. Zapisane liczniki przeżywają restart. Wymagany poprawny UTC/NTP.

Procesowy semafor dopuszcza dwa sloty bez oczekiwania na slot. Slot obejmuje
real/dummy Argon2id, rehash i commit wyniku, także po anulowaniu HTTP.
Weryfikacja odbywa się poza transakcją SQLite. Dwie równoległe weryfikacje mogą
zakończyć się 9 zamiast 8 błędami konta; crash może pozostawić najwyżej dwa
nieznane wyniki, z zachowanymi próbami IP. Wyczerpanie konta/IP/tabeli może
czasowo uniemożliwić nowy login. Istniejące sesje i oba webhooki HA działają dalej.
Brak stałych blokad, limitów dobowych, progresji kar i dodatkowych tabel.

Unit systemd jawnie uruchamia **`--workers 1 --no-proxy-headers`**. W LAN
limiter bierze wyłącznie peer IP, ignoruje X-Forwarded-For, Forwarded i
CF-Connecting-IP, również od localhost. Nie uruchamiać dodatkowych workerów ani
instancji. Klienci za wspólnym NAT lub w tym samym IPv6 /64 współdzielą budżet.
8B1-B nie dodaje ustawień ani sekretów; domyślny profil nadal jest LAN-only. Opcjonalne ustawienia 8B2 opisano poniżej.
Frontend pokazuje polskie komunikaty 401/429/503 i czas z `Retry-After`.
Sesje, heartbeat, logout i ochrona CSRF 8B1-A pozostają bez zmian.

Bezpieczne wdrożenie po merge i walidacji środowiska: potwierdzić origins,
`SESSION_COOKIE_SECURE=false` dla LAN HTTP, konto oraz backupy poza checkoutem.
Użyć istniejącego `sudo /opt/work-tracker/update.sh`: czysty checkout, backup
SQLite, fast-forward, wspólny build klienta/backendu, stop usługi, addytywna
migracja jako service user i restart z nowym unitem. Potwierdzić head
`20261025_08`, jeden worker, login/logout/heartbeat, eksporty i oba webhooki HA.
Operator potwierdził wdrożenie 8B1-A/B oraz pozytywne testy package-install i
service-user runtime na Debianie 13 LXC (main de963ae, head 20261025_08).
To potwierdzenie operatora, nie samodzielny test Codexa w produkcji. Bieżące
8B2 nie wykonuje deploymentu. Bieżący lokalny package-install przechodzi;
runtime użytkownika usługi jest SKIP/NOT RUN bez roota.

Rollback updatera przed wywołaniem Alembic zachowuje bieżącą DB; od próby migracji błąd wymaga ręcznej analizy kompatybilności, bez automatycznego cofania kodu i danych. Przy ręcznej naprawie zachowaj także sesje i historię limitera, nie przywracaj starszej DB kosztem nowych zapisów. Procedura w runbooku 9B2 zastępuje dawny automatyczny restore. Powrót do 8B1-A usuwa limiter, więc służy wyłącznie awaryjnemu użyciu w izolowanym LAN po analizie schematu.

## Przygotowanie Cloudflare Access i HTTPS (8B2, bez aktywacji)

Kod zawiera opt-in profil izolowanych ingressów. Obecny update pozostawia
`INGRESS_PROFILE=lan` (domyślne), LAN HTTP i tokenowe webhooki bez zmian.
Nie instaluje Nginx i nie tworzy Access/DNS/tunelu. Publiczny rollout to Phase 9.

Docelowo: browser HTTPS → Access (jawne osoby/default-deny) → cloudflared w
osobnym LXC (Protect with Access/JWT) → Nginx TLS w WT LXC (tylko źródło
connectora) → lokalny Uvicorn. Osobny Nginx LAN HTTP przyjmuje tylko HA, na
**dotychczasowym IP/porcie HA**. Backend rozróżnia ingressy po zachowanym
peer transportowym 127.0.0.2/.3 i odrzuca pozostałe. Publiczne webhooki są
blokowane niezależnie od sesji Access/WT; prywatne zachowują X-Webhook-Token.

| Ustawienie | Obecne LAN | Docelowe split (dopiero Phase 9) |
|---|---|---|
| INGRESS_PROFILE | lan (opcjonalny klucz) | split |
| BROWSER_ALLOWED_ORIGINS | dokładny dotychczasowy HTTP origin | jeden `https://<PUBLIC_HOSTNAME>` na 443 |
| SESSION_COOKIE_SECURE | false wyłącznie w kontrolowanym LAN HTTP | true |
| CORS_ORIGINS | dotychczasowy podzbiór origins | puste, same-origin |
| BROWSER_HSTS_SECONDS | 0 (opcjonalny klucz) | najpierw 0, po testach 300 → 86400 → 31536000 |

Runtime i updater sprawdzają spójność profilu przed uruchomieniem/migracją.
Host-only HttpOnly/SameSite=Lax, CSRF i sesje 30 dni idle/180 dni absolute są
bez zmian. Nadal jeden worker, dwa sloty Argon2id, 8 błędów/login i 30 prób/IP
na 15 minut. Split używa CF-Connecting-IP tylko za potwierdzonym ingress;
brak/błędny/duplikowany IP daje login 503, bez fallbacku do connectora.
XFF/Forwarded nigdy nie ustalają IP. Nie zmieniać `--no-proxy-headers`.

Przykłady z jawnymi placeholderami są w [deploy/ingress](deploy/ingress).
Pełny [runbook operatora Phase 9](docs/deployment/phase9-ingress.md) zawiera
Access local/remote, TLS między LXC, firewall/ACL, HTTPS cutover bez zmian HA,
kontrolę IP/Workers/Pseudo IPv4, testy i rollback z zachowaniem prywatnych HA.
[Macierz wymagań i dowodów](docs/architecture/security-8b2-verification.md)
oddziela testy ASGI, prawdziwe Nginx/Uvicorn, walidator JWT cloudflared i
**BLOCKED / REQUIRES PHASE 9 VERIFICATION** dla rzeczywistego konta/Edge.
Brak nowej migracji; Alembic head pozostaje 20261025_08.

## Opcjonalny HTTPS i kontrolowany cutover (9B1/9B2)

Wybór `https` w instalatorze automatycznie przygotowuje zależności i nieaktywną
generację. Bezpieczny bootstrap blokuje start domyślnego Nginx przed apt;
nie przejmuje obcej konfiguracji. LAN pozostaje działający, także przy błędnym TLS.
Operator dostarcza cert/key/CA, dokładne IP/porty/hostnames i rzeczywiste dowody
izolacji IPv4/IPv6. Nie ma automatycznego Cloudflare/Access/DNS ani publicznego HTTPS.

Administracja istniejącą instalacją:

```bash
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py bootstrap
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py configure
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py plan
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py activate --evidence /etc/work-tracker/ingress-input/evidence.json
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py verify
# Niedokończona operacja / powrót do poprzedniego LAN:
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py recover
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py rollback-lan
```

[Runbook 9B2](docs/deployment/phase9b2-cutover.md) opisuje świeży LAN/HTTPS,
przygotowanie prywatnego JSON, bramki, okno serwisowe, kontrole z HA, rollback,
reboot, wymianę certyfikatu i checklistę 9C. Podczas zmiany listenera webhook
może zaginąć — HA nie musi ponowić żądania. URL i token HA pozostają bez zmian.
Narzędzie nie tworzy testowych eventów i nie odtwarza DB przy błędzie sieci.

9B2 komponuje [generacje i walidację 9B1](docs/deployment/phase9b1-nginx.md),
dodając trwały dziennik env/systemd, pełną kompensację i ordered boot recovery.
Dopiero po kontrolach i potwierdzeniu z rzeczywistego HA aktywuje autostart proxy.
Przygotowane generacje nie startują same. Zwykły updater nie przełącza profilu
ani generacji; wspólna blokada zapobiega równoległemu update/cutoverowi, a
niedokończony cutover blokuje update do czasu `recover`. W aktywnym splicie
updater zachowuje bazowy unit i przed merge odmawia wydania zmieniającego unit,
manifest lub wzorce ingressu. Wtedy wymagany jest jawny powrót do LAN i nowy cutover.
Nie wykonano deploymentu ani zmian Cloudflare/firewalla/HA.

## Logs/status/restart

```bash
sudo systemctl status work-tracker
sudo journalctl -u work-tracker -f
sudo systemctl restart work-tracker
```

## Backup

Updater zapisuje `/var/backups/work-tracker/update-<UTC>.<ID>/online.db` oraz, po potwierdzonym zatrzymaniu backendu, `pre-migration.db`. Ten sam prywatny katalog zawiera `unit.before`, staged checker i `transaction.json` z commitami/fazą. Katalog ma 0700, kopie 0600; nie są automatycznie usuwane ani odtwarzane. Starsze backupy `work_tracker-<timestamp-UTC>.db` pozostają bez zmian. Retencja jest wyłącznie ręczna: chroń wszystkie wskazane przez dzienniki/niepewne transakcje oraz najnowsze punkty odzyskania; [procedura](docs/deployment/phase9b2-cutover.md#ręczna-retencja-backupów). SQLite może normalnie wykonywać checkpoint i zarządzać WAL/SHM; gwarancją są zachowane zatwierdzone dane, nie czas życia lub inode sidecarów.

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
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
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
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-proxy-headers
```

Nie oznacza to publicznego dostępu: nie konfiguruj przekierowania portów, publicznego IP ani tunelu. Dostęp powinien być ograniczony do zaufanej sieci LAN przez konfigurację sieci serwera. Home Assistant nie wymaga CORS; CORS dotyczy wyłącznie przeglądarkowego frontendu. Jeśli frontend Vite działa pod innym adresem LAN, dodaj jego pełny origin do `CORS_ORIGINS` i `BROWSER_ALLOWED_ORIGINS`.

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

Zbiorczo: `bash deploy/tests/run_shell_tests.sh` z root repo (testy shell bez wywołania tunelu). Runner obsługuje `0=PASS`, `77=SKIP/NOT RUN`, inne kody jako FAIL, także przy `set -e`. Wypisuje osobne liczniki; zwraca 1 przy FAIL, 77 gdy wszystko pominięto, 0 gdy dostępne testy przeszły. Podsumowanie z SKIP nie oznacza pełnej walidacji.

Dodatkowe lokalne testy ingress 8B2 (z root repo; narzędzia mogą być wypakowane
poza checkoutem, testy nie uruchamiają tunelu):

```bash
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> backend/.venv/bin/pytest deploy/tests/ingress_security_test.py -q
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> backend/.venv/bin/pytest deploy/tests/nginx_provision_test.py deploy/tests/ingress_cutover_test.py deploy/tests/infrastructure_privacy_test.py -q
WORK_TRACKER_CLOUDFLARED_BIN=<CLOUDFLARED_BIN> backend/.venv/bin/pytest deploy/tests/cloudflared_config_test.py deploy/tests/ingress_config_test.py -q
CLOUDFLARED_SOURCE_DIR=<SOURCE_2026.10.0> WORK_TRACKER_GO_BIN=<GO_BIN> deploy/tests/cloudflared_access_test.sh
git diff --check
```

Brak rzeczywistych binarek jest błędem tych testów, nie cichym PASS/skip.
Testy 9B2 symulują apt/systemd/reboot; nie zastępują testu na nowym systemie.
Dodatkowe regresje audytu: `ingress_audit_test.py`, `host_validation_test.py`,
`split_update_test.py`, `update_readiness_test.py`, `update_safety_test.py`, `checker_migration_test.py`, `startup_stabilization_test.py` i `first_upgrade_test.py` w `deploy/tests/` (ten sam runner pytest).
Historyczny runtime v1 jest zapisany jako fixture’y z przypiętymi SHA-256;
testy nie potrzebują historycznych commitów PR. First-upgrade celowo wymaga
rzeczywistego drzewa bazowego Phase 9B1 i jego rodzica: użyj pełnej historii
(`fetch-depth: 0` w CI) albo jawnie przygotuj płytki checkout testowy według
[instrukcji fixture’ów](deploy/tests/fixtures/README.md). Brak historii to FAIL,
nie SKIP. `backend/.venv/bin/python deploy/tests/history_portability_check.py`
sprawdza te regresje w płytkim snapshotcie z jednym commitem, bez commita PR.
Test `service_user_runtime_test.sh` bez roota zwraca **77 = SKIP/NOT RUN**,
nie PASS. Przerwij raportowanie tego kroku jako sukcesu.
Opcjonalna prywatna denylista: `WT_PRIVATE_INFRASTRUCTURE_DENYLIST` wskazuje
plik 0600 poza checkoutem; skan prywatności nie drukuje dopasowanych wartości.
Heurystyki obejmują IP obu rodzin oraz hostname w URL i typowych polach konfiguracji;
nie zastępują ręcznego przeglądu ani prywatnej denylisty operatora.
Źródło/tag, checksum i granice dowodów opisuje macierz weryfikacji. Live probes
`deploy/tests/access_live_test.py` są przygotowane wyłącznie dla operatora
Phase 9 z prywatnymi fixtures; nie uruchamiać ich jako części lokalnego pytest.

## Endpointy

Poza sześcioma wyjątkami opisanymi powyżej wszystkie endpointy API wymagają session cookie. Klient skryptowy ordinary API musi najpierw zalogować się przez `/api/auth/login` i zachować cookie (np. `curl -c` / `curl -b` z prywatnym plikiem cookie poza checkoutem). Nie przekazuj hasła ani tokenu w URL.

- `POST /api/auth/login` — JSON username/password, nowa trwała sesja i HttpOnly cookie; ogólny błąd HTTP 401 dla nieprawidłowych credentials.
- `GET /api/auth/me` — read-only username i absolute expires_at; invalid/expired/revoked cookie zwraca HTTP 401. Bez zapisu aktywności i Set-Cookie.
- `POST /api/auth/heartbeat` — ważna sesja, dozwolony Origin i znacznik; puste body lub JSON `{}`; 200 username/expires_at, bez Set-Cookie. Odnawia idle najwyżej raz na godzinę, bez zmiany absolute expiry.
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

# Phase 8B1 — projekt architektury bezpieczeństwa

**Status: PLANNED — specyfikacja do późniejszej implementacji.**

Dokument definiuje cały zakres 8B1 po security review. Ten PR zmienia wyłącznie
dokumentację; żaden opisany tu nowy mechanizm nie jest przez niego uruchamiany.
Słowa „musi”, „odrzuca” i „zwraca” w kontraktach oznaczają wymagania docelowe.
Phase 8 pozostaje IN PROGRESS, produkcja LAN-only. Publiczne HTTPS i routing
należą do Phase 9 po review 8B2; ten projekt nie upoważnia do ich aktywacji.

## 1. Architecture overview i stan wyjściowy

Podstawą jest pobrany aktualny `main` oraz
[AGENTS.md](../../AGENTS.md), [PROJECT_STATUS.md](../../PROJECT_STATUS.md),
[ROADMAP.md](../../ROADMAP.md) i [README.md](../../README.md).
Dokładne zachowanie potwierdzają:

| Źródło | Obecna implementacja, przed 8B1 |
|---|---|
| `backend/app/auth.py` | Login, logout, SHA-256 tokenu, wspólny walidator sesji i centralny pure-ASGI default-deny guard. `GET /api/auth/me` wywołuje walidator z `touch_activity=True`: może zmieniać `last_seen_at`, ale nie rotuje i nie wysyła cookie. |
| `backend/app/main.py` | CORS otacza guard; sześć dokładnych wyjątków metody/ścieżki. HA ingestion/correction mają osobny `X-Webhook-Token`. FastAPI serwuje frontend i chronione `/api/docs`, `/api/redoc`, `/api/openapi.json`. |
| `backend/app/models.py`, Alembic | `users` i `user_sessions`; sesja ma `id, user_id, token_hash, created_at, last_seen_at, expires_at, revoked_at`. UTC jest zapisywane jako tekst stałej szerokości przez `UTCDateTime`. Head: `20261006_07`. |
| `backend/app/identity.py` | Username: ASCII, 3–64 znaki, trim spacji ASCII i lowercase. Hasło: 15–1024 punktów kodowych Unicode, bez trim/normalizacji, NUL i niepoprawnych surrogate. Argon2id: 64 MiB, 3 iteracje, 4 lanes. Dummy verification dla nieistniejącego konta. |
| `backend/app/config.py`, `deploy/config.manifest` | Istnieją `CORS_ORIGINS` i `SESSION_COOKIE_SECURE`; nie istnieją `BROWSER_ALLOWED_ORIGINS`, plik kluczy, liczniki logowania ani konfiguracja 8B1. Kod domyślnie wymaga Secure; manifest jawnie proponuje false dla LAN HTTP. |
| `frontend/src/auth.ts`, `App.tsx` | Gate przed domain UI, cookie credentials, globalna obsługa 401, ochrona przed starymi odpowiedziami. Widoczna karta odpytywana co 5 minut/focus używa GET /me jako heartbeat. Brak CSRF i recovery. |
| `backend/tests/test_auth.py`, `test_auth_enforcement.py`, `frontend/tests/auth.test.ts`, `authGate.test.mjs` | Pokrycie expiry, revocation, coarse activity writes, cookies/CORS, default-deny, HA, polling i starych odpowiedzi. To nie są testy nieistniejącego jeszcze 8B1. |

Nie należy interpretować obecnego określenia „świadomy GET heartbeat” jako ochrony
przed CSRF. Cross-site top-level GET może przenosić cookie SameSite=Lax.
Docelowa zmiana rozdziela odczyt sesji od jej podtrzymywania.
README i PROJECT_STATUS opisują obecne zachowanie; ten PR nie zmienia tych plików.
Ich aktualizacja będzie obowiązkiem PR implementacyjnego.

Docelowy przepływ:

~~~text
browser → response headers / restrictive CORS
        → centralny guard: klasa endpointu + sesja + Origin/CSRF
        → odczyt lub autoryzowana transakcja
login   → kontrola HTTP → admission IP → reservation konta → Argon2id → finalizacja
HA      → dokładny wyjątek machine → X-Webhook-Token → istniejąca domena
~~~

Ochrona obejmuje cały namespace HTTP `/api` i `/api/...` przed routingiem,
walidacją domenowego body i mountami. Nie powstaje lista endpointów, które trzeba
ręcznie dopisać do ochrony. Unsafe methods to wszystkie metody poza
`GET/HEAD/OPTIONS`; samo użycie innej metody nie tworzy wyjątku.
Nie wprowadza się WebSocketów, rejestracji, RBAC, MFA ani resetu haseł.

## 2. Threat model

Chronione zasoby: credentials, sesje, prywatne dane pracy/płac i raporty,
audytowalne korekty, ustawienia, dostępność logowania i niezależne ingestion HA.
Granice zaufania: przeglądarka ↔ FastAPI, FastAPI ↔ SQLite, operator ↔
konfiguracja/backup, HA ↔ dedykowane webhooki. Przyszły proxy nie jest dziś
zaufanym źródłem nagłówków ani adresu klienta.

| Przeciwnik / sytuacja | Wymagana odpowiedź |
|---|---|
| Obca strona, formularz, top-level navigation, sibling origin | Origin + CSRF + polityka metod, JSON/custom header dla loginu; GET nie odnawia sesji. CORS i Fetch Metadata są dodatkowymi warstwami. |
| Brute force, credential stuffing, rozproszone próby na jeden login | Trwały limit konta i IP przed Argon2id, bez różnic zależnych od istnienia konta. |
| Denial-of-service przez celowe wyczerpywanie limitu logowania | Czasowe utrudnienie nowych loginów pozostaje możliwe. Brak permanentnego lockoutu; istniejące prawidłowe sesje pozostają aktywne. |
| Kradzież tokenu lub backupu SQLite | Hash-only storage, revocation, expiry i ograniczony previous-token recovery. Kradzież używalnego cookie nadal umożliwia przejęcie sesji. |
| Wiele kart, utrata odpowiedzi, restart, spóźnione Set-Cookie | CAS, deterministyczne recovery tej samej generacji, brak automatycznego powtarzania wykonanych mutacji. |
| Spoofing adresu, IPv6 privacy addresses, Host/proxy headers | Normalizacja IP z transportu, IPv6 /64, jawne origins; brak zaufania do niezweryfikowanych forwarded headers. |
| XSS / przejęta przeglądarka / aktywny napastnik w LAN HTTP | CSP ogranicza część ataków, ale CSRF nie chroni przed XSS, a HTTP przed podsłuchem lub modyfikacją. To jawne ryzyko LAN, nie gotowość do Internetu. |

Przejęcie hosta/operatora lub jednoczesna kradzież kluczy i danych wykracza poza
gwarancje ochrony samej bazy. Nie zakłada się, że IP identyfikuje osobę.

## 3. Security invariants i podetapy

- Raw HA events pozostają immutable. Auth nie tworzy, nie usuwa ani nie
  rekonstruuje czasu pracy. Pipeline korekt/parowania/pay pozostaje bez zmian.
- HA używa tylko własnego `X-Webhook-Token`; browser cookie, Origin i CSRF
  nie są dodatkowymi wymaganiami ani zamiennikami tokenu HA.
- Ważność sesji: `created_at <= now < expires_at`, `revoked_at IS NULL`,
  `last_seen_at > now - 30 dni`. `expires_at = created_at + 180 dni`
  jest nieprzedłużalne. Na granicy deadline sesja jest nieważna.
  Last_seen_at/rotated_at w przyszłości oznaczają niespójność zegara:
  fail-closed 503 do jej wyjaśnienia, bez touch/rotacji.
- Wyłącznie chroniony POST heartbeat podtrzymuje idle, najwyżej raz na godzinę.
  Zwykłe odczyty, domain mutations, HA i polling nie odnawiają idle ani tokenu.
- Nowy token powstaje przy loginie albo rotacji; istniejący previous token
  służy tylko do bootstrapu/recovery przez mniej niż 10 minut.
- HMAC CSRF jest przypisany do stabilnej sesji, nie do rotującego cookie.
  Żadnych auth tokens w localStorage, URL, SQLite ani logach.
- Nieudany guard/finalizacja/commit nie daje sukcesu ani nowego cookie.
  Przegrany CAS nigdy nie publikuje niezapisanego kandydata: może jedynie
  odzyskać już zapisany current zgodnie z §7.3.
  Brak konfiguracji bezpieczeństwa nie otwiera browser API.

| Etap | Status | Dependencies i zakres |
|---|---|---|
| 8B1-A — HTTP Security & CSRF | PLANNED | Ukończone 8A1–8A3. Centralne polityki, read-only /me, POST heartbeat, CSRF, origins, login/logout CSRF, limit body, CORS, CSP/headers/cache, bootstrap i zarządzanie kluczami. Heartbeat początkowo bez rotacji. |
| 8B1-B — Login Rate Limiting | PLANNED | 8B1-A: spójny ingress, błędy i jawna tożsamość transportowa. Trwała maszyna throttlingu, normalizacja IP, transakcyjna rezerwacja przed Argon2id, monitoring 24 h. |
| 8B1-C — Session Rotation & Recovery | PLANNED | 8B1-A i 8B1-B: stabilne CSRF, chroniony heartbeat, ograniczone logowanie awaryjne. Dodatkowe pola sesji, CAS, rotacja/recovery oraz centralny klient frontendowy. |

## 4. Kontrakty endpointów

Wszystkie auth responses, także 401/403/409/413/415/422/429/503, mają
`Cache-Control: no-store`. Wspólny format błędu:
`{"detail":{"code":"…","message":"polski komunikat"}}`.
Nie ujawniać stanu konta, używalnych tokenów sesji ani przyczyn credential mismatch.

| Endpoint | Wymagania i wynik docelowy |
|---|---|
| `POST /api/auth/login` | Wyjątek wyłącznie od wymagania istniejącej sesji. Wymaga poprawnego Origin, `Content-Type: application/json`, `X-Work-Tracker-Request: 1`, kontroli Fetch Metadata, body ≤ 16384 bajty i throttlingu. Jeśli cookie identyfikuje bieżącą ważną sesję, wymaga również jej CSRF. Sukces 200: `username, expires_at, csrf_token, session_generation` i nowe cookie; każdorazowo nowa sesja. Credentials failure: ogólne 401; throttle: 429 + Retry-After. |
| `GET /api/auth/me` | Wyłącznie odczyt: bez UPDATE/INSERT/DELETE, `last_seen_at`, rotacji, cleanup, Set-Cookie i usuwania cookie. Bieżący ważny token: 200 z `username, expires_at, csrf_token, session_generation`. Previous w oknie: tylko 409 `session_rotation_required` z `csrf_token, session_generation, recovery_expires_at`, bez danych domenowych. Inny token: 401. |
| `POST /api/auth/heartbeat` | Sesja + Origin + CSRF + custom header. Dla current token: 200, activity UPDATE tylko gdy od zapisu minęła ≥ 1 h, a w 8B1-C rotacja gdy od `rotated_at` minęło ≥ 7 dni. Dla previous: wyłącznie tryb recovery opisany w §7; bez touch i bez nowej rotacji. Żadnego przesunięcia absolute expiry. |
| `POST /api/auth/logout` | Ważny current token + Origin + CSRF + custom header; 204 po atomowym revocation i usunięciu cookie. Previous najpierw wymaga recovery. Invalid/missing/expired/revoked daje 401 bez Set-Cookie. Kolejne wywołanie nie zmienia już stanu; nie gwarantuje starego anonimowego 204. |
| Zwykłe `/api/...` | Current token na każdej operacji, dodatkowo Origin + CSRF + custom header na unsafe methods. Previous w oknie: 409 `session_rotation_required` przed wykonaniem operacji, nigdy zwykły dostęp; poza oknem 401. Domain responses nie ustawiają cookie. |
| `GET /api/health` | Jedyny minimalny anonimowy health; bez wersji, konfiguracji, danych kont i sesji. Nie jest testem gotowości wszystkich mechanizmów auth. |
| `POST /api/webhook/home-assistant`, `POST /api/webhook/home-assistant/correction` | Tylko ich dedykowany token; bez cookie, CSRF, browser origins i limitu logowania. Brak lub błędny token nadal odrzucany. |
| CORS preflight | Wyłącznie rzeczywisty OPTIONS z poprawnymi nagłówkami preflight i dozwolonym origin/method/headers. Brak sesji dozwolony; nie wykonuje endpointu ani żadnego touch/recovery. |
| Publiczny HTML/JS/CSS/assets | Wyłącznie shell loginu i pliki statyczne; bez danych użytkownika i sekretów. Docs/schema nadal chronione. |

Heartbeat nie trafia do anonimowej listy wyjątków. Centralny guard ma jawnie
typowaną politykę: normalnie current-session; tylko dokładny POST heartbeat
dopuszcza także uwierzytelnienie previous-token recovery. GET /me ma własną
politykę read-only current/previous. Oba nadal wymagają ważnej, nieodwołanej
sesji i jej deadline'ów. Logout przestaje być session-independent.
Nie wolno wyjąć całego `/api/auth/*` spod ochrony.

Heartbeat przyjmuje ograniczony JSON `{"mode":"activity"}` albo, od 8B1-C,
`{"mode":"recover"}`; bez innych wartości/pól (422). Sukces activity ma
te same pola sesji co /me. Recovery zwraca tylko `csrf_token,
session_generation`; nie jest jeszcze potwierdzeniem dostępu do domain UI.
Login z rozpoznanym previous cookie w oknie zwraca 409 i wymaga wcześniejszego
recovery, zanim klient spróbuje zastąpić tę sesję nowym loginem.
Brak/nieznane/wygasłe cookie nie przeszkadza w niezależnym logowaniu hasłem.
`session_rotation_required` to zastrzeżony kod 409 gwarantujący brak efektu
domenowego; zwykły błąd sesji ma kod `session_invalid` (401), polityki
`origin_rejected/csrf_rejected` (403), a limiter `login_throttled` (429).
Na ordinary 409 nie zwracać CSRF ani danych sesji — bootstrap tylko przez /me.

### 4.1. HTTP body a znaki hasła

Limit body logowania wynosi **16 KiB = 16384 bajty**, niezależnie od liczby
znaków. Limit musi działać w warstwie ASGI przed pełnym buforowaniem/parsingiem
JSON, wyszukaniem credentials i Argon2id. `Content-Length > 16384` daje 413;
rzeczywiście odebrane fragmenty też są sumowane, nawet bez Content-Length
lub przy jego błędnej deklaracji. Przekroczenie daje 413, bez dalszego odczytu
nieograniczonego strumienia. Nie polegać wyłącznie na Content-Length.
Body o rozmiarze dokładnie 16384 bajty przechodzi tę kontrolę.
Nieobsługiwane `Content-Encoding` odrzucać 415 przed dekompresją, aby nie
tworzyć obejścia limitu. Błędy framingu transport HTTP odrzuca wcześniej.

Po ograniczeniu bajtów parsować UTF-8 JSON zgodnie z RFC 8259, odrzucać
duplikaty pól credentials, niewłaściwe typy i niepoprawny JSON (422).
Następnie walidować hasło wspólnymi regułami identity: 15–1024 punktów
kodowych Unicode po unescape, bez trim, normalizacji i obcięcia; bez NUL
i nieparzystych surrogate. Niepoprawne credentials tekstowe dają ogólne 401,
bez Argon2id i bez zwiększenia liczby błędnych weryfikacji hasła.

1024 emoji to 1024 znaki mimo 4096 bajtów UTF-8 i 2048 jednostek UTF-16.
Zapis przez 1024 pary `\uD83D\uDE00` zajmuje 12288 bajtów samych escape'ów
i mieści się z normalną kopertą JSON w 16 KiB. Test musi zbudować rzeczywisty
escaped JSON, a nie tylko przesłać literalne emoji przez domyślny serializer.
Podobnie 1024 polskie znaki zapisane jako `\uXXXX` muszą być akceptowane.
Whitespace/dodatkowe pola mogą przekroczyć 16 KiB: wtedy 413 nawet przy
prawidłowym haśle. Limit HTTP nie obiecuje przyjęcia każdego możliwego
rozdmuchanego zapisu tego samego JSON.

## 5. 8B1-A — CSRF, Origin i HTTP Security

### 5.1. Jawne origins, CORS i Fetch Metadata

Planowane `BROWSER_ALLOWED_ORIGINS` jest wymaganym, niepustym zbiorem origins
uprawnionych do browser API. Każdy element to dokładny schemat HTTP/HTTPS,
host i efektywny port, bez ścieżki/query/fragmentu/credentials. Walidacja
startup odrzuca wildcard, `null`, niepoprawny URL i niejednoznaczne wpisy.
Kanonizacja: lowercase schematu/DNS, kanoniczny IP/IDNA, port domyślny zgodny
ze schematem. Dopasowanie całej krotki, nigdy suffix, regex ani substring.
Jawnie skonfigurowane prywatne IPv4/IPv6 są dozwolone; nie cały zakres LAN.

Przykład docelowej konfiguracji, **nie istniejących jeszcze ustawień**:

~~~dotenv
# LAN production, frontend i API z jednego originu
BROWSER_ALLOWED_ORIGINS=http://192.168.1.50:8000
CORS_ORIGINS=
SESSION_COOKIE_SECURE=false

# Wariant stabilnego lokalnego DNS zamiast powyższego IP:
# BROWSER_ALLOWED_ORIGINS=http://work-tracker.home.arpa:8000

# Osobny profil developerski, ten sam hostname, różne porty:
# BROWSER_ALLOWED_ORIGINS=http://localhost:8000,http://localhost:5173
# CORS_ORIGINS=http://localhost:5173

# Wyłącznie przyszły przykład HTTPS po 8B2/Phase 9, bez jego aktywacji:
# BROWSER_ALLOWED_ORIGINS=https://work.example.com
# CORS_ORIGINS=
# SESSION_COOKIE_SECURE=true
~~~

`CORS_ORIGINS` pozostaje oddzielnym, co najwyżej równym podzbiorem
`BROWSER_ALLOWED_ORIGINS` potrzebnym do cross-origin frontend fetch.
Same-origin production nadal wymaga BROWSER_ALLOWED_ORIGINS mimo pustego CORS.
Credentialed CORS nie odbija dowolnego Origin. Dozwolone metody są jawne:
GET, POST, PUT, DELETE oraz obsługa OPTIONS; rozszerzenie wymaga review.
Browser headers: `Content-Type, X-CSRF-Token, X-Work-Tracker-Request`;
zachować istniejący `X-Webhook-Token` bez udzielania mu browser-session uprawnień.
Udostępnić JS `Retry-After`; `Set-Cookie` nie jest czytelne przez JS.
Dynamiczne odpowiedzi CORS muszą zawierać `Vary: Origin`.

Każda browser mutacja, login i heartbeat wymaga dokładnie jednego poprawnego,
dozwolonego Origin. Brak Origin, `Origin: null`, lista/wiele nagłówków,
niedozwolony origin dają 403 przed mutacją/Argon2id. Nie stosować fallbacku
do Referer/Host/forwarded headers ani automatycznie dopisywać nowego IP.
Klient skryptowy ordinary API też musi podać skonfigurowany Origin i CSRF;
nie tworzy się ścieżki omijającej browser policy na podstawie User-Agent.
Read-only GET może nie mieć Origin; jeśli go podano, musi być dozwolony.

`Sec-Fetch-Site: cross-site` dodatkowo odrzuca browser API; `same-site`
nie wystarcza do autoryzacji sibling originu. `same-origin` i `none` nie
zastępują Origin/CSRF. Unsafe request z `Sec-Fetch-Mode: navigate` lub
`Sec-Fetch-Dest` innym niż `empty` jest odrzucany, jeśli te pola występują.
Nieprawidłowe/wielokrotne wartości odrzucać. **Brak Sec-Fetch-* jest wspierany
w LAN HTTP** i starszych klientach: pozostają obowiązkowe Origin/CSRF.
Nie można uzależnić bezpieczeństwa od obecności tych nagłówków.
Cross-site top-level GET /me z lub bez metadata nie może zapisać niczego
w sesji ani wygenerować Set-Cookie, niezależnie od tego, czy zostanie odrzucony.

Adres serwera powinien mieć rezerwację DHCP albo stabilny lokalny DNS.
Zmiana hosta/IP/schematu/portu może wymagać zmiany allowlist i spowodować 403
przy logowaniu, logout, heartbeat lub mutacjach; sama dostępność strony
nie potwierdza poprawnej konfiguracji origins. Procedura zmiany jest w §10.
Nie wyznaczać zaufanego originu ani client IP z niezweryfikowanych
`Forwarded`, `X-Forwarded-*` lub `CF-Connecting-IP`.
Review zaufanej ścieżki proxy pozostaje w 8B2, bez aktywacji proxy w 8B1.

### 5.2. Session-bound HMAC CSRF i bootstrap

Używać HMAC-SHA-256 z niezależnym `K_csrf`, wersjonowanego kodowania z §7.1
i stabilnych danych sesji:

~~~text
C = HMAC(K_csrf, encode("session-csrf", U64(session_id), csrf_nonce))
csrf_token = "v1." || base64url_without_padding(C)
~~~

`csrf_nonce` to 32 losowe bajty utworzone raz na sesję; nie jest bearer
credential. Baza przechowuje nonce i identyfikator klucza, nigdy klucz ani
używalny token. CSRF nie zależy od generacji ani current/previous cookie.
Pozostaje taki sam podczas rotacji i recovery, zmienia się przy nowym loginie.
Porównanie stałoczasowe, ścisły format i długość; odrzucać brak/wiele tokenów.
Przesyłać wyłącznie `X-CSRF-Token` z pamięci klienta, nie z URL lub cookie.
To serwerowo wyprowadzany session-bound token; nie stosować unsigned
double-submit cookie ani tokenu powiązanego tylko z user_id.

Bootstrap:

1. Przed loginem nie istnieje sesja do powiązania z HMAC. Login CSRF chronią
   obowiązkowy dozwolony Origin, JSON, custom header i restrictive CORS,
   dodatkowo Fetch Metadata. Jeśli login zastępuje ważną bieżącą sesję,
   wymagany jest też jej CSRF. Nie tworzyć anonimowej „sesji recovery”.
2. Udany login zwraca CSRF w no-store JSON i ustawia wyłącznie auth cookie.
   Frontend przechowuje CSRF w pamięci i wstrzymuje mutacje do bootstrapu.
3. Reload z current cookie: read-only GET /me dostarcza ten sam CSRF,
   bez heartbeat, touch lub Set-Cookie.
4. Reload z previous cookie w oknie: read-only GET /me zwraca ograniczony
   409 z CSRF tej samej sesji. Klient może już wysłać POST heartbeat
   z `{"mode":"recover"}`, Origin, custom header i otrzymanym CSRF.
   Nie trzeba wcześniej znać current tokenu ani mieć CSRF w storage.
5. Po recovery klient ponownie czyta GET /me, potwierdza current cookie
   i dopiero wtedy otwiera domain UI/ponawia dozwoloną operację.
   Zwykły 409 nie powoduje automatycznego globalnego logout.

Nie ma cyklu „recovery wymaga CSRF, którego można uzyskać dopiero po recovery”:
previous daje wyłącznie read-only bootstrap CSRF i chroniony recovery, a nie
zwykłe dane czy operacje. Obca strona nie może odczytać odpowiedzi przy
restrykcyjnym CORS/SOP; bez poprawnego POST nie zmieni cookie ani sesji.
Po końcu okna GET nie zwraca recovery CSRF ani informacji o sesji.

### 5.3. Heartbeat i świadoma aktywność

Frontend wywołuje heartbeat wyłącznie dla zalogowanej, widocznej karty po
świadomej aktywności: zaufany event interakcji (pointer/keyboard/touch)
lub świadomy focus/powrót do widocznej karty. Co najwyżej raz na 5 minut,
z jednym żądaniem w toku; po powrocie może wykonać jedno od razu.
Okresowy timer może wysłać heartbeat tylko jeśli od poprzedniego wysłania
zarejestrowano taką aktywność i karta nadal jest widoczna. Sam upływ czasu,
dashboard polling, odbiór odpowiedzi, zamontowanie ukrytej karty lub syntetyczny
event nie odnawia idle. Read-only bootstrap po reload jest niezależny.
Tryb recovery również rozpoczyna tylko widoczna karta; ukryta czeka.

Serwer nie może kryptograficznie dowieść widoczności karty; wymusza sesję,
Origin/CSRF, deadlines i conditional UPDATE. `last_seen_at <= now - 1 h`
jest warunkiem zapisu, razem z nadal ważną sesją; czas nie cofa się.
Coalescing może pominąć mniej niż godzinę końcowej aktywności przed zamknięciem.
Sukces domain mutacji sam nie zastępuje tego kontraktu.

### 5.4. CSP, nagłówki i cache

Docelowy bazowy CSP dla produkcyjnego shell:

~~~text
default-src 'none'; script-src 'self'; style-src 'self';
img-src 'self'; font-src 'self'; connect-src 'self';
object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
~~~

Przed enforcementem sprawdzić rzeczywisty build: brak inline script/style,
eval i zasobów CDN. Nie dodawać `unsafe-inline`, `unsafe-eval` ani wildcard
w celu uciszenia błędów. Ewentualny nieunikniony inline fragment wymaga
konkretnego hash/nonce i review. Nie stosować `upgrade-insecure-requests`
w LAN HTTP. Vite dev/HMR ma osobny, jawny profil developerski dla dokładnych
origins, bez osłabiania production policy.

Obecne Swagger/ReDoc generują HTML/JS korzystające z zewnętrznych zasobów
i inline bootstrap, co koliduje z powyższym CSP. Docelowo 8B1-A zachowuje
chronione docs/schema, serwuje ich zasoby lokalnie i nadaje własnemu
bootstrapowi jednorazowy nonce (co najmniej 128 bitów CSPRNG na odpowiedź).
Wyłącznie CSP odpowiedzi docs dopuszcza ten konkretny nonce skryptu/stylu;
nie przyznawać CDN, unsafe-inline/eval ani wyjątku całej aplikacji.
Tę różnicę względem obecnej implementacji trzeba wskazać w PR 8B1-A
i potwierdzić testem rzeczywistych Swagger/ReDoc, również stylów runtime.
Jeśli dostarczona biblioteka nie potrafi spełnić tej polityki, zgłosić
blokadę/nową decyzję architektoniczną zamiast automatycznie osłabiać CSP.

Na wszystkich odpowiedziach, także statycznych i błędach guardu:
`X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
`Permissions-Policy: camera=(), microphone=(), geolocation=()`.
Nie włączać przestarzałego X-XSS-Protection. HSTS dopiero dla zatwierdzonego
HTTPS po 8B2/Phase 9; nie ustawiać go na LAN HTTP.

Auth, prywatne API, docs/schema, CSV/PDF oraz ich błędy: `Cache-Control:
no-store`; nie wystarczy `no-cache` lub `private`. Login shell/index:
no-store, żeby uniknąć starego klienta po cutoverze. Publiczne pliki o nazwach
z content hash mogą mieć `public, max-age=31536000, immutable`, wyłącznie
jeśli nie zawierają danych/sekretów. Pozostałe statyczne pliki wymagają
revalidation. Nie przechowywać auth/domain responses w service worker/Cache API.
Frontend po logout/401 usuwa dane, CSRF i stan recovery; zachowuje istniejącą
ochronę przed starymi odpowiedziami i dekodowaniem body/blob.

## 6. 8B1-B — Login throttling state machine

### 6.1. Klucze i czas

Źródłem client IP w obecnym LAN jest adres peer z transportu ASGI.
Uvicorn nie może wcześniej bezwarunkowo przepisać go z proxy headers:
implementacja musi jawnie wyłączyć niezatwierdzone proxy-header processing,
także dla lokalnego peera. Sama ignorancja nagłówków wewnątrz handlera
nie wystarcza, jeśli serwer wcześniej zmienił `scope.client`.
Brak poprawnego peer IP daje 503 dla loginu, nigdy wspólny arbitralny
fallback omijający limit. Nie używać IP z body, Origin lub DNS.

Deterministyczna normalizacja przed utworzeniem klucza:

1. Parsować literalny adres IP jako wartość binarną; port transportu
   nie jest częścią adresu. Odrzucać niepoprawny adres i nieobsługiwany zone ID.
2. IPv4-mapped IPv6 (`::ffff:192.0.2.9`) sprowadzić do IPv4.
3. IPv4 → 32 bity adresu i prefix **/32**.
4. Natywne IPv6 → pierwsze 64 bity, pozostałe wyzerowane, prefix **/64**.
5. Klucz ma typ/family, prefix length i binarne network bytes
   (jednoznacznie zakodowane). Tekst w audycie używa kanonicznej postaci;
   różne skrócenia IPv6 muszą wskazać ten sam klucz.

Zmiana interface identifier w tym samym IPv6 /64 nie daje nowego limitu.
/64 może obejmować wiele urządzeń, a IPv4 NAT wiele osób; oba mają wspólny
budżet IP. Zmiana prefiksu albo rozproszony atak nadal może obchodzić limit IP.
To ogranicznik sieciowy, nie identyfikator użytkownika.

Klucz konta: `HMAC(K_throttle, encode("login-account", canonical_username))`.
Normalizacja jak w identity; nie zależy od lookupu `users`.
Każdy poprawny składniowo nieistniejący login ma taki sam trwały stan
jak istniejący. Niepoprawne nazwy używają wspólnego, rozdzielonego od
poprawnych nazw sentinel `0x00 || ASCII("invalid-username")`.
Nigdy nie zapisywać hasła ani jawnego badanego username w limiterze.

Wszystkie czasy są UTC o precyzji mikrosekund. Okno ruchome
`W(t) = (t - 900 s, t]`; zdarzenie dokładnie sprzed 900 s już nie liczy się.
Jedno `now` dla każdej transakcji. Liczenie wyłącznie z trwałych zdarzeń
i aktywnych rezerwacji, nie z process-local cache.
Jeżeli dla badanego klucza zapisany attempt/failure/reserved_at jest
w przyszłości względem now, odmówić admission/finalizacji (503) do naprawy
czasu; nie wolno po prostu wykluczyć future rows z W(t) i otworzyć limitu.

### 6.2. Limity i deterministyczna kara

IP: **30 prób w 15 minut**, niezależnie od wyniku, także poprawne loginy.
Konto: maksymalnie **8 faktycznie błędnych weryfikacji hasła w 15 minut**.
Przerwa po zakończeniu błędnej weryfikacji zależy od aktualnej liczby
błędów w W(t), łącznie z właśnie zapisanym błędem:

| Liczba błędów | Przerwa po błędzie |
|---|---|
| 1–3 | 0 s |
| 4 | 30 s |
| 5 | 60 s |
| 6 | 120 s |
| 7 | 240 s |
| 8 | 300 s |

Finalizacja ustawia `blocked_until = max(stare blocked_until, t + kara[F])`.
Admission jest dozwolone dopiero, gdy **oba** warunki są spełnione:
`F < 8` oraz `now >= blocked_until`, dodatkowo limit IP i brak zajętej
rezerwacji. Przy ośmiu błędach dalsza próba jest odrzucana przed Argon2id,
nawet jeśli 300 sekund już upłynęło. Czeka także na wyjście błędu z okna.
300 s ogranicza karę progresywną; nie obiecuje, że całe oczekiwanie nigdy
nie przekroczy 300 s. Limit ruchomy może blokować dłużej.

Projekt **nie przewiduje osobnego twardego limitu dobowego**.
Wymagany jest monitoring podejrzanej aktywności z ostatnich **24 godzin**,
także rozproszonej i wolnej, mieszczącej się w limitach 15-minutowych.

### 6.3. Admission → reservation → Argon2id → finalizacja

1. **Kontrole HTTP:** Origin/metadata/custom header/media type i streaming
   body cap z §4–5. Odrzucone 403/413/415 nie są próbami credentials
   w limiterze; agregować ich metryki bezpieczeństwa. Nie wykonywać Argon2id.
2. **Admission IP:** krótka transakcja `BEGIN IMMEDIATE` w SQLite.
   Liczyć przyjęte próby dla znormalizowanego IP w W(now).
   Jeśli jest 30, zwrócić 429 bez Argon2id i bez nowego wpisu konsumującego
   budżet IP. Jeśli jest mniej, zapisać jedną próbę i commit.
   Dalej nalicza się ona niezależnie od parse/validation, nieznanego konta,
   błędnego lub poprawnego hasła, blokady konta albo błędu przetwarzania.
   Równoległy 31. request nie może przejść. Nie przechowywać całego body.
3. **Parse i reservation konta:** po JSON i taniej walidacji wyznaczyć
   account key. Niepoprawny JSON/typ daje 422, niepoprawne hasło tekstowe
   ogólne 401, bez account failure. Poprawny input: kolejna krótka
   `BEGIN IMMEDIATE` wyznacza F i blocked_until, sprawdza/rezerwuje slot.
   Na konto dopuszczalna jest **jedna aktywna rezerwacja**. Umożliwia to
   deterministyczną eskalację po każdym błędzie i uniemożliwia równoległym
   requestom ominięcie przerwy 4./5./… błędu. Warunek `F + R < 8`
   przed nową rezerwacją uwzględnia już zajęte sloty.
   Zajęta rezerwacja/limit/kara → 429, bez sprawdzania hasła.
   Rezerwacja ma losowy request ID, owner ID i lease **60 s**, zapisane
   w SQLite przed Argon2id; transakcja kończy się commit.
4. **Argon2id:** poza transakcją SQLite i bez blokady write/read.
   Dopiero teraz lookup credentials, zakończenie read transaction i
   weryfikacja realnego lub dummy hasha o zgodnej polityce parametrów.
   Nie wolno użyć dummy ścieżki omijającej reservation.
   Potrzebny rehash wykonuje się w tym samym budżecie/rezerwacji, poza
   lockiem; CAS hasha użytkownika zachowuje obecną ochronę przed nadpisaniem.
   Zachować obecną semantykę przegranego CAS rehash: odczytać aktualny hash,
   zakończyć transakcję i ponownie zweryfikować podane hasło, nadal w ramach
   tej samej ważnej rezerwacji. Nie nadpisywać równoległej zmiany hasha.
   Dalsza zmiana credentials lub utrata lease kończy próbę bez sesji;
   nie wykonywać nieograniczonej pętli rehash/verification.
5. **Finalizacja:** krótka `BEGIN IMMEDIATE`; CAS po request ID/owner
   i nadal ważnym lease. Sukces weryfikacji i nadal aktualne credentials
   atomowo tworzą nową sesję i kończą rezerwację. Dopiero po commit
   odpowiedź 200 z Set-Cookie. Błąd hasła (także dummy dla nieistniejącej
   nazwy) dopisuje jeden failure z czasem finalizacji, przelicza F/karę,
   kończy reservation i commit; zwraca ogólne 401. Dla F ≥ 4 także
   Retry-After. Sam konflikt CAS credentials nie jest błędną weryfikacją;
   wykryte błędne hasło liczy się raz na próbę, także po recheck.
   Błąd infrastruktury lub nierozstrzygnięty konflikt credentials nie tworzy
   sesji, kończy reservation i daje ogólne 503.
   Żadnych pozytywnych odpowiedzi przy niepewnym commit.

Po udanym loginie **nie kasować** bieżących failures ani blocked_until;
starzeją się według W(t). Zapobiega to resetowi budżetu przez współbieżny
lub kontrolowany udany login. Nie resetować licznika IP. Udać może się
wyłącznie próba przyjęta po upływie obowiązującej przerwy.

Zablokowane próby nie są błędnymi weryfikacjami, nie podnoszą F, nie
eskalują/przesuwają blocked_until. Jeśli przeszły admission IP, liczą się
raz w IP; próby zablokowane już przez IP zwiększają tylko telemetrykę
odrzuceń, nie wydłużają ruchomego okna limitu. Te reguły obowiązują
również dla wielu równoległych requestów.

### 6.4. Retry-After, restart i awarie

Wyznaczyć najwcześniejszy czas dopuszczenia przy założeniu braku nowych prób:

~~~text
d_ip      = najstarsza z 30 aktywnych prób IP + 900 s, jeśli IP pełne; inaczej now
d_count   = najstarszy z 8 aktywnych failures + 900 s, jeśli F = 8; inaczej now
d_penalty = max(now, blocked_until)
d_lease   = lease_expires_at zajętej rezerwacji; inaczej now
Retry-After = max(1, ceil((max(właściwe znane deadlines) - now) / 1 s))
~~~

Dla blokady na etapie IP znany jest tylko d_ip, bo nie przeprowadza się
dalszej analizy konta. Dla blokady konta użyć wszystkich już znanych deadlines.
Dla odpowiedzi po właśnie błędnej weryfikacji uwzględnić także nowy stan.
Jeśli lease skończy się wcześniej po finalizacji, retry może udać się wcześniej;
Retry-After nie jest rezerwacją ani gwarancją sukcesu. Zwracać delta-seconds,
nie liczbę pozostałych prób i nie nazwę aktywnego ogranicznika.
Frontend pokazuje ogólny polski komunikat i nie wykonuje automatycznego
ponawiania hasła w pętli.

Lease jest ważny wyłącznie przy `now < lease_expires_at`; na granicy
nie wolno finalizować starej rezerwacji.
Restart procesu nie zeruje prób, failures, kar ani lease. Nowy worker
respektuje istniejące lease do deadline; po jego wygaśnięciu krótka
transakcja może oznaczyć reservation jako porzucone. Wynik starego workera
po utracie lease nie może dopisać failure, sesji ani cookie.
Porzucona próba pozostaje w IP, lecz nie udaje znanego błędu hasła.
Lease nie jest automatycznie odnawiany bez końca. Liczba prac Argon2
w procesie musi być ograniczona do dostępnych zasobów; przeciążenie lub
timeout daje 503, nigdy pominięcie kontroli. Wygaśnięcie lease nie dowodzi,
że biblioteczny wątek CPU już się zatrzymał.

SQLite busy/lock timeout lub błąd zapisu daje 503 bez wykonania nowego
Argon2id, jeśli nie udało się potwierdzić reservation. Finalizacja po
awarii nie daje 200. Cleanup odbywa się tylko w transakcjach writer/maintenance,
nigdy w GET /me. Retencja nie może usuwać zdarzeń z aktywnych okien/lease.

Nieistniejące konta przechodzą te same stany i kary; status/body i sposób
wyliczenia Retry-After nie zależą od ich istnienia. Nieprawidłowa nazwa
nie dostaje informacji walidacyjnej ujawniającej konto. Testować również
timing real/dummy, ale nie obiecywać matematycznie stałego czasu całego HTTP.

## 7. 8B1-C — Session rotation and recovery state machine

### 7.1. Kodowanie i separacja kryptograficzna

HMAC-SHA-256 zgodnie z RFC 2104. `K_rotation`, `K_csrf`, `K_throttle`
to trzy niezależne 32-bajtowe klucze CSPRNG, nie WEBHOOK_TOKEN ani hasło.
Każdy algorytm ma osobny klucz i context. Format `encode` v1:

~~~text
ASCII("WT8B1") || 0x00 || 0x01
|| U16BE(length(context)) || ASCII(context)
|| U16BE(number_of_fields)
|| concatenate(U32BE(length(field)) || field)
~~~

Długości oznaczają bajty, nie znaki; liczby bez znaku, big-endian.
Session ID i generation są polami po 8 bajtów U64BE (obecne dodatnie
SQLite id mieszczą się w tym zakresie). Contexty: `session-rotation`,
`session-csrf`, `login-account`. Nie używać konkatenacji stringów,
JSON, separatorów o zmiennej interpretacji ani platformowego endian.
Wersja kodowania i identyfikator zestawu kluczy są przypisane do sesji;
zmiana formatu/klucza nie jest niejawnie negocjowana danymi od klienta.

`T(g)` we wzorach to dokładnie 32 surowe bajty. Cookie jest ich
kanonicznym base64url bez paddingu (43 znaki); wejście musi dekodować się
i ponownie kodować do identycznego stringu. Hash w SQLite zachowuje
obecną konwencję: `SHA256(ASCII(cookie))`, nie hash surowych 32 bajtów.

**Nowa rotacja** przy aktualnej generacji g:

~~~text
T(g+1) = HMAC(K_rotation,
              encode("session-rotation", U64(session_id), U64(g+1), T(g)))
~~~

**Recovery istniejącego current tokenu**:

~~~text
T_current = HMAC(K_rotation,
                 encode("session-rotation", U64(session_id),
                        U64(persisted_generation), T_previous))
~~~

W recovery używa się **już zapisanej** generacji, bez inkrementacji.
Odtworzony cookie hash musi zgadzać się z zapisanym current hash;
niezgodność daje fail-closed 503 i redacted alarm, nie kolejny token.

### 7.2. Stany i granice

| Stan tokenu żądania | GET /me | Ordinary API / logout | POST heartbeat |
|---|---|---|---|
| Current, sesja ważna | 200, read-only bootstrap | Dostęp zgodny z Origin/CSRF | Touch/rotacja w trybie activity; recover może potwierdzić current bez touch |
| Previous, sesja ważna i `now < previous_valid_until` | Ograniczony 409 + CSRF | 409 `session_rotation_required`, operacja niewykonana | Tylko `mode=recover`, Origin/CSRF, deterministyczne odtworzenie current |
| Previous dokładnie na deadline lub później, starsza generacja, inny token | 401, bez danych i cookie | 401 | 401 |
| Revoked, idle/absolute expired, brak użytkownika | 401, bez danych i cookie | 401 | 401, bez recovery i touch |

Nowa sesja: generation 0, rotated_at=created_at, previous puste.
Rotacja staje się należna przy `now >= rotated_at + 7 dni`, ale wykonuje ją
wyłącznie activity heartbeat. Current token pozostaje ważny po siedmiu dniach,
jeśli nie było świadomej aktywności; expiry nadal obowiązuje.
Nie tworzyć wymogu relogin tylko dlatego, że przez tydzień nie było heartbeat.

W trybie `activity` previous daje 409 przed touch. W trybie `recover`
current daje 200 bez touch, rotacji i Set-Cookie — inna karta mogła już
odzyskać wspólne cookie. Previous daje tylko current Set-Cookie po walidacji;
nie zmienia generation, rotated_at, last_seen_at ani previous_valid_until.
Odpowiedź 200 recovery zawiera CSRF i generation, a klient potwierdza ją /me.
Recovery nie jest dowodem nowej aktywności; następny świadomy heartbeat
może osobno odnowić idle.

### 7.3. Atomic CAS i przegrany CAS

Activity heartbeat z current:

1. Zweryfikować sesję, CSRF i Origin. Odczytać ID, current hash, g,
   rotated_at i deadlines; zakończyć read transaction.
2. Jeśli rotacja należna, wyprowadzić T(g+1) w pamięci.
3. W krótkiej transakcji writer wykonać conditional UPDATE z warunkami:
   ID, generation=g, token_hash=hash(T(g)), rotacja nadal należna,
   brak revocation i nadal poprawne idle/absolute deadlines. W tym samym
   zapisie przesunąć current hash do previous hash, ustawić current hash,
   generation=g+1, rotated_at=now,
   `previous_valid_until=min(now+600 s, expires_at)`;
   touch tylko jeśli należny. Commit przed Set-Cookie.
4. Dokładnie jeden request wygrywa CAS. Przegrany ponownie czyta stan:
   jeśli jego token jest teraz immediate previous i okno trwa, wyprowadza
   current z **persisted_generation**, porównuje hash i zwraca dokładnie
   to samo current cookie. Nie przeprowadza drugiej rotacji ani touch.
   Inna generacja/revocation/expiry kończy się 401/409 według stanu,
   bez Set-Cookie i bez wymuszania nowego CAS w pętli.

CAS i recovery muszą ponownie sprawdzić deadlines/revocation przy ostatecznej
transakcyjnej decyzji. Żaden previous hash nie jest akceptowany jako current.
Wszyscy workerzy korzystają z tych samych kluczy i bazy; restart nie zmienia
wyprowadzanego wyniku ani końca okna. Ponowne recovery nie przedłuża 10 minut.
Następna rotacja zastępuje previous, bez listy starszych tokenów.

Cookie zawsze host-only, HttpOnly, SameSite=Lax, Path=/ i Secure zgodnie
z jawnym profilem; `Expires` nadal równe pierwotnemu expires_at,
`Max-Age` najwyżej liczba pozostałych sekund (bez zaokrąglenia w górę).
Nie ustawiać od nowa 180 dni podczas rotacji/recovery.

### 7.4. Wiele kart, mutacje i ograniczenia HTTP

Frontend centralizuje obsługę 409 i pojedynczy recovery w karcie; zatrzymuje
nowe mutacje do jego zakończenia. Web Locks/BroadcastChannel mogą koordynować
karty i przekazywać sygnał generacji/logout, ale nie są warunkiem poprawności
serwera i nie przesyłają auth tokenów. Przeglądarka sama współdzieli HttpOnly
cookie. Algorytm działa także bez Web Locks, BroadcastChannel i persistent
storage, w tym po reload.

Rotacja zachodzi tylko w heartbeat, więc odpowiedź zwykłej mutacji nigdy
nie niesie zmiany auth cookie. Guard oraz **ponowna kontrola current tokenu
w tej samej transakcji writer co zapis domenowy** zapobiegają TOCTOU:
jeśli rotacja/revocation wygrała wcześniej, mutacja kończy się przed swoim
zapisem; jeśli mutacja wygrała wcześniej, jej commit jest jeden.
Nie trzymać write locka podczas odczytu sieci, Argon2id lub generowania PDF.
Prawidłowo autoryzowany odczyt rozpoczęty przed revocation może zakończyć się
później; nie może ustawić cookie ani przywrócić danych w nowszym auth lifetime.

Automatyczne ponowienie mutacji dopuszczalne jest **raz**, wyłącznie po
jednoznacznym 409 `session_rotation_required`, które gwarantuje, że handler
nie wykonał żadnych efektów, i po potwierdzonym recovery/current /me.
Generic 409 (np. konflikt korekty), timeout, zerwane połączenie, 5xx lub
utrata odpowiedzi po commit nie dają tej gwarancji: nie powtarzać automatycznie
POST/PUT/DELETE, najpierw odczytać stan i umożliwić świadome ponowienie.
Ten projekt nie obiecuje exactly-once dla dowolnych utraconych odpowiedzi
biznesowych i nie dodaje odrębnego systemu idempotency keys.

Utracona odpowiedź rotacyjna pozostawia w przeglądarce previous; recovery
w tym samym 10-minutowym oknie odtwarza identyczny current.
Spóźnione odpowiedzi z równoległej rotacji/recovery tej samej generacji
ustawiają identyczne cookie. Jednak JS **nie może odrzucić Set-Cookie**
z już odebranej odpowiedzi na podstawie swojej wersji stanu.
Odpowiedź opóźniona ponad kolejną rotację może cofnąć cookie do immediate
previous; wtedy działa tylko nowe okno recovery. Jeszcze starszy token,
opóźnienie po oknie, nowe logowanie lub logout mogą wymagać ponownego loginu.
Przed login/logout klient w miarę możliwości opróżnia/anuluje auth requests,
lecz abort nie gwarantuje zatrzymania obsługi cookie przez przeglądarkę.

Stare cookie nie może wskrzesić revoked sesji. Spóźnione usunięcie cookie
po logout może nadpisać nowy login w innej karcie: odzyskiwanie stanu UI
nie gwarantuje odzyskania cookie. Akceptowany jest bezpieczny relogin,
nigdy wydłużenie okna lub dopuszczenie previous do domain API.
Po **10 minutach nie ma gwarancji odzyskania bez ponownego logowania**.

### 7.5. Deterministyczne test vectors

Poniższe bajty są wyłącznie publicznymi danymi testowymi, nie kluczami deploymentu.
K_rotation = bajty 0x00..0x1f, session_id=42, g=0,
T(0)=bajty 0x20..0x3f. Dla kodowania z §7.1:

~~~text
cookie(T0) = ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8
encode(session-rotation, 42, 1, T0), hex =
57543842310001001073657373696f6e2d726f746174696f6e000300000008000000000000002a00000008000000000000000100000020202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f
T1, hex = 7df6e1af97d9e419b5d618bd08594e4401d5d6def7e0f42d6d2fe14aead73e39
cookie(T1) = ffbhr5fZ5Bm11hi9CFlORAHV1t734PQtbS_hSurXPjk
SHA256(ASCII(cookie(T1))) =
4edb865e5e1c0d731dac22bf83997fc381a6112d18a7e91000f95cfe98927aa3
T2, hex = d9f46208d9affd31c83fcd6aa7ad4db63ad718be751759bb2a1aab7c0ac36c47
~~~

Trzy ścieżki muszą dać powyższe **T1**, nie T2: pierwszy zwycięski CAS
z g=0; przegrany CAS po odczycie persisted_generation=1 i T0 jako previous;
recovery po restarcie/reload z persisted_generation=1 i T0.
Test błędnego `persisted_generation+1` musi wykazać inny wynik i odmowę,
bez podniesienia generacji. Osobno testować context, length-prefix,
endianness, identyfikator sesji/klucza i granice liczb oraz stabilność CSRF.

## 8. Database schema i migracje addytywne

Poniższy schemat jest planowany; **ten PR nie tworzy migracji**.
Nowe rewizje muszą następować po rzeczywistym head, bez zmiany zastosowanych
`20261006_06/07`. Stosować UTCDateTime i istniejące hide_parameters;
bez SQL echo/parametrów credentials w exception logs.

| Obiekt | Planowane pola / ograniczenia |
|---|---|
| `user_sessions` — 8B1-A | Dodać `csrf_nonce` (32 bajty, nie bearer), `security_key_id` i `encoding_version=1`. Backfill losowego nonce per istniejący rekord; bez zmiany token_hash, ID, user_id, created/last_seen/expires/revoked. |
| `user_sessions` — 8B1-C | Dodać `generation >= 0` (default 0), `rotated_at` (backfill created_at), nullable `previous_token_hash`, `previous_valid_until`. Current pozostaje w obecnym `token_hash`. Previous para albo oba NULL, albo oba ustawione; hash ma ten sam format SHA-256, różny od current, z unikalnym indeksem dla nie-NULL. Rotated_at mieści się w lifetime; previous deadline najwyżej rotated_at+600 s i expires_at. |
| `login_ip_attempts` — 8B1-B | `attempt_id, normalized_ip_key, admitted_at`; indeks `(normalized_ip_key, admitted_at)`. Jedna przyjęta próba = jeden rekord, nigdy sam inkrementowany licznik bez czasów okna. |
| `login_account_state` | `account_key` PK, `blocked_until`; istnieje również dla nieistniejących nazw. Brak FK do users. |
| `login_failures` | `request_id` unikalny, `account_key, failed_at`; indeks `(account_key, failed_at)`. Dopisanie failure i kara w tej samej transakcji. |
| `login_reservations` | `request_id` PK, `attempt_id`, unikalny `account_key`, `owner_id, reserved_at, lease_expires_at`. Jedna aktywna rezerwacja konta, finalizacja warunkowa po ID/owner/deadline. Zakończone reservation usuwane po atomowym zapisaniu wyniku. |
| `auth_security_buckets` | Minutowe agregaty `bucket_start, normalized_ip_key, account_key (jeśli znany), outcome, count`, z jednoznacznym PK również dla braku konta. Wyniki obejmują success, bad_credentials, blocked, abandoned i błędy HTTP/infrastruktury. Co najmniej ostatnie 24 h do monitoringu; bez body, cookie, CSRF i kluczy. |

Finalizacja jest idempotentna po request ID: brak aktywnej rezerwacji lub
powtórny wynik nie tworzy drugiej sesji/failure. IP attempt pozostaje bez
względu na wynik. Agregacja telemetryki i rotacja logów muszą ograniczać
rozrost przy floodzie; failures/attempts w aktywnym oknie są autorytatywne,
nie wolno ich zastąpić samplingiem. Wyniki blokad można agregować bez
przechowywania każdego requestu.

W SQLite użyć krótkich transakcji writer, unique constraints i conditional
UPDATE/rowcount, nie process-local mutex jako jedynej ochrony. Rozwiązanie
musi działać z wieloma procesami. Nie zakładać SELECT FOR UPDATE w SQLite.
Migracje zachowują wszystkie dane domenowe i użytkowników; ewentualny
SQLite table rebuild wymaga sprawdzenia FK/indexów/CHECK i kopii danych.
Nie zapisywać current/previous tokenów nawet tymczasowo w tabeli migracyjnej.
Istniejące sesje zachowują deadlines, a pierwsza należna rotacja nastąpi
dopiero przy autoryzowanym heartbeat po wdrożeniu 8B1-C.

## 9. Cryptographic key management, backup i utrata klucza

8B1-A ma wprowadzić wymagane `BROWSER_SECURITY_KEYS_FILE` do manifestu
jako niesekretną ścieżkę do pliku poza checkoutem, przykładowo
`/etc/work-tracker/browser-security-keys.json`.
Plik zawiera wersję formatu, aktywny key ID, zestawy K_csrf/K_rotation
oraz oddzielny stabilny K_throttle; klucze kodowane kanonicznym base64url,
po dekodowaniu dokładnie 32 bajty. Generowanie CSPRNG wykonuje operator
lub jawny provisioning wdrożenia, bez wyświetlania kluczy.
Nie generować zamiennika automatycznie przy starcie/restartach.
To przyszły kontrakt konfiguracji, nie polecenie edycji produkcji w tym PR.

Właściciel root, odczyt tylko dla roota i grupy usługi, typowo 0640,
katalog /etc/work-tracker 0750, bez zapisu dla usługi. Brak symlinkowego
wyjścia do niezaufanego pliku; walidować typ/właściciela/uprawnienia.
Klucze nie należą do SQLite, repo, builda frontendu, CLI arguments ani logów.
WEBHOOK_TOKEN pozostaje niezależnym sekretem; nie wyprowadzać z niego HMAC.
Logi HTTP muszą redagować Cookie, Set-Cookie, X-CSRF-Token, body credentials
i zawartość pliku kluczy, także na poziomie ewentualnego przyszłego proxy.

Sesja jest przypisana do key ID; wszystkie procesy znają ten sam keyring.
Kontrolowana zmiana aktywnego zestawu dotyczy nowych sesji. Stare zestawy
pozostają do końca ważności związanych z nimi sesji lub ich jawnego
revocation; nie zmieniać po cichu CSRF/rotation key w istniejącej sesji.
K_throttle pozostaje stabilny niezależnie od rotacji zestawów sesji,
aby zmiana kluczy nie resetowała limitów nazw kont.

Backup musi obejmować spójną kopię SQLite oraz oddzielną, chronioną kopię
konfiguracji i pasującego keyringu. Kopia kluczy jest sekretem, wymaga
kontroli dostępu i szyfrowanego przechowania; nie dopisywać jej do publicznego
artefaktu DB. Obecny updater wykonuje backup SQLite, **nie zapewnia jeszcze
backupu nowego pliku kluczy**. PR wdrożeniowy musi to rozwiązać i sprawdzić
restore bez ujawniania sekretów. Backup SQLite nie zawiera używalnego cookie.

Brak/niepoprawny klucz nie może uruchomić niechronionego browser API ani
ephemeral key. Operacje zależne od niedostępnego klucza zwracają fail-closed 503:
brak K_throttle blokuje login, a brak zestawu sesji blokuje obsługę dotkniętych
sesji, w tym ich /me, odczyty i mutacje; nie omija się wymaganych kontroli.
Minimalny health i oba token-authenticated webhooki HA zachowują swoją
niezależną ścieżkę. Readiness operatora musi osobno sprawdzać działanie loginu.
Nie zwracać 200 /me z pominiętym CSRF, gdy wymagany klucz jest niedostępny.

Po utracie K_rotation/K_csrf operator przywraca zweryfikowany keyring lub
unieważnia wszystkie dotknięte sesje, provisionuje nowy zestaw i wymaga loginu.
Nie da się odzyskać tokenu z samego hasha ani odtworzyć utraconego HMAC key.
Po podejrzeniu kradzieży klucza: revoke dotkniętych sesji, zmienić klucze
i wykonać review incydentu. Przy utracie K_throttle nie wolno od razu otworzyć
nowych pustych limitów: wstrzymać nowe loginy do upływu co najmniej 15 minut
od ostatniej możliwej próby/starej reservation i zakończenia wszystkich kar;
istniejące sesje oraz HA nadal działają, jeśli ich klucze są dostępne.
Przełączenie K_throttle i okno maintenance muszą być jawne w audycie.

Restore starej bazy może wskrzesić historyczny stan sesji, zgubić revocations
i liczniki. Przed wznowieniem browser dostępu po restore unieważnić wszystkie
przywrócone sesje i zachować aktualne limity albo odczekać opisane pełne
okno maintenance logowania. Nie traktować backupu jako aktualnego źródła
revocation. Nie nadpisywać nowszych raw events/korekt zwykłym rollbackiem auth.

## 10. Deployment/runbook i rollback

### 10.1. Przyszły rollout implementacji

1. Wdrożenia nadal tylko w LAN. Potwierdzić konto operatora, dostęp do hosta,
   `SESSION_COOKIE_SECURE=false` wyłącznie dla LAN HTTP i sprawne HA.
   Zarezerwować DHCP albo ustawić stabilny lokalny DNS.
2. W PR 8B1-A rozszerzyć manifest o wymagane origins i ścieżkę kluczy;
   provisionować i zweryfikować klucze bez drukowania. Uaktualnić README
   i PROJECT_STATUS w ramach implementacji. Przygotować wspólny backend
   i frontend cutover: stary klient bez CSRF ma otrzymać 403, nie bypass.
3. Zachować clean-worktree, SQLite backup przed update, fast-forward-only,
   instalację/build, migracje i bezpieczny rollback obecnego updatera.
   Skontrolować backup konfiguracji/keyringu poza repo.
4. Wdrożyć i osobno zaakceptować A → B → C. Dla każdej addytywnej migracji
   sprawdzić upgrade z obecnego production head i świeżą bazę, jedno head,
   zachowanie danych oraz restart. Nie zmieniać wstecz istniejących rewizji.
5. Wyłączyć niezaufane proxy-header processing serwera przed oparciem limitów
   o ASGI client IP. Sprawdzić spoofed X-Forwarded-For także od localhost.
   Zaufane proxy/cloud routing nadal nie są aktywowane.
6. Smoke: shell → login → /me bez zapisów → świadomy heartbeat →
   ustawienia/korekta/eksport → logout → login; hidden tab nie odnawia idle.
   Dla C zasymulować należną rotację/utraconą odpowiedź w środowisku testowym,
   nie zmieniać produkcyjnych danych ani czasu hosta.
7. HA bez browser cookie/Origin/CSRF: ingest raw event oraz tokenowa
   timestamp correction. Brak/błędny X-Webhook-Token nadal odrzucany.
   Potwierdzić immutable raw timestamp i zachowanie settings aliases/timezones.
8. Monitorować 401/403/413/429/503, błędne loginy/porzucone reservation,
   konflikty CAS/recovery, SQLite busy i stan backupu. Analizować ostatnie
   24 h: wiele IP na konto, wiele nazw z prefixu, tempo poniżej progów,
   wielokrotne wyczerpywanie konta oraz wolumen błędów HTTP.
   Nie dodawać twardej blokady dobowej jako ukrytej odpowiedzi na alarm.

### 10.2. Zmiana LAN IP / hosta / portu

1. Zidentyfikować rzeczywisty origin widoczny w pasku przeglądarki i docelowy
   schemat/host/port; naprawić rezerwację DHCP/DNS przed rozszerzeniem allowlist.
2. Z konsoli operatora edytować wyłącznie konfigurację poza checkoutem.
   Ustawić nowy dokładny BROWSER_ALLOWED_ORIGINS; jeśli cross-origin frontend
   istnieje, uaktualnić również CORS_ORIGINS i jawny frontend API base.
   Przy migracji można jawnie podać oba zaufane origins tylko na czas przejścia.
3. Zwalidować konfigurację i zrestartować work-tracker.service.
   Nie rozwiązywać 403 przez wildcard, `null`, wyłączenie CSRF lub zaufanie
   do dowolnego Host/forwarded header.
4. Otworzyć nowy adres, sprawdzić login, chroniony heartbeat i mutację.
   Cookie jest host-only: zmiana hostname/IP zwykle wymaga nowego loginu;
   nie kopiować tokenu między hostami. Usunąć stary origin po przejściu.
5. Sprawdzić i w razie potrzeby zmienić ręcznie URL obu integracji HA,
   zachowując ich token i brak zależności od browser origins.
   Odnotować przyczynę 403: nieaktualna allowlist może blokować login/mutacje,
   mimo że statyczna strona i minimalny health nadal się otwierają.

### 10.3. Rollback

Ten dokumentacyjny PR nie wymaga deploy ani rollbacku DB.
Przy przyszłym rollbacku kodu zatrzymać zgodnie z updaterem ruch writerów,
zachować spójny backup i dobrać zgodny frontend/backend/config/keyring.
Addytywne tabele/pola pozostawić; nie wykonywać destructive downgrade
wyłącznie dla usunięcia metadanych auth.

Wycofanie C do A/B może użyć obecnego current token_hash, ale pozbawia
previous-token recovery. Może wymagać relogin; stare retry z klienta nie mogą
powtarzać domain mutations. Wycofanie A/B do 8A3 usuwa CSRF/throttling
i przywraca mutujący GET /me: to świadomy regres bezpieczeństwa, dopuszczalny
wyłącznie w odizolowanym LAN jako incydentalny rollback operatora.
Wycofanie aż do 8A2 otwiera ordinary API i nie jest bezpiecznym domyślnym
rollbackiem ochrony browser access. Żaden rollback nie uruchamia publicznego
routingu, nie usuwa nowych danych pracy ani nie resetuje limitów po cichu.
Po przywracaniu DB stosować zasady revocation/maintenance z §9.

## 11. Failure modes, concurrency i residual risks

| Sytuacja | Zachowanie fail-safe / pozostałe ograniczenie |
|---|---|
| Cross-site GET /me bez Origin i bez Sec-Fetch-* | Brak zapisów i Set-Cookie w każdym wyniku; bez podtrzymania idle. |
| Stary frontend, zły origin, brak CSRF | 403 przed efektem, reload nowego klienta/naprawa jawnej konfiguracji. HA pozostaje niezależny. |
| Timeout/busy SQLite | 503 bez obejścia limitera/guardu, bez pozytywnej finalizacji lub nowego cookie. |
| Równoległe loginy | Atomowy IP admission i jeden account lease przed kosztowną weryfikacją; różne konta nie dzielą account failure budget. |
| Crash przed/po Argon2id lub przed/po commit | Reservation i IP są trwałe; nieznany wynik nie tworzy sesji; po commit utracone cookie loginu może wymagać nowego loginu, bez zgadywania tokenu. |
| Rotacja kontra mutacja/logout/revocation | Warunki w tej samej transakcji writer, jeden porządek commitów; brak resurrection i podwójnego wykonania wskutek recovery. |
| Utrata odpowiedzi/spóźnione Set-Cookie | Deterministyczny immediate-previous recovery w oknie; relogin po oknie lub starszym cookie. Przeglądarka nie daje globalnego CAS cookie. |
| Skok zegara hosta | Terminy zależą od poprawnego UTC, nie zegara klienta. Cofnięcie nie cofa zapisów activity/rotated_at ani nie zeruje limitów; future state odrzucany. Przy wykryciu istotnej niespójności wstrzymać browser login/renewal do naprawy czasu. NTP i alarmy są wymaganiem operacyjnym, restart nie „naprawia” deadline. |
| Utrata/kradzież klucza lub restore starej DB | Fail-closed i procedura §9; relogin/revocation zamiast osłabienia walidacji. |
| Flood różnych nazw/prefiksów IP | Limity per konto/IP nie eliminują rozproszonego CPU/DB/network DoS; potrzebny monitoring i bounded worker/storage operations. |

**Denial-of-service przez celowe wyczerpywanie limitu logowania jest
zaakceptowanym ryzykiem resztkowym.** Atakujący znający login może okresowo
utrudniać nowe logowania; kolejne serie mogą powtarzać ten efekt. Brak
permanentnego lockoutu i maksymalna kara progresywna 300 s ograniczają
skutki, lecz próg 8 błędów w 15 minut może wymusić dłuższe czekanie.
Prawidłowe istniejące sesje nie są odwoływane ani ograniczane przez te liczniki.
Nie przedstawiać tego kompromisu jako całkowitej eliminacji DoS.

Rotacja nie usuwa skutków kradzieży current cookie: posiadacz może wykonywać
dozwolone operacje i pozyskać CSRF tak jak przeglądarka. Kradzież previous
może umożliwić recovery przez 10 minut. Rate limits i HMAC nie zastępują TLS,
bezpieczeństwa endpointu ani operatorowego revocation. LAN HTTP nadal pozwala
aktywnemu napastnikowi sieciowemu wykraść hasło/cookie lub zmienić aplikację.
Nie poszerzać z tego powodu zakresu tego PR o publiczne HTTPS/proxy.

Metadata i timing mogą ujawniać istnienie aktywności na określonym loginie,
choć protokół nie potwierdza istnienia konta. Dummy verification i identyczne
stany ograniczają user enumeration, bez obietnicy całkowitego usunięcia
wszystkich kanałów czasowych. IPv6 /64 i IPv4 NAT mogą blokować współdzielone
urządzenia; zmiana sieci i atak rozproszony pozostają możliwe.

## 12. Security test matrix

To wymagania przyszłych testów, **nie raport testów wykonanych przez ten PR**.
Zachować testy obecnych regresji, zmieniając tylko kontrakty świadomie
zastąpione w A: mutujący GET heartbeat oraz anonimowy logout 204.
Testy czasu używają kontrolowanego zegara, race — barier i osobnych sesji/
połączeń SQLite, bez kruchych sleepów. Testy API i przeglądarkowe są odrębne:
jsdom nie symuluje prawdziwego stosu cookie/CORS/Fetch Metadata.

| ID | Etap / uwaga | Scenariusz i wymagany dowód |
|---|---|---|
| A01 | A / review A | Cross-site top-level GET /me z ważnym current, previous, expired i revoked cookie; z metadata, bez metadata i bez Origin. Snapshot wszystkich pól sesji/tabel auth przed/po i monitoring SQL: zero zapisów, zero Set-Cookie; nawet gdy activity/rotacja są należne. |
| A02 | A / review A | Same-origin GET /me wielokrotnie: zero touch, rotacji, cleanup i cookie. CSRF bootstrap poprawny; invalid/expired/revoked daje 401 bez usuwania cookie. HEAD/nieznane metody nie mutują. |
| A03 | A / review A | POST heartbeat bez sesji, z błędnym Origin/CSRF, expired/revoked: brak zmian. Poprawny zapis przy dokładnie 1 h, brak przed granicą i przy równoległych touches; 30 dni idle i 180 absolute bez wydłużenia. |
| A04 | A / review A | Widoczna karta z realną aktywnością wysyła heartbeat; ukryta, sam timer/polling, syntetyczne eventy i brak aktywności go nie wysyłają. Bootstrap GET działa niezależnie; utrata sesji zatrzymuje polling. |
| A05 | A / review A,C | Origin missing/null/wildcard/lookalike/subdomain/zły port/schemat/wiele wartości; Sec-Fetch cross-site/navigate/niepoprawne. Bez Sec-Fetch przy poprawnym Origin+CSRF LAN działa. Same-site sibling nie jest zaufany. |
| A06 | A | Login CSRF przez formularz, simple request, brak custom header, zły media type, obcy Origin i login w istniejącej sesji bez CSRF: brak sesji/cookie. Logout CSRF nie odwołuje sesji. |
| A07 | A / review A | Nowy nieopisany endpoint/mount/metoda w /api, suffix/trailing slash i podobna ścieżka auth: default-deny przed body. Heartbeat/recovery nigdy anonimowe, previous tylko w dwóch dokładnych politykach. |
| A08 | A / review B | Body 16383/16384/16385 bajtów; chunked/no Content-Length, fałszywy Content-Length i Content-Encoding. >16384 → 413 przed parsingiem/Argon2id, bez nieograniczonego bufora. |
| A09 | A / review B | Hasła 14/15/1024/1025 znaków; Unicode/polskie znaki/emoji/spacje, NUL/unpaired surrogate i duplicate JSON keys. 1024 znaków jako rzeczywiste escape \uXXXX oraz 1024 pary \uD83D\uDE00 przechodzą limit body i poprawną weryfikację; nie liczyć jednostek UTF-16 jako znaków. |
| A10 | A / review C | Walidacja konfiguracji origins, same-origin production bez CORS, Vite exact origin, preflight + X-CSRF-Token/custom header, error responses z CORS i Retry-After. Spoofed Host/Forwarded nie dodają originu; zmiana IP daje 403 do czasu jawnej aktualizacji. |
| A11 | A | Nagłówki/cache dla HTML, auth, 401/403/409/413/429/503, docs i CSV/PDF. Prywatne odpowiedzi no-store, hash assets cache'owalne; brak danych po logout/back/restore UI. |
| A12 | A | Rzeczywisty production build pod CSP: login, style/fonty, dashboard, forms, pobranie blob CSV/PDF, docs policy. Brak unsafe-inline/eval/CDN bypass i brak HSTS/forced HTTPS w LAN. |
| B01 | B / review D | Kontrolowane błędy 1–8 i dokładna tabela 0/0/0/30/60/120/240/300. Próba podczas przerwy i przy F=8 po końcu 300 s nie wywołuje Argon2id. |
| B02 | B / review D | Granice (t−900 s, t], wyjście najstarszego failure oraz jednoczesna kara; Retry-After to ceil maksimum deadlines, poprawne także przy częściowej sekundzie. |
| B03 | B / review D | 30 prób IP obejmuje sukcesy, błędy, nieznane konto, błędny JSON i account-blocked; 31. nie weryfikuje hasła. IP-blocked nie przesuwa okna, account-blocked nie zwiększa F/kary. |
| B04 | B / review D | Wiele procesów/połączeń: jeden account lease przed Argon2id, brak przekroczenia limitów i ominięcia progresji; wolna weryfikacja nie trzyma SQLite locka. |
| B05 | B / review D | Restart po admission/reservation i podczas finalizacji, lease expiry i spóźniony worker: brak resetu limitów, podwójnego failure/sesji i cookie po niepewnym commit. SQLite busy → 503 bez bypass. |
| B06 | B / review D | Udany login nie czyści IP/failures/kar; normalizacja Owner/owner/spacje ma wspólny budżet. Istniejąca, nieistniejąca i invalid nazwa mają ogólne odpowiedzi i odpowiednie dummy/reservation; test timing bez wczesnego account-existence exit. Zachować regresję rehash CAS przy równoległej zmianie hasha na to samo/inne hasło, bez nadpisania zmiany i bez wyjścia poza lease. |
| B07 | B / review D,E | Celowe wyczerpywanie konta z wielu IP utrudnia nowy poprawny login, lecz istniejące sesje nadal odczytują/zmieniają dane i heartbeat działa. Po ustaniu ataku powrót po obu deadline, bez permanentnego lockoutu. Brak twardego limitu dobowego, monitoring ostatnich 24 h. |
| B08 | B / review G | Różne zapisy IPv6 i zmiana adresu wewnątrz /64 współdzielą budżet; inny /64 ma osobny. IPv4 /32, IPv4-mapped IPv6=IPv4; spoofed X-Forwarded-For/Forwarded/CF-Connecting-IP, także od localhost, nie zmieniają klucza. |
| C01 | C / review F | Wektory §7.5: zwycięski CAS, przegrany CAS i recovery/restart dają identyczne T1/hash. Persisted_generation+1, inny context/encoding/ID/key dają odmowę. CSRF identyczny przed/po. |
| C02 | C / review F | 7 dni −1 µs / dokładnie 7 dni / +1 µs; GET i ordinary API nie rotują, activity heartbeat rotuje raz. Brak aktywności przez >7 dni nie unieważnia current przed idle/absolute expiry. |
| C03 | C / review A,F | Utracona odpowiedź, reload z previous bez CSRF w pamięci: GET 409 bootstrap → chroniony recover → GET 200. Brak CSRF/Origin/sesji/revoked zawsze odrzucany; zero cyklicznej zależności, zero anonymous endpoint. |
| C04 | C / review F | Previous tuż przed 600 s działa tylko jako recovery; dokładnie 600 s i później 401. Kolejne recovery nie przedłuża okna i nie zmienia generation/last_seen/rotated_at; starsze tokeny nie działają. |
| C05 | C / review F | Wiele kart bez Web Locks/storage, dwa heartbeaty, przegrany CAS, recovery z już-current cookie: jeden current token, bez pętli i drugiej rotacji; Current recover nie odnawia idle. |
| C06 | C / review F | Bariera rotacja↔manual-event/correction/logout/revocation: mutacja commit raz albo 409 przed efektem. Retry wyłącznie explicit rotation_required; domain 409/timeout/5xx/utrata odpowiedzi po commit nie powodują automatycznej duplikacji. |
| C07 | C / review F | Zmiana kolejności Set-Cookie w tej samej i kolejnej generacji, opóźnienie >10 min, logout/new login. Brak resurrection; spodziewany relogin tam, gdzie przeglądarka nie zapewnia recovery. Stare JSON/blob/401 nie psują nowego auth lifetime. |
| C08 | C | Cookie attributes po loginie/rotacji/recovery: HttpOnly/host-only/Lax/Path, jawny Secure, Expires oryginalny i pozostały Max-Age. DB/logs/exception/request traces bez używalnych tokenów, haseł i kluczy. |
| X01 | A/B/C | Oba endpointy HA z poprawnym/złym/brakującym tokenem, bez browser cookie, CSRF i Origin, także przy browser throttle/expiry/revocation/błędzie klucza. Ingest i tokenowa korekta działają, raw timestamp niezmieniony. |
| X02 | A/B/C | Addytywne upgrade fresh/existing DB, constraints/index/FK/head, stare sesje/backfill, restart/key ID, klucze utracone/niepoprawne, backup+restore i rollback. Brak zmiany danych domenowych; restore nie wskrzesza sesji ani nie zeruje cicho limitów. |
| X03 | A/B/C | Cofnięcie UTC względem last_seen/rotated_at lub attempt/failure/reserved_at, także po restarcie: fail-closed bez touch/rotacji, wyzerowania budżetu i przedłużenia okna. Skok w przód zachowuje granice expiry, nie odtwarza wygasłych sesji. |

## 13. Acceptance criteria

- [ ] **8B1-A:** odczytowy /me i chroniony POST heartbeat, stabilny HMAC CSRF
  i kompletny bootstrap, login/logout CSRF, jawne origins z procedurą DHCP,
  brak zależności od Fetch Metadata w HTTP, 16 KiB body i pełne Unicode,
  default-deny, restrictive CORS, CSP/security headers/cache. A01–A12, X01–X03.
- [ ] **8B1-B:** dokładne progi 8/30 i tabela kar, trwała atomowa rezerwacja
  przed Argon2id, restart/concurrency/Retry-After, brak enumeration bypass,
  IPv4 /32 i IPv6 /64, monitoring 24 h bez limitu dobowego; jawny DoS compromise.
  B01–B08, X01–X03.
- [ ] **8B1-C:** należna 7-dniowa rotacja i 10-minutowe recovery tylko przez
  chroniony heartbeat, poprawna persisted generation, hash-only DB, CAS,
  wielokartowość/lost responses/reload/late cookies bez automatycznej duplikacji
  mutacji. Wektory i granice są deterministyczne. C01–C08, X01–X03.
- [ ] Nadal 30 dni idle, 180 dni absolute, server-side revocation i oddzielne
  tokenowe HA; żadnej zmiany raw events, korekt lub obliczeń domenowych.
- [ ] Klucze i konfiguracja poza repo, backup/restore/key-loss/rollback
  przetestowane, pełna walidacja backend/frontend i odpowiednie deploy tests
  zgodnie z README w przyszłych PR implementacyjnych.
- [ ] Przed gotowością do Internetu osobne review 8B2; 8B1 nie aktywuje Phase 9
  ani nie zmienia statusu Phase 10.

### 13.1. Decyzje i kwestie do jawnego przeglądu w PR

| Uwaga review | Kontrakt / opis | Pokrycie macierzy |
|---|---|---|
| A — read-only /me, heartbeat i bootstrap | §4, §5.1–5.3, §7.2–7.4 | A01–A07, C03–C05 |
| B — limit bajtów a znaki hasła | §4.1 | A08–A09 |
| C — jawne origins i DHCP | §5.1, §10.2 | A05, A10 |
| D — deterministyczne throttling/reservation | §6 | B01–B07 |
| E — denial-of-service konta | §2, §6.2, §11 | B07 |
| F — poprawne wyprowadzenie i recovery | §5.2, §7 | C01–C08 |
| G — IPv6 /64 i normalizacja | §6.1 | B08 |

Wymagania A–G z security review są rozstrzygnięte w tej specyfikacji.
Istotne doprecyzowania: previous-token GET zwraca ograniczony CSRF bootstrap,
recover nie podtrzymuje idle; logout wymaga sesji zamiast anonimowego 204;
jeden account lease i brak resetu failures przy sukcesie; limit ruchomy może
blokować dłużej niż maksymalna 300-sekundowa kara; nie obiecuje się odporności
przeglądarki na dowolnie opóźnione Set-Cookie.

Obecne CDN/inline bootstrap Swagger/ReDoc kolidują z docelowym CSP;
§5.4 wymaga lokalnych zasobów i kontrolowanego nonce bez szerokiego wyjątku.
Kompatybilność konkretnej wersji bibliotek docs wymaga dowodu w PR 8B1-A;
brak kompatybilności jest blokadą do zgłoszenia, nie zgodą na słabszy CSP.
Konkretne narzędzie chronionej kopii kluczy i operacyjne progi alarmów
muszą być nazwane oraz zweryfikowane w PR wdrożeniowym. Żadne z tych ustaleń
nie upoważnia do złagodzenia CSRF, previous-token policy lub wymogów kluczy.
Jeśli implementacja wykaże konieczność zmiany któregoś inwariantu, wymagane
jest jawne zgłoszenie decyzji architektonicznej, bez ukrytego bypass.

## 14. Źródła i standardy

Zweryfikowane online 2026-10-25. Poniższe źródła uzasadniają mechanizmy;
konkretne progi czasu/liczb i protokół recovery są decyzjami Work Trackera,
nie domniemanym zaleceniem liczbowym OWASP.

- [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html):
  session-bound HMAC, Origin, login CSRF i Fetch Metadata jako dodatkowa warstwa.
- [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html):
  throttling, ogólne błędy, user enumeration i ryzyko blokowania kont przez napastnika.
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html):
  cookie security, expiry, revocation i ryzyko wyścigów przy odnowieniu identyfikatora.
- [OWASP CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
  i [HTTP Headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html):
  restrykcje źródeł, ochrona ramek i cache danych wrażliwych.
- [OWASP Key Management](https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html):
  cykl życia, ochrona i recovery kluczy.
- [RFC 9110 §9.2.1, §10.2.3, §15.5.14](https://www.rfc-editor.org/rfc/rfc9110.html):
  bezpieczne metody, Retry-After i HTTP 413.
- [RFC 6585 §4](https://www.rfc-editor.org/rfc/rfc6585.html#section-4):
  HTTP 429 Too Many Requests.
- [RFC 9111 §5.2.2.5](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.5):
  semantyka no-store.
- [RFC 6454](https://www.rfc-editor.org/rfc/rfc6454.html)
  i [WHATWG Fetch — CORS](https://fetch.spec.whatwg.org/#http-cors-protocol):
  origin oraz credentialed cross-origin requests.
- [Fetch Metadata Request Headers](https://www.w3.org/TR/fetch-metadata/):
  dodatkowy kontekst przeglądarki; dokument W3C ma status Working Draft.
- [RFC 6265](https://www.rfc-editor.org/rfc/rfc6265.html):
  cookies, Set-Cookie i ograniczenia konkurencyjnych odpowiedzi.
- [RFC 2104](https://www.rfc-editor.org/rfc/rfc2104.html):
  HMAC; [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html):
  JSON, Unicode i escaped surrogate pairs.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html):
  BEGIN IMMEDIATE, serializacja writerów i możliwe SQLITE_BUSY.

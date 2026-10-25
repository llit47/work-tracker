# Phase 8 — praktyczny projekt bezpieczeństwa 8B1 / 8B2

**Status: PLANNED.** To specyfikacja przyszłych zmian, wyłącznie dokumentacja.
Phase 8 pozostaje IN PROGRESS; obecna produkcja jest LAN-only.
Kolejność: **8A3 → 8B1-A → 8B1-B → 8B2 → Phase 9**.

## 1. Stan obecny i decyzja o uproszczeniu

Kod i testy 8A1–8A3 już zapewniają Argon2id, server-side sessions, revocation,
centralny pure-ASGI default-deny dla całego HTTP /api oraz oddzielny token HA.
`user_sessions` zawiera tylko ID, user_id, SHA-256 tokenu i daty sesji.
Head Alembic to `20261006_07`. Nie ma limitera, heartbeat POST, kontroli
Origin/custom header, keyringu ani okresowej rotacji.
Obecny GET /me **może zapisać last_seen_at**; frontend używa go co 5 minut
w widocznej karcie i przy focus. Read-only /me jest dopiero celem 8B1-A.
CORS jest już restrykcyjny; nie jest samodzielną ochroną przed CSRF.

Decyzja: bezpieczeństwo proporcjonalne do małego wdrożenia self-hosted.
8B1-A nie wymaga nowej tabeli ani sekretu; 8B1-B dodaje jedną tabelę historii
prób. Nie projektujemy tokenów CSRF, kluczy HMAC, lease, telemetrycznych tabel
ani protokołu rotacji/recovery. Działające zabezpieczenia pozostają.
Późniejszy publiczny dostęp wymaga dodatkowo weryfikacji 8B2.

README nadal opisuje działający 8A3; jego zapowiedzi obowiązkowej rotacji
w 8B1 są nieaktualne. Zgodnie z zakresem tego PR nie edytujemy README;
aktualny plan wyznaczają ten dokument, ROADMAP i PROJECT_STATUS.
AGENTS wymaga możliwości przyszłej rotacji, nie obowiązku jej wdrożenia teraz.

## 2. Krótki threat model i zachowane inwarianty

Chronimy prywatne dane pracy/płac, credentials, sesje i dostępność małego LXC.
Zagrożenia: obca strona wysyłająca żądania, zgadywanie haseł, kradzież cookie,
spoofing proxy headers, przypadkowe publiczne wystawienie originu lub webhooków.
XSS, przejęty host/przeglądarka oraz aktywny napastnik w LAN HTTP pozostają
poza gwarancjami CSRF; CSP i przyszłe HTTPS ograniczają część ryzyka.

- Raw events są immutable; auth nie zmienia korekt, parowania ani pay.
- HA ingestion i timestamp correction nadal wymagają wyłącznie
  `X-Webhook-Token`, bez browser sesji, Origin i custom header.
- Cookie: 256-bitowy losowy token, wyłącznie SHA-256 w SQLite, HttpOnly,
  SameSite=Lax, host-only, Path=/; Secure=true przy HTTPS.
- Sesja: 30 dni idle od zapisanego last_seen_at, 180 dni absolute od loginu,
  bez przedłużania absolute; na granicy deadline jest nieważna.
- Nowy login daje nowy token; logout/revocation działają server-side.
  Brak tokenów auth w localStorage, URL-ach, body odpowiedzi lub logach.

## 3. 8B1-A — Lightweight Browser Security (PLANNED)

### Origin, niestandardowy nagłówek i format żądań

Centralna polityka obejmuje wszystkie browser mutations, w tym login,
logout, heartbeat, POST/PUT/PATCH/DELETE i przyszłe unsafe methods.
GET/HEAD/OPTIONS nie zmieniają stanu. Nie dodajemy rozproszonych dependencies
ani wyjątku dla całego /api/auth. Wymagane na każdej mutacji:

1. Dokładnie jeden dozwolony `Origin`: pełny scheme/host/effective port.
   Brak, null, wiele wartości, wildcard i lookalike dają 403.
   Bez fallbacku do Referer, Host, User-Agent lub forwarded headers.
2. Dokładnie `X-Work-Tracker-Request: 1`; brak/błędna wartość daje 403.
   To stały znacznik żądania JS, nie sekret ani token uwierzytelniający.
3. Dla body: `application/json` (opcjonalnie charset UTF-8), ścisły JSON
   i walidacja schematu; simple content types text/plain, form-urlencoded
   i multipart są odrzucane (415). Operacje bez body nadal wymagają 1–2.
   Błędy 422/401 nie mogą odbijać hasła ani pełnego inputu.

Nowe wymagane `BROWSER_ALLOWED_ORIGINS` to jawna, niepusta allowlista
kontrolowanych origins, bez ścieżek, query i credentials.
Kanonizacja schematu/hosta/portu jest jednoznaczna; brak zgadywania z requestu.
Przykłady: LAN `http://192.168.1.50:8000` albo
`http://work-tracker.home.arpa:8000`; później `https://work.example.com`.
Rezerwacja DHCP lub stabilny DNS ograniczają błędy konfiguracji.
Zmiana adresu/portu wymaga aktualizacji allowlist i restartu; do tego czasu
403 przy logowaniu/mutacjach jest oczekiwane. Zmiana hosta wymaga nowego loginu.

`CORS_ORIGINS` pozostaje jawnym podzbiorem tej allowlist dla cross-origin JS,
np. `http://localhost:5173`; produkcyjnie same-origin ma pusty CORS.
Credentialed CORS dopuszcza wyłącznie znane origins, używane metody i nagłówki
Content-Type/X-Work-Tracker-Request; zachować dotychczasowy X-Webhook-Token.
Preflight nie wymaga sesji, ale nie wykonuje endpointu; Vary: Origin,
Expose-Headers: Retry-After. Bez origin reflection, wildcard i regex subdomen.
Opcjonalne odrzucanie Sec-Fetch-Site: cross-site jest defense-in-depth;
brak Sec-Fetch-* w LAN HTTP nie omija obowiązkowych kontroli 1–2.

[OWASP CSRF — Custom Headers and CORS](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#employing-custom-request-headers-for-ajaxapi)
opisuje ten model bez tokenu dla AJAX/API. Obcy formularz/simple request
nie ustawi custom header; cross-origin JS wymaga udanego preflight.
Serwer sprawdza nagłówek i Origin również dla bezpośrednio otrzymanego requestu:
samo nieudzielenie CORS nie zatrzymuje skutków simple request.
Model zakłada poprawny stos przeglądarki, bezpieczne dozwolone origins i HTTPS
przy publicznym dostępie. Nie chroni przed XSS, przejętym allowed origin
ani nieprzeglądarkowym klientem podrabiającym nagłówki; ten nadal wymaga auth.
Frontend dodaje znacznik centralnie; nie ma bootstrapu ani storage tokenów CSRF.

### Kontrakty endpointów i aktywność

| Endpoint | Docelowy kontrakt |
|---|---|
| GET /api/auth/me | 200 username/expires_at lub 401; wyłącznie odczyt, bez last_seen_at, cleanup, rotacji i Set-Cookie, również usuwania cookie. |
| POST /api/auth/heartbeat | Ważna sesja + Origin + znacznik; puste body lub JSON {}; 200 bez Set-Cookie. Conditional UPDATE last_seen_at najwyżej raz na godzinę, bez revival expired/revoked i bez zmiany expires_at. Nigdy anonimowy wyjątek. |
| POST /api/auth/login | Origin + znacznik + JSON + limiter; nowa sesja/cookie albo ogólne 401, 429 + Retry-After lub 503 przy awarii/przeciążeniu. |
| POST /api/auth/logout | Origin + znacznik nawet bez sesji; zachować idempotentne 204, revocation wskazanej sesji i usunięcie cookie. |
| Ordinary /api, docs/schema | Zachować centralny session guard; dodatkowo 1–3 przy unsafe requests. Nieprawidłowa sesja 401, błąd polityki 403 przed efektem. |
| GET /api/health, dwa istniejące POST webhooki HA | Dokładne obecne wyjątki; minimalny health i niezależne uwierzytelnianie HA. Bez nowych prefix exceptions. |

Cross-site top-level GET /me z cookie Lax nigdy niczego nie zapisuje.
Frontend bootstrap po reload pozostaje zwykłym odczytem /me.
Heartbeat tylko w zalogowanej, widocznej karcie po interakcji użytkownika
lub świadomym focus/powrocie do aplikacji; łączyć żądania najwyżej co 5 minut.
Sam timer/polling lub ukryta karta nie odnawia idle. Serwer nie może dowieść
widoczności; wymusza sesję, Origin, znacznik i istniejące warunki czasu.
Zachować globalne 401/logout, odmontowanie chronionych danych i odrzucanie
starych odpowiedzi/JSON/blob; bez automatycznego replay mutacji po timeout.

### Body, CSP, nagłówki i cache

Limit loginu: **16384 bajty HTTP body**, sprawdzane strumieniowo przed parsingiem
i Argon2id, także bez/z fałszywym Content-Length; więcej daje 413.
Odrzucać nieobsługiwany Content-Encoding przed dekompresją (415).
Po JSON unescape nadal 15–1024 punktów kodowych hasła, bez trim/normalizacji,
NUL i unpaired surrogate. 1024 emoji jako pary escape mieszczą się w limicie;
limit znaków nie jest limitem bajtów i nie oznacza jednostek UTF-16.

Podstawowy production CSP: default-src 'self'; script-src 'self';
style-src 'self'; object-src 'none'; base-uri 'none';
frame-ancestors 'none'; form-action 'self'. Bez unsafe-eval i szerokich CDN
wyjątków; testować rzeczywisty build. Obecne Swagger/ReDoc używają CDN/inline:
dla prostoty planujemy wyłączyć ich interaktywny HTML w production,
pozostawiając chronione OpenAPI JSON; development docs nadal wymagają sesji.
To jawna przyszła zmiana pomocniczego UI, nie usunięcie działającej ochrony.
Nosniff, Referrer-Policy: no-referrer, X-Frame-Options: DENY.
Auth/private API/CSV/PDF i ich błędy: Cache-Control: no-store;
login shell również no-store. Publiczne hash assets mogą być cache'owane.
HSTS dopiero w profilu HTTPS po 8B2, bez wymuszania HTTPS w LAN.

## 4. 8B1-B — Simple Login Rate Limiting (PLANNED)

### Jedna tabela i ograniczenie zasobów

Planowana tabela `login_attempts`: id INTEGER PRIMARY KEY AUTOINCREMENT
(bez ponownego użycia ID), login_key, ip_key, attempted_at UTC,
nullable failed_at UTC; indeksy (ip_key, attempted_at), (login_key, failed_at)
oraz attempted_at do cleanup. Brak FK do users; unknown login działa identycznie.
login_key = SHA-256 ASCII("login:" + kanoniczny username z normalizacji);
invalid username ma wspólny klucz SHA-256 ASCII("invalid-username"). Hash nie jest sekretem
ani gwarancją prywatności nazw, lecz daje stałą długość bez dodatkowego klucza.
ip_key: kanoniczne IPv4 /32, IPv6 /64, IPv4-mapped IPv6 → IPv4.
W LAN używać peer IP; wyłączyć niezaufane przepisanie go przez Uvicorn.
Brak poprawnego źródła IP daje 503; nagłówki proxy dopiero według §6.

Wdrożenie ma **jeden worker Uvicorn**, jawnie utrwalony w przyszłym unicie;
procesowy semafor ma **2 sloty**, wspólne dla real/dummy Argon2id i rehash.
Bez nieograniczonej kolejki; brak slotu daje 503, a nie wykonanie hashowania.
Slot zwalnia się dopiero po zakończeniu rzeczywistej pracy i zapisie wyniku,
także gdy HTTP timeout/abort nie zatrzymuje wątku. Wiele workerów/instancji
wymaga osobnego review; nie obiecywać globalnego limitu CPU z lokalnego semafora.

### Algorytm

Okno W(t) = (t−900 s, t]; błąd sprzed dokładnie 900 s już nie liczy się.
IP liczy attempted_at, login liczy failed_at. Wynik sukcesu nie resetuje okien.

1. Sprawdzić HTTP/body/JSON z §3, wyznaczyć klucze bez sprawdzania istnienia
   konta. Odrzucenia 403/413/415/422 nie są próbami credentials.
   Spróbować zająć slot bez czekania; dalsza krótka kontrola IP działa także,
   gdy brak slotu, aby przeciążone próby też zużywały budżet IP.
2. W jednej krótkiej transakcji BEGIN IMMEDIATE: cleanup starych rekordów,
   kontrola pułapu tabeli, policzenie prób IP i błędów loginu.
   Przy IP ≥30 zwrócić 429 bez nowego rekordu; inaczej dopisać próbę
   z failed_at=NULL. Przy loginie ≥8: commit i 429 bez weryfikacji.
   Brak slotu: commit i 503; inaczej commit i przejść do weryfikacji.
   Nigdy nie utrzymywać transakcji/SQLite write locka podczas Argon2id.
3. Zweryfikować realne lub dummy credentials poza transakcją.
   Błędna długość/NUL hasła daje ogólne 401 bez Argon2id, ale z zapisem błędu.
   Znany błąd: krótki UPDATE failed_at=now, commit i ogólne 401.
   Sukces: istniejący bezpieczny rehash i zapis nowej sesji; cookie dopiero
   po udanym commit. Zachować ochronę przed nadpisaniem równoległej zmiany hasha.
4. Błąd DB/niepewny commit daje 503 bez cookie; zwolnić zajęty slot.
   Nie przyznawać dostępu przy niemożności sprawdzenia limitu/zapisania wyniku.
   Zwolnić slot także na każdej ścieżce wcześniejszego odrzucenia.

Próby zablokowane loginem i 503 po sprawdzeniu HTTP liczą się do IP,
lecz nie do błędów hasła. IP-blocked nie dopisują rekordów ani nie przesuwają
deadline. Nieistniejący login ma ten sam licznik, dummy Argon2 i komunikaty;
bez ujawniania nazwy ogranicznika lub informacji o istnieniu konta.

**Świadomy kompromis:** login liczy zakończone, zapisane błędy, więc dwie
równoległe weryfikacje przy siedmiu błędach mogą dać **9 zamiast 8**.
Semafor zajęty przed odczytem limitów i trzymany do commit ogranicza ten wyścig
do jednego dodatkowego błędu; limit IP 30 jest atomowy.
Restart zachowuje zapisane liczniki. Crash może zgubić wynik najwyżej dwóch
trwających weryfikacji; ich próby IP pozostają. Nie ma lease/recovery wyniku.
To zaakceptowane ograniczenie, a nie gwarancja ścisłego limitu konta.

429 ma Retry-After = max(1, ceil((najpóźniejszy deadline blokujących
ograniczeń − now) w sekundach)). Dla IP=30: najstarsza próba+900 s.
Dla N≥8 błędów: musi wypaść N−7 najstarszych; użyć czasu ostatniego
z nich+900 s (przy N=9 drugiego najstarszego). Obliczać po dopisaniu próby IP.
Brak permanentnego lockoutu, progresji kar i twardego limitu dobowego.

Cleanup usuwa rekord dopiero, gdy attempted_at i ewentualne failed_at
są starsze niż okno; wykonuje się na starcie i przy próbach loginu.
Twardy pułap **4096 rekordów** sprawdzany atomowo po cleanup: pełna tabela
daje 503 bez nowego wpisu/Argon2id; nie usuwać aktywnych liczników.
To ochrona rozmiaru, nie limit dobowy. Jeśli długa weryfikacja utraciła rekord
przez cleanup, odrzucić wynik 503. Brak danych auth w logach i żadnej tabeli
telemetrycznej; zwykłe redagowane logi z rotacją wystarczają.
NTP i poprawny UTC są wymaganiem; przyszłe timestampy liczyć konserwatywnie,
nie usuwać ich jako „nieaktualnych” po cofnięciu zegara.

## 5. Sesje bez obowiązkowej okresowej rotacji

Zachowujemy model z §2, bez zmian schematu sesji i dodatkowej kryptografii.
8B1-C usunięto z obowiązkowego planu. Okresowa rotacja może wrócić wyłącznie
jako osobny projekt po ocenie ryzyka; nie jest warunkiem Phase 9.
Kradzież działającego cookie nadal wymaga revocation; sam nowy login nie
odwołuje innych sesji. To zaakceptowane ryzyko długowiecznych zaufanych urządzeń.

## 6. 8B2 — Cloudflare Access and Internet Readiness (PLANNED)

Cel: przygotować i zweryfikować konfigurację/testy, **bez publikacji produkcji**.
Docelowo: Browser → Cloudflare Access → Cloudflare Tunnel → Work Tracker.
Pozostaje również login Work Trackera; Access nie tworzy jego sesji.

- Access: jawna allowlista osób/tożsamości, default-deny, bez Bypass/Everyone.
  Zweryfikować pokrycie całego browser hostname/API, aliasów i metod.
  Preferować `Protect with Access` w cloudflared: walidacja Access JWT
  z właściwym team/issuer, audience, podpisem i expiry; sam nagłówek nie wystarcza.
  Brak/błędny token musi zostać odrzucony także przy błędnym routingu edge.
  [Cloudflare opisuje walidację na origin path](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/#3-validate-the-access-token).
- Origin: browser listener dostępny tylko kontrolowanej ścieżce cloudflared;
  firewall/bind bez publicznego portu, zapasowego DNS/hostname omijającego Access.
  Oddzielna prywatna ścieżka HA dopuszcza tylko dwa istniejące endpointy
  i zaufane źródło LAN, nadal z X-Webhook-Token. Publiczny ingress wyklucza je
  **również dla zalogowanej osoby w Access**; sprawdzić normalizację path.
- HTTPS użytkownika, Secure=true, jawny publiczny Origin i trusted hosts.
  Łącze cloudflared→origin lokalne/chronione; przy przejściu przez sieć użyć TLS
  z weryfikacją certyfikatu. Bez automatycznego zaufania do Forwarded.
- Uvicorn: jawne workers=1, zaufane proxy peers ograniczone do właściwego
  transportu cloudflared, nigdy forwarded-allow-ips='*'. Zachować surowy peer
  do rozróżnienia ingressów; samo scope.client po przepisaniu nie jest dowodem.
- **Access nie naprawia wspólnego peer IP tunelu.** Zdefiniować jeden sposób
  pobrania client IP: CF-Connecting-IP wyłącznie na potwierdzonej ścieżce
  edge→własny tunnel→origin, gdzie klientowskie nagłówki są nadpisywane.
  Nie brać pierwszego X-Forwarded-For. Bez Workers modyfikujących IP i bez
  Pseudo IPv4 Overwrite Headers; zachować rzeczywiste IPv6 i normalizację /64.
  Brak/wielokrotny/błędny nagłówek na zaufanej trasie: login 503, bez losowego
  fallbacku do wspólnego peera; na bezpośredniej LAN trasie nagłówki ignorować.
  [Semantyka CF-Connecting-IP i wyjątki](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip)
  wymagają testów rzeczywistego przebiegu, nie tylko deklaracji configu.
- Backupy DB, env, tunnel credentials i dane Access poza checkoutem;
  sprawdzić uprawnienia service user/root oraz brak dostępu przez static mount.
  Access nie chroni kopii plików udostępnionych inną drogą.

Zweryfikowano online 2026-10-25: [Access ma wariant Free](https://www.cloudflare.com/sase/products/access/),
a [plan Zero Trust Free ma limit 50 użytkowników](https://www.cloudflare.com/plans/).
[WAF Free rate limiting](https://developers.cloudflare.com/waf/rate-limiting-rules/#availability)
ma 1 regułę, zliczanie po IP, okres i mitigation po 10 s; nie odtwarza
limitera aplikacji 15 minut/login. WAF jest opcjonalnym dodatkiem.
Przed wdrożeniem ponownie sprawdzić plan konta, polityki Access/IdP i dostępność
`Protect with Access` w używanej wersji cloudflared; nie zakładać uprawnień
do płatnych funkcji. Bez dowodu skutecznej ochrony Access brak zgody na Phase 9.

## 7. Minimalny DB/config, wdrożenie i rollback

Ten PR niczego nie migruje ani nie wdraża.
8B1-A: tylko BROWSER_ALLOWED_ORIGINS w przyszłym manifest/config oraz zmiany
guardu/klienta/headers; zero nowych kluczy i pól sesji.
8B1-B: jedna addytywna migracja login_attempts z indeksami; istniejące dane
pracy, użytkownicy i sesje bez zmian. Semafor/progi są stałymi, nie zestawem
nowych ustawień. Nowe wymagania proxy/Access należą do osobnego PR 8B2.
Dzisiejszy unit nie przypina jawnie workerów/proxy trust; common.sh nie ma
validatora nowej allowlist — przyszła implementacja musi objąć deploy tests.

Wdrożenie A, następnie B w LAN: zachować clean-worktree, backup SQLite przed
update, fast-forward-only, migracje jako service user i rollback updatera.
Wspólny cutover backend/frontend; stary klient bez znacznika dostaje 403.
Po zmianie IP/hosta poprawić allowlist/CORS, zrestartować, sprawdzić login,
heartbeat i mutację, a potem usunąć stary origin; zaktualizować URL HA.
Sprawdzić login/logout, hidden-tab idle, eksporty oraz oba webhooki HA.

Rollback kodu/frontend razem, pozostawić addytywną tabelę.
Nie przywracać starej DB kosztem nowych eventów/korekt; restore odtwarza również
dawne sesje i liczniki, więc przed wznowieniem odwołać przywrócone sesje
i odczekać pełne 15 minut dla nieaktualnej historii logowania.
Powrót do 8A3 usuwa nowy CSRF/limiter: tylko awaryjnie w izolowanym LAN.
Po publicznym rollout najpierw wyłączyć publiczny routing; nie usuwać Access
jako sposobu „naprawy” problemów. Nie cofać ochrony do anonimowego 8A2.

## 8. Testy akceptacyjne i regresyjne

Poniższe scenariusze są wymaganiami implementacji, nie raportem wykonanych testów.

| ID | Wymagany dowód |
|---|---|
| A1 | Cross-site GET /me, także bez Sec-Fetch-* i przy należnym touch: zero zapisów wszystkich pól sesji i zero Set-Cookie. |
| A2 | Heartbeat: brak sesji 401, zły/brak/null Origin lub znacznik 403; granice 1 h, 30/180 dni, concurrent touch, revoked/expired bez revival. |
| A3 | Formularze/simple requests, text/plain/form/multipart, brak Origin/header, lookalike, subdomena/port i błędny preflight nie mutują; poprawne LAN HTTP bez metadata działa. |
| A4 | Login/logout również egzekwują politykę; przyszła trasa/mount/method default-deny; zachować 204 logout i brak anonimowych danych. |
| A5 | Body 16383/16384/16385 bajtów, streaming/Content-Length/encoding; hasła 15–1024 znaków, 1024 Unicode jako escape i surrogate pairs; brak echa sekretów. |
| A6 | Realny frontend: tylko widoczna aktywna karta podtrzymuje sesję; stary JSON/blob/401 nie wpływa na nowy login; CSP/cache/nosniff/frame/exports i docs policy. |
| B1 | 8 błędów/login, 30 prób/IP, niezależność sukcesów i brak resetu failures; blocked/503 liczą IP, nie failures; granice okna i Retry-After także dla 9 błędów. |
| B2 | Bariery concurrency: najwyżej 2 Argon2, maksymalnie 9 błędów/login i atomowe 30/IP; żaden write lock podczas hashowania, brak nieograniczonej kolejki. |
| B3 | Restart zachowuje zapisane daty; crash weryfikacji pozostawia IP; SQLite busy/błąd zapisu→503 bez cookie. Równoległy rehash nie nadpisuje zmiany hasła. |
| B4 | Normalizacja nazw i dummy dla unknown bez enumeration; IPv4 /32, IPv6 /64, mapped IPv4 i zmiana adresu w tym samym /64; spoofed headers w LAN ignorowane. |
| B5 | Cleanup granic/failed_at, 4096 rekordów przy losowych loginach/IP, bez wzrostu po pułapie i bez usuwania aktywnych okien; clock rollback nie otwiera limitu. |
| X1 | HA ingest i tokenowa timestamp correction bez browser cookie/Origin/header, dobry/zły/brak tokenu, także podczas throttlingu; raw timestamp immutable. |
| X2 | Addytywny upgrade/head/constraints i zachowanie domeny; backup/restore/rollback, permissions/manifest/service jako dotychczas; bez sekretów w logach. |
| C1 | 8B2: missing/forged/expired/wrong audience Access JWT, niedopuszczona osoba, alias/ścieżka/metoda i bezpośredni origin nie omijają Access; HA publicznie odrzucone. |
| C2 | Dwaj klienci z różnych publicznych IP/prefiksów za tunelem mają osobne IP budgets; ten sam NAT lub /64 współdzieli limit. Spoofed CF/XFF/Forwarded nic nie zmienia, błędne IP fail-closed, IPv6 zachowane; HTTPS/Secure i dodatkowy login WT. |

8B2 przygotowuje lokalne testy/config i checklistę C1–C2 bez publikacji;
Phase 9 wykonuje je ponownie na docelowej trasie przed dopuszczeniem użytkowników.
Przyszłe PR: pełne pytest, npm test/build i właściwe deploy tests z README.
Nie ma warunku testowania odłożonej rotacji.

## 9. Akceptowane ryzyka i bramka Phase 9

Czasowy DoS nowych loginów przez wyczerpanie konta/IP/tabeli pozostaje możliwy;
istniejące sesje i HA nie są z tego powodu odwoływane/blokowane.
Mały wyścig limitu konta, utrata nieznanego wyniku przy crash i współdzielone
NAT/IPv6 /64 są jawnie przyjęte. Rozproszony flood wymaga ochrony infrastruktury;
nie dodajemy osobnego systemu telemetrycznego ani dobowej polityki blokad.
CSRF bez tokenu zależy od Origin/CORS/JS i bezpieczeństwa dozwolonych origins;
nie rozwiązuje XSS. LAN HTTP, kradzież cookie i długi lifetime są ryzykami.

**Realne blokery przed Phase 9:** brak implementacji/testów A/B, dowodu
szczelności Access/origin/private HA oraz poprawnego client IP/HTTPS i ochrony
backupów. Wspólny peer tunelu bez bezpiecznej identyfikacji IP nie spełnia C2.
Brak okresowej rotacji nie jest blokerem. Nie ma zgody na publiczny rollout
na podstawie samej tej specyfikacji.

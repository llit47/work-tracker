# Phase 8 — praktyczny projekt bezpieczeństwa 8B1 / 8B2

**Status: 8B1-A / 8B1-B DONE (potwierdzenie operatora); 8B2 IN PROGRESS.**
Operator potwierdził wdrożenie A/B, package-install i service-user runtime na Debianie 13 LXC.
8B2 ma implementację i lokalne dowody ingress/JWT; lokalna ponowna walidacja
package/runtime X2 pozostaje BLOCKED. Publiczne E2E i aktywacja należą do Phase 9.
Phase 8 pozostaje IN PROGRESS; obecna produkcja jest LAN-only.
Kolejność: **8A3 → 8B1-A → 8B1-B → 8B2 → Phase 9**.

## 1. Stan wyjściowy 8A3 i decyzja o uproszczeniu

Kod i testy 8A1–8A3 już zapewniają Argon2id, server-side sessions, revocation,
centralny pure-ASGI default-deny dla całego HTTP /api oraz oddzielny token HA.
`user_sessions` zawiera tylko ID, user_id, SHA-256 tokenu i daty sesji.
Head Alembic to `20261006_07`. W 8A3 nie było limitera, heartbeat POST, kontroli
Origin/custom header, keyringu ani okresowej rotacji.
W stanie wyjściowym 8A3 GET /me **mógł zapisać last_seen_at**; frontend używał go co 5 minut
w widocznej karcie i przy focus. Read-only /me wdraża 8B1-A.
CORS jest już restrykcyjny; nie jest samodzielną ochroną przed CSRF.

Decyzja: bezpieczeństwo proporcjonalne do małego wdrożenia self-hosted.
8B1-A nie wymaga nowej tabeli ani sekretu; 8B1-B dodaje jedną tabelę historii
prób. Nie projektujemy tokenów CSRF, kluczy HMAC, lease, telemetrycznych tabel
ani protokołu rotacji/recovery. Działające zabezpieczenia pozostają.
Późniejszy publiczny dostęp wymaga dodatkowo weryfikacji 8B2.

README opisuje zaimplementowane kontrakty 8B1-A i konfigurację operatora.
Status walidacji wyznaczają ROADMAP i PROJECT_STATUS.
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

## 3. 8B1-A — Lightweight Browser Security (DONE — operator-confirmed)

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
Przykłady: LAN `http://10.123.45.10:8000` albo
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
| POST /api/auth/login | Origin + znacznik + JSON; nowa sesja/cookie albo ogólne 401. Limiter, 429 + Retry-After i 503 przy przeciążeniu dopiero w 8B1-B. |
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
dla prostoty ich interaktywny HTML jest wyłączony (również development),
pozostawiając chronione OpenAPI JSON. Nie dodajemy wyjątków CSP dla CDN/inline.
Nosniff, Referrer-Policy: no-referrer, X-Frame-Options: DENY.
Auth/private API/CSV/PDF i ich błędy: Cache-Control: no-store;
login shell również no-store. Publiczne hash assets mogą być cache'owane.
HSTS dopiero w profilu HTTPS po 8B2, bez wymuszania HTTPS w LAN.

## 4. 8B1-B — Simple Login Rate Limiting (DONE — operator-confirmed)

### Jedna tabela i ograniczenie zasobów

Tabela `login_attempts` (migracja `20261025_08`): id INTEGER PRIMARY KEY AUTOINCREMENT
(bez ponownego użycia ID), login_key, ip_key, attempted_at UTC,
nullable failed_at UTC; indeksy (ip_key, attempted_at), (login_key, failed_at)
oraz attempted_at do cleanup. Brak FK do users; unknown login działa identycznie.
login_key = SHA-256 ASCII("login:" + kanoniczny username z normalizacji);
invalid username ma wspólny klucz SHA-256 ASCII("invalid-username"). Hash nie jest sekretem
ani gwarancją prywatności nazw, lecz daje stałą długość bez dodatkowego klucza.
ip_key: kanoniczne IPv4 /32, IPv6 /64, IPv4-mapped IPv6 → IPv4.
W LAN używać peer IP; wyłączyć niezaufane przepisanie go przez Uvicorn.
Brak poprawnego źródła IP daje 503; nagłówki proxy dopiero według §6.

Wdrożenie ma **jeden worker Uvicorn**, jawnie utrwalony w unicie;
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

## 6. 8B2 — Cloudflare Access and Internet Readiness (IN PROGRESS)

Zaimplementowano **nieaktywny, opt-in** profil `INGRESS_PROFILE=split`.
Domyślny `lan` zachowuje dotychczasowy transport i brak zaufania do proxy headers.
Nginx/Access/tunnel/firewall nie są instalowane ani aktywowane przez updater.
Szczegółowy [runbook Phase 9](../deployment/phase9-ingress.md) opisuje placeholdery,
konfigurację, cutover bez zmiany HA URL, powrót do LAN i natychmiastową izolację
browser path. [Macierz dowodów C1–C2/X1–X2](security-8b2-verification.md) oddziela
PASS lokalny od BLOCKED w rzeczywistym Cloudflare oraz lokalnych ograniczeń X2.

```text
Browser HTTPS → Access [default-deny, jawne osoby] → własny Tunnel
  → cloudflared LXC [Protect with Access: podpis/JWKS, issuer, expiry, AUD]
  → TLS z weryfikacją CA/SAN → WT LXC Nginx browser [ACL: tylko connector]
  → TCP source 127.0.0.2 → Uvicorn 127.0.0.1:BACKEND_PORT → SQLite
HA LAN HTTP + X-Webhook-Token → WT LXC Nginx HA [ACL: tylko HA]
  → TCP source 127.0.0.3 → ten sam Uvicorn (1 worker, no-proxy-headers)
```

### Granice zaufania i separacja

cloudflared jest w osobnym LXC; nie zakładamy jego loopbacku/IP. Nginx na WT
ma dwa różne listenery z dokładnymi źródłowymi ACL i firewallem na obu rodzinach
IP. Backend nie słucha na LAN. Źródłowy `proxy_bind` rozróżnia wejścia bez
nagłówka „ingress”, bez zaufania do arbitralnego Host i bez modyfikacji
`scope.client`. Peer 127.0.0.2/.3 pozostaje niezmieniony przez Uvicorn.
Pozostałe peery backendu (także 127.0.0.1), WebSocket i niekanoniczne raw path
są odrzucane przed CORS, sesją, body i routerem. Zaufani operatorzy/procesy LXC
pozostają granicą bezpieczeństwa; kod nie chroni przed przejęciem takiego LXC.

Browser blokuje całą przestrzeń `/api/webhook*` niezależnie od metody/cookie/Access.
HA dopuszcza tylko dwa dokładne istniejące POST, bez redirectu i bez reszty API/UI.
Procent encoding (w tym podwójny), backslash, repeated slash i dot segments nie
przechodzą origin guardu; Nginx `proxy_pass` bez URI zachowuje raw target.
Nie istnieje publiczny Bypass dla HA. Token, kontrakty JSON i raw events są
niezmienione. Prywatna ścieżka nie kontaktuje się z Cloudflare/Access i nie
przechodzi przez browser limiter. Alias hosta i HTTP scheme na browser ingress
nie są akceptowane. Sam nagłówek JWT nie jest uwierzytelnieniem aplikacji.

### Access i rzeczywisty client IP

Konfiguracje local i remote wymagają Protect with Access dla całego hostname,
prawidłowego team i jednego AUD, z catch-all 404; brak path exceptions, Everyone,
Bypass i alternatywnego hosta. JWTValidator pobiera JWKS organizacji i sprawdza
podpis, issuer, expiry oraz audience. Dodatkowy login/sesja WT są wymagane.
Allowlista osób jest polityką Edge, nie sprawdzeniem email w JWTValidator.
Lokalny test rzeczywistego walidatora nie potwierdza polityki operatora.

Sprawdzono dokumentację i upstream 2026.10.0: Protect with Access jest obsługiwane
również w remotely-managed originRequest.access. Lokalna binarka parsuje YAML;
remote efektywne config/version wszystkich connectorów wymaga Phase 9.
`TUNNEL_DISABLE_PATH_NORMALIZATION=false` jawnie na każdej replice (2026.10.0
ma default off). Normalizacja matching nie zmienia raw path do originu.
Źródła: [parametry originu](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/),
[remote API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/subresources/configurations/),
[normalizacja](https://developers.cloudflare.com/changelog/post/2026-10-22-cloudflared-path-normalization/).

CF-Connecting-IP jest używany tylko po potwierdzonej trasie connector ACL → Nginx
→ browser peer. Nginx usuwa XFF/Forwarded/X-Real-IP. W LAN nadal ignorujemy
wszystkie nagłówki proxy. Publiczny IP trafia tylko do klucza limitera, nie do
transport peer. Brak/błędny/wielokrotny IP → login 503 bez fallbacku do wspólnego
connectora. IPv4 /32, IPv6 /64 i mapped IPv4 używają istniejącej normalizacji;
progi 8/login, 30/IP/15min, dwa sloty i trwała historia są bez zmian.
Zapisane próby nie są resetowane przy przejściu na HTTPS.

Wymagane wyłączenie Workers/subrequests modyfikujących visitor IP, Pseudo IPv4
Overwrite Headers, Remove visitor IP headers i transformów IP. CF-Worker,
znany cross-zone Worker IP oraz pseudo IPv4 240/4 są dodatkowo odrzucane.
Zwykły klient nie może wybierać bucketu przez CF/XFF/Forwarded; rzeczywiste
nadpisanie CF headers i brak wyjątków trzeba potwierdzić przez Edge.
[Semantyka Cloudflare headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
Opcjonalny WAF może uzupełniać ochronę, nie zastępuje aplikacyjnego limitera.

### HTTPS, konfiguracja i rollback

Split wymaga jednego dokładnego HTTPS origin na 443, Secure=true i pustego CORS.
Host-only HttpOnly/SameSite=Lax, CSRF i 30 dni idle/180 dni absolute bez zmian.
Nginx akceptuje tylko dokładny Host, właściwy TLS/SNI i visitor scheme HTTPS.
Nie ma fallbacku do HTTP UI. Obecne security headers/cache pozostają; opcjonalne
`BROWSER_HSTS_SECONDS=0` domyślnie, po walidacji HTTPS stopniowo 300 → 86400 →
31536000, bez includeSubDomains/preload. Oba klucze manifestu są opcjonalne.
Spójność profilu sprawdza wspólny stdlib validator runtime/updatera przed migracją.
Brak nowej migracji, tabeli, sekretu, protokołu sesji czy CSRF.

Konsola/SSH i kopia LAN env/unit/firewalla chronią przed odcięciem. Cutover
zachowuje dotychczasowy HA IP/port; Nginx zajmuje stary port, backend przechodzi
na loopback na inny port. Rollback publiczny zamyka tylko browser port/route,
pozostawia WT split i HA aktywne. Powrót do LAN UI dopiero po wyłączeniu
publicznego routingu, bez cofania DB i liczników. Pliki infrastruktury operatora
są poza Git i wymagają osobnych kopii/odtworzenia; updater nie zarządza nimi.

## 7. Minimalny DB/config, wdrożenie i rollback

Implementacja dostarcza migrację do wykonania przez operatora; nie wykonano
wdrożenia ani migracji produkcyjnej.
8B1-A: wymagane BROWSER_ALLOWED_ORIGINS w manifest/config oraz zmiany
guardu/klienta/headers; zero nowych kluczy i pól sesji.
8B1-B: jedna addytywna migracja login_attempts z indeksami; istniejące dane
pracy, użytkownicy i sesje bez zmian. Semafor/progi są stałymi, nie zestawem
nowych ustawień. Opcjonalny profil proxy/Access opisuje §6; bieżący LAN pozostaje domyślny.
Unit 8B1-B przypina `--workers 1 --no-proxy-headers`: LAN korzysta z peer IP.
Login jest synchronicznym handlerem; sesja SQLite i semafor są własnością
wykonującego pracę wątku, więc anulowanie HTTP nie zwalnia ich przed końcem
weryfikacji i zapisu wyniku. Pełna tabela daje 503 przed kontrolą budżetów.
Profil 8B2 zachowuje no-proxy-headers także za Nginx.
common.sh i backend współdzielą już validator origins, objęty deploy tests.
Brak allowlisty nie uruchamia nowej wersji: operator musi podać dokładne origins;
przy braku odpowiedzi update uruchamia rollback przed migracją, bez restore DB.

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

Poniższe scenariusze definiują kontrakt. Raport wykonania 8B2 i granice dowodów:
[macierz weryfikacji](security-8b2-verification.md).

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

**Realne blokery przed Phase 9:** lokalne X2 8B2, potwierdzenie skutecznej konfiguracji i dowodu
szczelności Access/origin/private HA oraz poprawnego client IP/HTTPS i ochrony
backupów. Wspólny peer tunelu bez bezpiecznej identyfikacji IP nie spełnia C2.
Brak okresowej rotacji nie jest blokerem. Nie ma zgody na publiczny rollout
na podstawie samej tej specyfikacji.

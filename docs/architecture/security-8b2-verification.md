# 8B2 — macierz wymagań i dowodów

Status implementacji: przygotowano profil opt-in, przykłady i lokalne testy.
**8B2 IN PROGRESS:** lokalne package-install/service-user X2 pozostają BLOCKED.
Publiczny rollout i wszystkie dowody rzeczywistego Edge są osobną Phase 9.
Nie wykonano deploymentu, zmian produkcyjnego LXC, HA, DNS ani tunelu.

Fundament provisioning Nginx z 9B1 ma osobne
[dowody i ograniczenia](../deployment/phase9b1-nginx.md#dowody-lokalne-i-pozostała-praca).
Lokalne testy cyklu usługi nie zamykają X2 ani żadnego dowodu E poniżej.

Poziomy dowodu nie są zamienne:

- **U** — backend ASGI/polityka z kontrolowanym peer; nie dowodzi Access ani ACL sieci.
- **P** — prawdziwy Nginx 1.26.3 → Uvicorn TCP/TLS na loopback, oryginalne
  nagłówki/duplikaty/path, rzeczywiste ACL, cookie i limiter SQLite/Argon2id.
  Symulowany connector dostarcza CF headers; nie jest to prawdziwy Edge.
- **J** — prawdziwy `NewJWTValidator` cloudflared 2026.10.0: RSA, JWKS przez
  lokalny TLS, issuer/time/audience i zły klucz. Nie mock samego nagłówka.
- **K** — oficjalny cloudflared 2026.10.0 `ingress validate/rule` oraz statyczny
  kontrakt local/remote templates. Bez uruchamiania tunelu.
- **E** — prawdziwe konto/polityka Cloudflare, sieć i docelowe LXC. Wszystkie E
  mają status **BLOCKED / REQUIRES PHASE 9 VERIFICATION**, nie lokalny PASS.

| Wymaganie | Status przygotowania/lokalny dowód | Pozostały dowód |
|---|---|---|
| Access default-deny, jawne osoby, bez Bypass/Everyone/path wyjątków | PASS K: access-policy template i ingress_config_test.py | E: skuteczna polityka, allowlista, plan konta, IdP i brak alternatywnych apps |
| JWT brak/podpis/expiry/AUD/issuer/team | PASS J: 6 podpisanych scenariuszy, rzeczywisty JWKS i konstruktor | E: te same negatywne przypadki przez Edge/tunnel |
| Niedopuszczona osoba | Przygotowano wzorzec polityki i live probe; walidator JWT nie ocenia email | BLOCKED E: rzeczywisty wykluczony użytkownik w IdP, także revocation |
| Protect with Access local/remote, cała domena i catch-all | PASS K: oficjalna binarka parsuje local YAML i match; remote JSON zgodny z aktualnym API | E: effective remote config_version wszystkich connectorów, właściwy team/AUD |
| Normalizacja cloudflared | PASS K: 2026.10.0, TUNNEL_DISABLE_PATH_NORMALIZATION=false, bez path rules | E: env każdej repliki, Edge normalization/normalize-to-origin |
| Izolacja browser/HA i zachowanie peera | PASS U/P: 127.0.0.2/.3, inne peery 403; brak przepisywania scope.client | E: rzeczywiste IP connectora/HA, firewall, bindy i brak wspólnego SNAT |
| Bezpośredni browser origin i alias hosta | PASS U/P: ACL źródła, host, TLS cert/SNI, peer backendu | E: trzeci host LAN, Internet, IPv4/IPv6, alternatywny DNS/port |
| Publiczny HA także po Access/loginie WT | PASS U/P: komplet metod, sesja WT, brak dopuszczenia do handlera | E: rzeczywiście poprawny Access + WT cookie, wszystkie warianty |
| Encoded/double encoded path, backslash, //, dot segments, suffix/aliases | PASS U/P: raw targets bez normalizacji klienta, 9 metod, deny przed routerem | E: powtórzyć przez Edge i wybraną wersję connectora |
| HA wyłącznie dwa dokładne POST | PASS U/P: reszta UI/API/metod/path 403; brak redirectów | E: rzeczywista ACL źródła LAN |
| HA entry/exit/correction dobry/zły/brak tokenu | PASS U/P + X1: bez browser cookie/Origin/CSRF/Access | E: dotychczasowe HA URL, bez zmian automatyzacji |
| Niezależność HA od Cloudflare/Access/routingu/limitera | PASS U/P: Cloudflare/tunnel w ogóle nie działa w testach, HA działa także po 429 | E: wyłączenie prawdziwej browser trasy, niedostępny Access/Cloudflare |
| Immutable raw, te same ID/JSON, pairing/godziny/płaca/dashboard | PASS U: entry/exit + correction daje 7h45/387.50 PLN, raw minute bez zmian; pełne domain suites | E: kontrolowany smoke test na produkcyjnej ścieżce |
| Prawdziwy CF-Connecting-IP wyłącznie na zaufanej ścieżce | PASS U/P: tylko browser peer; XFF/Forwarded ignorowane/usuwane, direct peer 403 | E: Edge nadpisuje spoofed CF header; brak Workers/transformów |
| Brak/błędny/wielokrotny IP | PASS U/P: login 503, brak cookie i fallbacku; P sprawdza duplikaty na drucie | E: zachowanie wszystkich rzeczywistych warstw |
| IPv4, IPv6 /64, mapped IPv6, NAT, dwa niezależne IP | PASS U/P: 30/IP, współdzielenie i niezależność; 429 + Retry-After | E: dwie prawdziwe sieci i klient IPv6 za tunelem |
| Workers i Pseudo IPv4 | PASS U: CF-Worker/cross-zone sentinel/pseudo 240/4 deny; instrukcja wyłączenia | E: same-zone/cross-zone Worker test, brak routes i overwrite/remove-IP transforms |
| 8/login, 30/IP, 15 min, 2 Argon2id, 1 worker | PASS U/P + niezmienione B1–B5; unit i override no-proxy-headers | E: efektywny proces systemd, brak dodatkowych instancji |
| Bez resetu konta/IP przy HTTPS/restart | PASS U: ta sama DB i klucze z LAN-era, okno zachowane | E: zachować DB; zmiana rzeczywistego IP/NAT tworzy inny klucz z definicji |
| HTTPS, host, Secure/HttpOnly/Lax/host-only, Origin/heartbeat | PASS U/P: dokładny origin, TLS, cookie, CSRF i wymaganie sesji WT | E: browser HTTPS, certyfikaty między LXC, HTTP odmowa, aktywność/logout |
| HSTS bez odcięcia operatora | PASS U: max-age; default 0, split-only, staged rollout, osobny LAN rollback | E: włączyć stopniowo po testach certyfikatów/HTTPS |
| LAN kompatybilny, brak nowych wymaganych publicznych kluczy | PASS U/K + config shell: default lan, optional manifest, zero automatycznego proxy install | E: kontrolowany update przy dotychczasowym LAN config |
| Sesje/CSRF bez nowych mechanizmów i 30/180 dni bez zmian | PASS: pełne auth/browser/frontend suites, minimalna zmiana źródła IP | E: smoke test bieżącej sesji i nowego loginu |
| X2: Alembic upgrade/head/data | PASS: test_migrations.py w pełnym pytest; head 20261025_08; brak migracji 8B2 | E: standardowa kontrola po updaterze |
| X2: manifest/updater/rollback/layout/shell | PASS: config_update_test.sh, update_rollback_test.sh, application_permissions_test.sh, bash -n | E: operator odtwarza env/drop-in/Nginx/firewall osobno od Git |
| X2: nowa instalacja pakietu lokalnie | BLOCKED: backend_package_install_test.sh zatrzymany, brak ensurepip/python3-venv | Wykonać na środowisku z python3-venv |
| X2: service-user runtime lokalnie | BLOCKED: service_user_runtime_test.sh zgłasza skip; brak uprawnień roota do runuser; sudo no-new-privileges | Wykonać jako root; skip nie oznacza PASS |
| Baseline 8B1-A/B package/runtime i działająca produkcja | OPERATOR-CONFIRMED, Debian 13 LXC; nie test Codexa | Nie zastępuje lokalnego X2 zmienionego kodu ani E dla 8B2 |
| Backup/env/credentials poza Git i static | PASS: przykłady tylko placeholdery, dotychczasowe testy layout; brak sekretów | E: rzeczywiste właścicielstwo, permissions, backup/restore i static root |
| Natychmiastowe wyłączenie publicznego routingu bez HA | Przygotowano runbook; P HA działa bez tunelu | E: zamknąć wyłącznie browser port/route i potwierdzić HA |
| Zakres: bez Phase 9 aktywacji/Calendar/rotacji/recovery/RBAC/Redis | PASS: brak takich zmian; nie wykonano deploymentu/merge/release/review trigger | Phase 9 wymaga osobnego wykonania operatora |

## Powtarzalne komendy lokalne

Z root repo, dla dodatkowych narzędzi użyć istniejących binarek albo wypakować
publiczne pakiety do katalogu tymczasowego. Nie wymaga to instalacji systemowej.
Nginx/Go/cloudflared są narzędziami walidacji, nie nowymi zależnościami backendu.
Sockety loopback mogą wymagać uruchomienia poza sandboxem.

```bash
cd backend
. .venv/bin/activate
pytest
pytest tests/test_migrations.py
alembic heads
cd ../frontend
npm test
npm run build
cd ..
bash -n install.sh update.sh deploy/common.sh deploy/update_rollback.sh deploy/tests/*.sh
deploy/tests/config_update_test.sh
deploy/tests/update_rollback_test.sh
deploy/tests/application_permissions_test.sh
deploy/tests/backend_package_install_test.sh
sudo deploy/tests/service_user_runtime_test.sh
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> backend/.venv/bin/pytest deploy/tests/ingress_security_test.py -q
WORK_TRACKER_CLOUDFLARED_BIN=<CLOUDFLARED_BIN> backend/.venv/bin/pytest deploy/tests/cloudflared_config_test.py deploy/tests/ingress_config_test.py -q
CLOUDFLARED_SOURCE_DIR=<OFFICIAL_SOURCE_2026.10.0> WORK_TRACKER_GO_BIN=<GO_BIN> deploy/tests/cloudflared_access_test.sh
git diff --check
```

Źródło Go: oficjalne archiwum tagu cloudflared 2026.10.0
`https://github.com/cloudflare/cloudflared/archive/refs/tags/2026.10.0.tar.gz`.
Lokalnie SHA-256 archiwum:
`60203c146da07015fda8b1c583939828913f1310d63716666124a89c0f86bd8c`.
Runner sprawdza również SHA pliku upstream JWTValidator przed testem, kopiuje
źródła do temp i używa ich rzeczywistego konstruktora/JWKS; nie zmienia produkcji.
Go zgodny z go.mod upstream (lokalnie 1.27.3). Pierwsze uruchomienie pobiera
zależności Go; kolejne może korzystać z cache. Nie uruchamia cloudflared run.
Binarka cloudflared `ingress rule` pokazuje dopasowanie hostname/service;
nie jest dowodem walidacji JWT ani załadowania remote konfiguracji w koncie.

Ostatnie wykonanie lokalne: pełny backend 751 PASS (w tym 170 przypadków U
z test_ingress.py i migracje), frontend test/build PASS, P 8 PASS, J 6 PASS,
K 4 PASS, config/rollback/layout/bash PASS. Pakiet/runtime: BLOCKED jak powyżej.
Alembic head: `20261025_08`; żadnej nowej migracji. Dokładny wynik po końcowych
zmianach podany również w PR. Baseline operatora pochodzi z zadania, nie z
połączenia z jego LXC.

Live probes są wyłącznie przygotowane: `deploy/tests/access_live_test.py`.
Bez prawdziwej konfiguracji nie wykonano ich i nie oznaczono PASS.
Do wykonania w Phase 9 według [runbooka](../deployment/phase9-ingress.md).

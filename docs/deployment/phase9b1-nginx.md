# Phase 9B1 — opcjonalne przygotowanie Nginx

9B1 dostarcza fundament przygotowania, weryfikacji i cofania prywatnego ingressu.
Ten dokument opisuje warstwę 9B1. Kompletny instalator i cutover z 9B2
opisuje [aktualny runbook](phase9b2-cutover.md). DB i kontrakt webhooków pozostają bez zmian. Żadna z poniższych operacji nie zarządza cloudflared,
Cloudflare Access, DNS, firewallem, Proxmoxem ani Uvicornem.

## Kontrakt narzędzia

Z root repo: `python3 deploy/nginx_provision.py <operacja>`. System: Debian/Ubuntu,
Python >= 3.10, OpenSSL 3, `ip`, `ss`, systemd, istniejący `/usr/sbin/nginx`
z modułem HTTP SSL i użytkownik `www-data`. Kod korzysta wyłącznie ze stdlib.
Brak zależności kończy operację odmową. Narzędzie **nie instaluje pakietów**.
Bezpieczny bootstrap pakietów zapewnia osobna komenda 9B2, przed użyciem tego
narzędzia. Nie wykonywać zwykłego `apt install nginx` na świeżym hoście: może
uruchomić domyślną usługę i listenery. Przed użyciem na hoście z już zainstalowanym Nginx
operator sprawdza wszystkie istniejące listenery i konfiguracje. Narzędzie ich
nie wyłącza, nie przejmuje i nie przeładowuje.

| Operacja | Skutki |
|---|---|
| `plan --config <plik>` | Odczyt i walidacja operatora, IP, lokalnego bindu, portów, TLS i permissions; identyfikator generacji. Bez zapisu, `nginx -t`, bindu lub systemctl |
| `validate --config <plik>` | Te same kontrole read-only; nie jest testem składni Nginx |
| `prepare --config <plik>` | Root: nieaktywna generacja z kopiami TLS i unit template, rzeczywisty `nginx -t` w prywatnym stagingu, atomowa publikacja; brak usługi/listenerów |
| `verify` | Read-only: hashe, permissions i aktualne TLS przygotowanej generacji; przy aktywacji dodatkowo procesy, bindy i handshake TLS. Bez naprawy/reloadu |
| `activate --evidence <plik>` | Root: wyłącznie jawne uruchomienie osobnej prywatnej instancji po wszystkich bramkach; nie wykonuje cutover aplikacji ani enable/autostartu |
| `rollback` | Root: zatrzymuje własny ingress, usuwa wyłącznie własny unit, zachowuje przygotowanie i backup; nie przywraca env/Uvicorna/DB |

`plan`/`validate` dopuszczają zajęty dotychczasowy port HA i zgłaszają ten stan.
`prepare` także go nie zajmuje. Browser port nie może być zajęty. Obcy listener
na backend port poza `127.0.0.1` blokuje przygotowanie. Kontrola konfliktów jest
celowo konserwatywna: ten sam port na innym adresie również blokuje operację.
Sprawdzane są TCP IPv4 i IPv6. Ostateczny bind następuje dopiero przy `start`;
konflikt powstały po kontroli uruchomi rollback, bez zatrzymywania innego procesu.

[`nginx -t`](https://nginx.org/en/docs/switches.html) otwiera pliki konfiguracji
i może tworzyć katalogi robocze. Dlatego występuje tylko w mutującej operacji
`prepare`, na kopii z podmienionymi wyłącznie ścieżkami TLS/runtime w stagingu.
Nie jest częścią read-only planowania. Gotowy unit uruchamia ten sam zestaw
serwerów osobnym `-c`/`-p`, bez include `/etc/nginx`, sites-enabled, generic
proxy, wildcard bindów i IPv6 listenerów. Nginx master działa jako root,
worker jako www-data; runtime jest oddzielny. Access log jest wyłączony,
error log ma poziom crit. Narzędzie nie drukuje wyjścia OpenSSL/Nginx/systemctl,
kluczy, tokenów ani zawartości konfiguracji aplikacji.

## Pliki operatora i przygotowanie

Przykłady wymagające uzupełnienia:
[provision.json.example](../../deploy/ingress/provision.json.example),
[activation-evidence.json.example](../../deploy/ingress/activation-evidence.json.example).
Nie są działającą konfiguracją ani dowodem infrastruktury.

Operator tworzy poza checkoutem, np. `/etc/work-tracker/ingress-input/`, katalog
root:root 0700, plik `provision.json` root:root 0600. Certyfikat, niezaszyfrowany
klucz i zaufana CA także pozostają poza checkoutem/static root. Klucz wymaga
0600, cert/CA mogą mieć 0600/0644; właściciel root, bez zapisu grupy/innych
w całym łańcuchu katalogów. Symlinki i niezaufany właściciel są odrzucane.
Nie umieszczać plików w `/tmp` na produkcji. Narzędzie nie generuje kluczy,
certyfikatów, tokenów ani haseł.

Wszystkie klucze JSON są wymagane, bez domyślnych IP/portów/hostów. Parametry
z przykładu operator musi potwierdzić; adres HA jest celowo placeholderem:

| Parametr | Wartość/warunek dla obecnej infrastruktury |
|---|---|
| WT | `<WORK_TRACKER_LAN_IP>`; adres musi należeć do lokalnego interfejsu |
| Connector | `<CLOUDFLARED_SOURCE_IP>`; potwierdzić transport peer i brak SNAT |
| HA | `<OBSERVED_HA_SOURCE_IP>`; potwierdzić transport peer webhooków |
| Porty | docelowo HA 8000, browser TLS 8443, backend 8001; trzy różne porty 1024–65535 |
| Hostnames | jawny lowercase DNS public hostname i origin TLS name, bez wildcardów/aliasów/portów |
| TLS | dokładny DNS SAN originu (CN nie wystarcza), zgodny klucz, co najmniej 7 dni ważności leaf, poprawny łańcuch CA i serverAuth, ścisła weryfikacja X.509 |

9B1 obsługuje wyłącznie konkretne RFC1918 IPv4 dla listenera i dwóch różnych
źródeł. Nie przyjmuje CIDR, publicznych/loopback IP ani IPv6. To ograniczenie
adresowania **nie usuwa obowiązku ochrony IPv6**: Host może mieć IPv6; Uvicorn i inne usługi wymagają audytu.
Bez dowodów skutecznej izolacji obu rodzin aktywacja jest zablokowana.

Po spełnieniu zależności, na kontrolowanym hoście operator może wykonać:

```bash
sudo python3 deploy/nginx_provision.py plan --config /etc/work-tracker/ingress-input/provision.json
sudo python3 deploy/nginx_provision.py validate --config /etc/work-tracker/ingress-input/provision.json
sudo python3 deploy/nginx_provision.py prepare --config /etc/work-tracker/ingress-input/provision.json
sudo python3 deploy/nginx_provision.py verify
```

9B1 sam nie wykonuje tych komend na produkcji. Pliki zarządzane:

- `/etc/work-tracker/ingress/generations/<sha256>/`: kompletne generacje 0700,
  wszystkie pliki 0600, także kopie cert/key/CA, manifest hashy i unit.
- `/etc/work-tracker/ingress/prepared.json`: atomowy wskaźnik, 0600.
- `/etc/work-tracker/ingress/active.json`: dziennik write-ahead aktywacji, 0600.
- `/var/backups/work-tracker/ingress/`: backupy 0700/0600; poprzedni wskaźnik
  przygotowania, brak jednostki przed aktywacją, dowody i wynik rollbacku.
- `/etc/systemd/system/work-tracker-ingress.service`: powstaje dopiero przy
  `activate`, root:root 0644; 9B1 nie tworzy linku autostartu. Sekcję Install wykorzystuje
  dopiero 9B2 po zatwierdzeniu całej transakcji.
- `/run/work-tracker-ingress/`: runtime systemd 0750 root:www-data.

Powtórne przygotowanie tych samych wejść nie zmienia plików ani usługi.
Zmiana wejść/TLS/kodu/wzorców tworzy nową generację; poprzednie pozostają do
odtworzenia i audytu. `prepare` odmawia przy istniejącym dzienniku aktywacji.
Pomiędzy publikacją katalogu i wskaźnika ponowienie odzyskuje kompletną,
zweryfikowaną generację. Osierocone `.stage-*` po SIGKILL nie są aktywne;
operator może usunąć je po sprawdzeniu, bez dotykania kompletnych generacji.
Nie nadpisywać istniejących generacji ręcznie. Backup wskaźnika zachowuje
odwołanie do starszych kopii TLS; nie usuwać ich w ramach sprzątania backupów.
Zmianę/odnowienie TLS wykonywać przez nową generację według runbooka 9B2,
z kontrolowanym powrotem do LAN; nie edytować aktywnej generacji.

## Bramka jawnej aktywacji prywatnej

Aktywacja nie jest krokiem przygotowania ani poleceniem do wykonania w tym PR.
Wymaga osobnego okna operatora i przygotowanego cofnięcia aplikacji do LAN.
Narzędzie nie zatrzyma obecnego Uvicorna w celu zwolnienia portu 8000.

Raport 0600 root:root poza checkoutem musi wskazywać dokładną generację,
świadomy timestamp sprzed najwyżej 24 godzin oraz prywatny plik z dowodami.
Każda flaga musi być literalnym `true` po rzeczywistym sprawdzeniu:

1. Efektywny firewall IPv4: tylko connector → TLS, tylko obserwowany HA → HA,
   tylko loopback → backend; reguły przed ogólnym LAN allow, bez DNAT/public origin.
2. Efektywny firewall IPv6: analogiczne deny dla całego originu i backendu,
   brak alternatywnego listenera, routingu i obejścia przez globalny IPv6.
3. Źródła HA/connectora potwierdzone na WT; różne IP bez wspólnego SNAT.
4. Negatywne próby z trzeciego hosta LAN i zewnętrznej IPv4/IPv6 wraz z kontrolą
   reguł/liczników, a nie tylko connection refused na jeszcze nieotwartym porcie.
5. Publiczna trasa WT nie istnieje/jest wyłączona; współdzielony tunnel i inne
   usługi pozostają bez zmian. Nie wyłączać współdzielonego connectora.
6. Dostęp konsolowy, backup i przećwiczone cofnięcie env/drop-in/Uvicorna do LAN.

Raport jest świadomym poświadczeniem operatora, **nie automatycznym dowodem
firewalla**. Program nie ma dostępu do API Proxmoxa/Cloudflare i nie wnioskuje
bezpieczeństwa z samej ACL Nginx. Nie wolno przepisywać flag z lokalnych testów.
Bez tych dowodów nie aktywować; brak raportu/false/stary timestamp odmawia operacji.

Dodatkowo kod sprawdza na żywo backend `work-tracker.service`: PID, argumenty
Uvicorna `127.0.0.1:<backend_port>`, jeden worker, `--no-proxy-headers`, runtime
env `split`, dokładny HTTPS origin, Secure=true, pusty CORS, HSTS=0 i pojedynczy
socket należący do tego PID wyłącznie na loopback. Nie drukuje `/proc/*/environ`.
Port HA i browser muszą być wolne w obu rodzinach IP. Dopiero po tym zapisuje
backup i dziennik, instaluje własny unit, uruchamia i sprawdza jego PID/sockety
oraz TLS CA/SNI. Nie wprowadza publicznego routingu. Dwa wejścia i proxy_bind
127.0.0.2/.3 pochodzą z projektu 8B2; kontrola `X-Webhook-Token` nadal jest
w backendzie. Sam TLS handshake nie zastępuje testów webhooków i aplikacji.

## Rollback i awarie

```bash
sudo python3 deploy/nginx_provision.py rollback
```

W przypadku błędu po zapisaniu dziennika `activate` próbuje wykonać rollback.
Po przerwaniu procesu/SIGKILL operator wywołuje go jawnie; nie potrzeba wtedy
oryginalnego JSON, ważnego certyfikatu, klucza ani sprawnego backendu.
Operacja jest powtarzalna. Weryfikuje hash własnej jednostki, zatrzymuje ją,
potwierdza stan inactive, usuwa unit i wykonuje daemon-reload. Backupy i
przygotowane generacje pozostają. Obca/zmieniona jednostka nie jest nadpisywana.
Jeżeli stop/reload/cofnięcie zawiedzie, dziennik pozostaje do ponowienia lub
inspekcji operatora; sukces nie jest zgłaszany.

Dla transakcji 9B2 CLI odmawia samego rollbacku Nginx: użyj
`ingress_cutover.py rollback-lan`, aby przywrócić całą aplikację.

Rollback samego 9B1 zwalnia HA port i chwilowo odcina proxy do HA: **nie jest to
pełny rollback aplikacji**. Operator następnie przywraca zapisany LAN env i
wyłącznie ingress drop-in aplikacji, daemon-reload i restart WT na poprzednim
porcie, według [runbooka](phase9-ingress.md#7-natychmiastowe-wyłączenie-i-rollback).
Nie odtwarzać starej DB, nie usuwać raw events ani historii limitera.
Samo awaryjne wyłączenie publicznej trasy przy działającym split/HA opisano
osobno w tym runbooku; nie używa ono `rollback` całego prywatnego ingressu.

## Dowody lokalne i pozostała praca

Komenda z root repo (brak binarki kończy test błędem):

```bash
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> backend/.venv/bin/pytest deploy/tests/nginx_provision_test.py deploy/tests/ingress_security_test.py deploy/tests/ingress_config_test.py -q
```

Lokalnie 55 testów 9B1 PASS: prawdziwy Nginx 1.26.3/OpenSSL, read-only i
przygotowanie z zajętym HA portem, idempotencja, uprawnienia/symlinki/właściciel,
SAN/CN/CA/klucz i expired/future/near-expiry TLS, konflikty IPv4/IPv6, wszystkie
bramki, start/ACL/TLS, backup i rollback, błędy `nginx -t`/start/reload/postcheck/
stop, odzyskanie z dziennika po symulowanym SIGKILL, kontrola rzeczywistego
procesu Uvicorna i niezmieniony domyślny LAN/updater. Dodatkowo regresje 8B2:
8 prawdziwego Nginx→Uvicorn i 3 kontraktu konfiguracji PASS (HA entry/exit i
korekta bez sesji, dobry/zły/brak tokenu, surowe ścieżki, auth/CSRF/limiter).
Łącznie zestaw ingress: 66 PASS. Pełny backend: 751 PASS; frontend `npm test`
i `npm run build`: PASS. `config_update_test.sh`, `update_rollback_test.sh`,
`application_permissions_test.sh`, `bash -n` i `git diff --check`: PASS.
Alembic head pozostaje `20261025_08`; brak zmian schematu.

Testy 9B1 mapują RFC1918 na loopback i symulują systemd/raport sieciowy; procesy
Nginx/Uvicorn i OpenSSL są prawdziwe. Nie wykonano instalacji pakietu Nginx,
root-master/worker www-data, jednostki w prawdziwym systemd, restartu LXC,
firewalla Proxmoxa, probe z realnego LAN/Internet IPv4/IPv6 ani produkcyjnego
cutover. Pełny backend i istniejące shell testy są raportowane w PR. X2 8B2
package-install nadal BLOCKED (brak ensurepip), service-user runtime BLOCKED
(brak roota/sudo no-new-privileges). To ograniczenia środowiska, nie PASS.

Dokładny dalszy zakres:

- **9B2:** implementacja gotowa lokalnie: opcjonalny bootstrap/instalator i pełny
  cutover/rollback/boot recovery; [procedury i granice testów](phase9b2-cutover.md).
- **9C:** test na Debian/Ubuntu, także unprivileged kontenery: prawdziwy systemd, root/worker,
  pakiety, bindy 8000/8443/8001, obserwowane źródło HA, firewall IPv4/global IPv6,
  negatywne probes, CA na connectorze 2026.10.3, HA ingestion/correction bez sesji
  i przy awarii browsera, backup/pełny LAN rollback oraz reboot. Domknąć X2 8B2.
- **9D:** osobno zatwierdzony publiczny rollout po bramce Phase 8/9C: pojedyncza
  trasa WT we współdzielonym local config (bez nadpisania innych usług), Access
  i JWT/IdP/Edge/normalizacja, dwie sieci i IPv6/Workers/spoofing, cookie/CSRF/
  limity/HSTS, odcięcie tylko browser route bez utraty HA i końcowy rollback.
  Żaden z tych testów nie został tutaj wykonany.

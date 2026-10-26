# Phase 9B2 — instalacja i prywatny cutover

Narzędzia są dla Debiana/Ubuntu z systemd, Pythonem >= 3.10 i OpenSSL 3.
Nie konfigurują Cloudflare, DNS, routera, firewalla, hypervisora ani HA.
HTTPS oznacza przygotowanie prywatnego ingressu, **nie działającą publiczną stronę**.
Publiczny rollout jest osobną Phase 9D po testach 9C i bramkach Phase 8.
Wszystkie parametry i dowody operatora pozostają poza publicznym repozytorium.

## 1. Nowa instalacja LAN

Uruchom `install.sh` według README i zaakceptuj domyślne `lan`.
Podaj jawny LAN origin, adres/port i ustaw Secure=false dla HTTP.
Instalator tworzy użytkownika, env, DB, buduje aplikację i uruchamia systemd.
Ścieżka LAN nie wywołuje bootstrapu ani provisioningu Nginx. Nie wymaga TLS/CA.
Utwórz pierwszego użytkownika według README; instalator nie tworzy konta.

## 2. Nowa instalacja HTTPS

Wybierz `https`. Najpierw powstaje działający LAN z jego poprawnym originem,
który będzie celem ewentualnego rollbacku. Dalsza część automatycznie wywołuje:

```bash
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py bootstrap
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py configure
```

`configure` pyta o wszystkie pola [provision.json.example](../../deploy/ingress/provision.json.example):
prywatny lokalny IPv4, dotychczasowy port HA, nowy port backendu, port TLS,
dokładny browser hostname, SAN originu, zaobserwowane odrębne źródła HA i connectora,
bezwzględne ścieżki certyfikatu, klucza i zaufanej CA. Bez domyślnych IP/hostnames.
Porty: trzy różne liczby 1024–65535. Żadnych CIDR/wildcardów/publicznych IP.
Nie generuje CA, certyfikatów, sekretów ani raportu potwierdzającego bramki.

Przed tym krokiem operator dostarcza TLS/CA poza checkoutem: zaufana hierarchia
root bez zapisu grupy/innych i symlinków, klucz root:root 0600; cert/CA 0600/0644.
Wymagane dokładny DNS SAN, zgodny niezaszyfrowany klucz, serverAuth,
poprawny łańcuch X.509 i co najmniej 7 dni ważności. Walidacja i rzeczywisty
`nginx -t` pochodzą z 9B1. Wynikiem jest nieaktywna generacja, bez listenerów.
Niepoprawne wejście zostaje w prywatnym pliku do poprawy; ponów `prepare`.
Jeśli brakuje TLS/zależności lub walidacja zawiedzie, instalator jasno zgłasza
nieudane przygotowanie HTTPS, a działający LAN pozostaje dostępny.

### Bootstrap pakietów

Na świeżym hoście narzędzie najpierw zapisuje dziennik własności, trwałą maskę
`/etc/systemd/system/nginx.service -> /dev/null` i własną `/usr/sbin/policy-rc.d`.
Polityka odmawia wyłącznie operacji `nginx`/`nginx.service` (kod 101), pozostałe
przepuszcza. Dopiero potem uruchamia apt dla `nginx openssl iproute2`, bez
upgrade istniejących pakietów i z needrestart w trybie listowania.
Kontrola listenerów przed apt korzysta z `/proc/net/tcp{,6}`, więc działa
także bez iproute2. Maska i polityka pozostają po rollbacku, SIGKILL i awarii apt: chronią restart
systemu i późniejsze aktualizacje pakietu. Ponów `bootstrap`, nie usuwaj osłon.
Nie zmienia `/etc/nginx` ani `sites-enabled`, nie wykonuje globalnego reload.
Nie usuwa pakietów/usług. Wykrywa nowe listenery 80/443 obu rodzin po instalacji. Przy błędzie podaje
etap apt i kod/timeout bez wypisywania wyjścia pakietów. Operator sprawdza
`dpkg --audit`, `/var/log/dpkg.log` i `/var/log/apt/term.log`; częściowe pakiety
naprawia świadomie, zachowując osłony. Narzędzie nie zatrzymuje obcych usług.

Istniejący pakiet Nginx jest używany wyłącznie odczytowo, jeśli zależności już są.
Jego konfiguracja i cykl życia należą do administratora. Jeśli brakuje zależności,
operator uzupełnia je osobno — narzędzie odmawia apt mogącego restartować obcą usługę.
Przy nowej instalacji obca jednostka/maska lub dowolna istniejąca policy-rc.d
blokuje bootstrap. Nie nadpisujemy polityki administratora ani nie zatrzymujemy
jego listenerów; konflikt portu blokuje przygotowanie/aktywację.

### Znane wersje własnych artefaktów

`bootstrap` obsługuje dokładne bajty znanej policy v1/v2 i recovery unitu v1/v2.
Poprzednia policy wymaga również zaufanego markera bootstrapu. Sama nazwa pliku
lub komentarz nie stanowi własności. Nieznana treść/wersja/właściciel/uprawnienia
blokują migrację; cudza policy nadal nie jest nadpisywana.

Przejście znanej wersji do bieżącej: niezmienny prywatny backup → fsync rekordu
`*-migration.json` → atomowa wymiana pliku z jawnym trybem → fsync → usunięcie
rekordu. Po przerwaniu ponów `bootstrap` zgodną wersją narzędzia. Akceptuje tylko
zapisane bajty starej lub docelowej wersji; obca zmiana blokuje ponowienie.
Backupy pozostają w `/var/backups/work-tracker/ingress`. Migracja idzie do przodu;
nie usuwa pakietów ani nie przywraca globalnego domyślnego Nginx. Znany starszy
recovery unit jest nadal rozpoznawany przez verify. Zwykły updater nie przeprowadza
tej migracji automatycznie; jawne `bootstrap` może zaktualizować własne artefakty.

[Debian Policy](https://www.debian.org/doc/debian-policy/ch-opersys.html#running-init-scripts)
wymaga respektowania invoke-rc.d przez skrypty pakietów. Maska systemd jest
dodatkowym trwałym zabezpieczeniem. Rzeczywiste zachowanie pakietów i needrestart
w konkretnej dystrybucji wymaga testu 9C; lokalny mock apt nie jest takim dowodem.

## 3. Przygotowanie istniejącej instalacji

Wykonaj standardowy update do kodu 9B2. Updater nie instaluje Nginx i nie przełącza
sieci; odmawia nowego update, gdy dziennik wskazuje niedokończony cutover.
Zachowaj konsolę/SSH, kopię SQLite w standardowej procedurze oraz poprzedni LAN
origin. Nie pokazuj env/tokenów w terminalu, zgłoszeniu ani PR.

```bash
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py bootstrap
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py configure
# Alternatywa dla configure: prywatny JSON root:root 0600 w katalogu 0700.
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py prepare --config /etc/work-tracker/ingress-input/provision.json
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py plan
```

Nie wykonuj obu wariantów tworzenia wejścia bez potrzeby. Przygotowanie nie zajmuje
portu HA. Wymagany działający LAN backend z `/api/health`, dotychczasowym portem HA
i bindem `0.0.0.0` lub konkretnym IP LAN. Backend port musi być całkowicie wolny,
także na innym IPv4/IPv6. Backend musi mieć trwały autostart (`enabled`, nie tylko runtime).
Unit musi pochodzić ze standardowej ścieżki systemowej;
wszystkie efektywne drop-iny muszą znajdować się w jego katalogu `.service.d`.
Niestandardowy/vendor/runtime layout blokuje migrację, wymaga osobnego planu.
Bazowy unit musi być zgodny z dostarczonym plikiem. Preflight sprawdza również
efektywne ExecStart (dokładny Uvicorn, zmienne APP_HOST/PORT, jeden worker,
no-proxy-headers), użytkownika/grupę, WorkingDirectory, EnvironmentFile, brak
innych poleceń start/stop i inline env, oraz wymagane właściwości ochrony systemd.
Przed pierwszym cutoverem obsługiwane są tylko dodatkowe resource drop-iny
Nice/CPUWeight/IOWeight/MemoryHigh/MemoryMax/TasksMax; inne wymagają osobnego planu.
Nie przeładowuj i nie edytuj równolegle konfiguracji poza narzędziem. Po potwierdzeniu
okna preflight i wszystkie snapshoty są zbierane ponownie.
Zachowane są niepowiązane ustawienia i drop-iny; nie nadpisujemy zmian administratora.

Operator potwierdza faktyczny transport peer HA i connectora, uwzględniając SNAT.
Samo IP skonfigurowane w HA nie wystarcza. Wspólny SNAT wyklucza aktywację.
Przygotuj skuteczny firewall IPv4 **i IPv6**, także dla loopback backendu;
nie trzeba globalnie wyłączać IPv6. Profil obecnie słucha na konkretnym RFC1918 IPv4.
Brak listenera IPv6 nie dowodzi izolacji. Instalator nie zmienia firewalla i nie
potrafi potwierdzić jego skuteczności za administratora.

Zbierz rzeczywiste negatywne próby z trzeciego hosta i spoza LAN w obu rodzinach,
z kontrolą reguł/liczników, bez utożsamiania chwilowego connection refused z ochroną.
Wypełnij [raport 9B1](../../deploy/ingress/activation-evidence.json.example) poza Git:
wszystkie bramki, dokładna generacja, świadomy timestamp sprzed najwyżej 24 h,
prywatny plik dowodów 0600. Nigdy nie przepisuj flag z lokalnych testów.
Brak dowodu lub false blokuje przełączenie **przed restartem aplikacji**.

## 4. Świadoma aktywacja

```bash
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py plan --evidence /etc/work-tracker/ingress-input/evidence.json
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py activate --evidence /etc/work-tracker/ingress-input/evidence.json
```

Wymagany terminal i wpisanie `AKCEPTUJĘ PRZERWĘ HA`. Uzgodnij okno bez wejść,
wyjść i korekt: restart backendu zwalnia dotychczasowy port HA przed startem proxy.
To nie jest atomowe przełączenie. **Webhook w tej przerwie może zaginąć; HA nie
musi go ponowić.** Nie zmieniamy URL ani automatyzacji HA i nie dodajemy kolejki.
Nie uruchamiamy drugiego backendu/workerów do tej samej DB.

Narzędzie przygotowuje i uruchamia ordered recovery oneshot przed transakcją,
wykonuje kopię env, unitu i drop-inów, następnie fsync dziennika `cutover.json`.
Ustawia APP_HOST=127.0.0.1, nowy port, INGRESS_PROFILE=split, dokładny HTTPS
origin, Secure=true, pusty CORS i HSTS=0. Własny `99-work-tracker-ingress.conf`
wymusza jeden worker Uvicorn i `--no-proxy-headers`. Token i DATABASE_URL nie zmieniają się.
Sprawdza runtime env, PID/socket/health i uruchamia **istniejący** mechanizm aktywacji
9B1: generacja, TLS, wszystkie bramki, wolne porty, backup, journal, dedykowany unit,
PID/listenery, master root, worker www-data i TLS CA/SNI. Nic publicznego nie powstaje.

Po lokalnych probes z rzeczywistego hosta HA wykonaj oba żądania na dotychczasowym URL:

```bash
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -X POST -H 'Content-Type: application/json' --data '{}' \
  'http://<WORK_TRACKER_LAN_IP>:<HA_PORT>/api/webhook/home-assistant'
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -X POST -H 'Content-Type: application/json' --data '{}' \
  'http://<WORK_TRACKER_LAN_IP>:<HA_PORT>/api/webhook/home-assistant/correction'
```

Bez tokenu/cookie i bez poprawnego payloadu. Obie odpowiedzi mają być **422**:
obecny backend waliduje body przed kontrolą tokenu. 403 oznacza m.in. zły source/ACL,
502 brak backendu. 422 dowodzi dojścia do walidacji, **nie** jest pełnym testem tokenu.
Dobry/zły/brak tokenu i poprawne zdarzenia są testowane wyłącznie w izolowanej bazie;
na produkcji obserwuj kolejne legalne zdarzenie i korektę, nie generuj testowych.
Po kontroli wpisz `HA 422 OK`. Odmowa/EOF/błąd powoduje pełny rollback.
Dopiero po tej kontroli powstaje link autostartu i trwały stan `active`.
Powtórne `activate` dla już aktywnej transakcji tylko weryfikuje stan, bez restartu.

## 5. Weryfikacja i reboot

```bash
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py verify
sudo systemctl status work-tracker work-tracker-ingress work-tracker-ingress-recovery
sudo ss -ltnp
```

HA: to samo prywatne IP/port i dwa endpointy. Browser: osobny konkretny bind TLS,
tylko connector. Backend: jeden socket 127.0.0.1. Brak nowych wildcard/IPv6
listenerów i domyślnego Nginx. Dla istniejącego obcego Nginx sprawdź jego wcześniejsze
listenery osobno; narzędzie ich nie wyłącza. Po restarcie systemu powtórz probes
z HA i testy negatywne. Połączenie z connectora sprawdza CA/SAN, nigdy noTLSVerify.
Browser UI do 9D może być niedostępne; lokalny handshake nie oznacza działania Access.

Po udanym cutoverze `work-tracker.service.wants/work-tracker-ingress.service`
przyciąga proxy przy starcie backendu. Ingress Requires/After backend i PartOf
propagują stop/restart; Restart=on-failure ponawia awarię proxy. Zwykły updater
zatrzymuje backend przed migracją i uruchamia go ponownie, co przyciąga ingress.
Nie zmienia profilu, env, generacji, firewalli ani tras. W aktywnym splicie
nie zastępuje bazowego unitu. Przed merge sprawdza nową gałąź origin/main:
unit musi pozostać identyczny z zainstalowanym i z snapshotem LAN, manifest
z poprzednim manifestem, a wzorce ingressu z bieżącymi. Również uszkodzony backup
lub niedokończona migracja artefaktów blokują update przed zatrzymaniem aplikacji.
Po pobraniu kodu powtarza kontrolę przed zależnościami/migracją; po restarcie
sprawdza split/backend/health/proxy. Dotychczasowy rollback updatera pozostaje,
łącznie z SQLite backupem i odtworzeniem tylko po rozpoczęciu migracji.

Zmiana kontraktu unitu/manifestu/wzorców wymaga jawnego okna: rollback-lan →
zwykły update → bootstrap/prepare według potrzeb → nowe evidence → activate.
Nie rebasujemy archiwalnego snapshotu w tle i nie przełączamy automatycznie na LAN.
To świadoma blokada kompatybilności, a nie obietnica obsługi dowolnego przyszłego unitu.

Recovery oneshot z
RemainAfterExit=yes wykonuje się przed aplikacją przy boot, bez oczekiwania na
start usług za nim. Ingress ma ConditionPathExists na dziennik aktywacji 9B1:
cofnięta transakcja nie uruchomi proxy nawet przy wcześniej zakolejkowanym starcie.
Nieaktywna generacja nie ma jednostki ani linku autostartu.
[Semantyka PartOf](https://www.freedesktop.org/software/systemd/man/systemd.unit.html#PartOf=).

## 6. Awaria i pełny rollback do LAN

```bash
# Niedokończona transakcja po SIGKILL/awarii; aktywna pozostaje bez zmian:
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py recover
# Jawne cofnięcie również poprawnie aktywnego split:
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py rollback-lan
```

SIGTERM/Ctrl-C/błąd wywołują kompensację. SIGKILL/zasilanie pozostawiają trwały
journal. Ponowienie narzędzia lub ordered recovery przy reboot odtwarza LAN.
Boot recovery przywraca pliki przed startem backendu; pozostawia `lan-restored`
do późniejszego `recover`, które sprawdza `/api/health` i zamyka dziennik.
Commit `active` przetrwa reboot bez cofnięcia. Recovery najpierw bezpiecznie
sprawdza format/fazę/właściciela/uprawnienia journala. Dla active brak, zły SHA
lub uszkodzone permissions archiwum wypisują **ALARM: automatyczny rollback NIEDOSTĘPNY**
do stderr/journald, ale zwracają sukces bez restartu i bez blokowania Requires
backendu. Tak samo działa ręczne recover. Nie oznacza to sprawnego rollbacku:
verify oraz rollback-lan nadal odmawiają przy takim backupie, zanim zatrzymają
usługę lub zmienią pliki. Odtwórz zaufany oryginalny backup/rozstrzygnij na konsoli;
nie poprawiaj SHA na ślepo ani nie usuwaj journala. Dla niedokończonej transakcji
brak poprawnego backupu pozostaje błędem recovery i blokuje niebezpieczne odtwarzanie. Nie ma automatycznego cofania już
zatwierdzonego split z powodu awarii browsera/certyfikatu.

Rollback usuwa własny link autostartu, zatrzymuje własny ingress, usuwa jego unit,
zatrzymuje backend, odtwarza dokładny poprzedni env (także grupę/tryb), usuwa
wyłącznie własny drop-in, reloaduje systemd i restartuje poprzedni LAN Uvicorn.
Bazowy unit i obce drop-iny pozostają identyczne z zapisanymi kopiami.
Sprawdza PID, poprzedni listener i `/api/health`. Błąd zostawia dziennik do ponowienia.
Zmieniony przez administratora env/unit/drop-in/link jest blokadą: zachowaj pliki,
porównaj prywatny backup i rozstrzygnij konflikt na konsoli. Narzędzie nie zgaduje,
które późniejsze zmiany administratora można zniszczyć. Zgodny updater zachowuje unit i tę zdolność rollbacku.
Niezgodny przyszły release zostaje odrzucony przed merge; zastosuj wyżej opisany
jawny powrót do LAN przed aktualizacją.

Nie potrzeba oryginalnego wejścia JSON ani działającego/ważnego TLS do cofnięcia.
Backupy 0700/0600 są w `/var/backups/work-tracker/ingress`, generacje i dzienniki
w `/etc/work-tracker/ingress`. Pozostają do audytu. Pakiety, maska, policy-rc.d
oraz bezczynny recovery oneshot pozostają zainstalowane; nie dotykamy obcych usług.
**Nie odtwarzamy SQLite, nie czyścimy sesji, użytkowników, korekt, eventów ani limitera.**
Firewall LAN operator przywraca osobno, jeśli wcześniejsze restrykcje blokują LAN UI.

Wyłączenie samej publicznej trasy jest inną operacją: operator wyłącza tylko
route browsera WT we współdzielonym Cloudflare; pozostawia split/backend/Nginx HA.
Nie używa `rollback-lan` ani `nginx_provision.py rollback` do awarii Access.
Szczegóły w [runbooku publicznego rolloutu](phase9-ingress.md#7-natychmiastowe-wyłączenie-i-rollback).

## 7. Wymiana certyfikatów

Nie edytuj kompletnej generacji. Przed upływem 7 dni ważności zaplanuj okno:
`rollback-lan` → popraw ścieżki TLS/CA w prywatnym JSON → `prepare --config ...`
→ nowy raport evidence dla nowej generacji → `plan` → `activate` → `verify`.
Zachowaj wszystkie inne pola JSON, kopię poprzednich wejść i poprzedni LAN env.
Kopie TLS i generacje pozostają do audytu. To świadomie konserwatywna wymiana
z dwoma krótkimi oknami zmiany listenera HA, bez hot reload i bez utraty konfiguracji.
W razie problemu pozostaje działający LAN. Brak ACME/zewnętrznej CA automatycznie;
CA na connectorach aktualizuje operator osobno, bez publikacji trasy w 9B2.

### EnvironmentFile i uprawnienia

Deploy czyta pojedyncze KEY=value bez wykonywania shella. Obsługuje zwykłe,
single/double quoted wartości i reguły backslash dla pojedynczej linii zgodnie z
[EnvironmentFile](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#EnvironmentFile=).
Token po odczycie ma tę samą wartość co runtime; oryginalne bajty env pozostają
w backupie. Nie wyświetlamy wartości. Duplikaty, multiline/konkatenacja i
niedomknięte quotes są bezpiecznie odrzucane jako nieobsługiwany kontrakt deploy.
Nowe katalogi systemd .wants i .service.d otrzymują jawnie 0755 także przy umask 077;
obce istniejące katalogi nie są chmodowane. Prywatne backupy pozostają 0700/0600.

## 8. Walidacja i bramka 9C

Lokalny zestaw używa prawdziwego OpenSSL/Nginx/Uvicorn i tymczasowego SQLite.
Sockety są na loopback, apt i systemd symulowane. Osobny proces naprawdę otrzymuje
SIGKILL; reboot jest symulacją kolejności recovery, nie prawdziwym restartem hosta.
Komendy i wyniki lokalne: README i opis PR. Lokalny PASS nie ustawia flag evidence.

Przed rzeczywistą aktywacją operator wykonuje na odizolowanym Debianie/Ubuntu:

- [ ] Świeży LAN i HTTPS od końca do końca, realne apt/postinst/needrestart,
      brak domyślnego Nginx po instalacji, awarii apt, upgrade i reboot.
- [ ] Istniejący obcy Nginx/policy: odmowa lub odczyt bez zmian cudzych usług/plików.
- [ ] Rzeczywisty systemd: root master/www-data worker, uprawnienia, sandbox,
      stop/restart updatera, boot aktywnego split i nieaktywnego przygotowania.
- [ ] SIGKILL/zasilanie na każdym etapie, restart backendu/proxy, konflikt IPv4/IPv6,
      pełny i powtarzalny rollback z niedostępnym certyfikatem i zachowaną DB.
- [ ] Potwierdzone HA/connector peery bez wspólnego SNAT, ACL i skuteczny firewall
      obu rodzin; negatywne próby z LAN/Internet oraz brak alternatywnego originu.
- [ ] Dotychczasowe HA URL, token auth i korekty na izolowanej bazie; na produkcji
      probes bez poprawnych payloadów i obserwacja wyłącznie legalnego ruchu.
- [ ] CA/SAN z connectora, cookies/sesje/CSRF, zachowanie historii limitera,
      brak publicznej trasy, niezależność HA przy awarii browsera.
- [ ] Pakiet backendu i runtime service user X2 Phase 8B2.

9D osobno: Access/IdP/JWT/Edge, DNS/tunnel, dwie prawdziwe sieci i spoofing,
publiczna odmowa HA, HSTS i wyłączenie wyłącznie trasy browsera.

Aktualna walidacja lokalna po audycie:

| Zestaw | Wynik |
|---|---|
| Backend, pełny pytest | **PASS: 751**, 27 istniejących ostrzeżeń Alembic |
| Deploy/ingress, pełny zestaw poniżej | **PASS: 225** |
| 9B1 provisioning / 9B2 cutover | **PASS: 55 / 48** |
| Regresje audytu / walidatory Host | **PASS: 42 / 59** |
| Rzeczywisty update.sh + LAN rollback na lokalnym Git/proxy/SQLite | **PASS: 6**, systemd/build/package adapters symulowane |
| Privacy / proxy security / ingress config / cloudflared config | **PASS: 3 / 8 / 3 / 1** |
| JWTValidator cloudflared | **PASS: 6 scenariuszy**, lokalny JWKS TLS, bez tunelu |
| Frontend npm test i npm run build | **PASS** |
| config_update / update_rollback / application_permissions shell | **PASS** |
| bash syntax, py_compile, diff-check, Alembic heads | **PASS**, head nadal 20261025_08 |
| backend_package_install_test.sh | **BLOCKED**, brak ensurepip/python3-venv |
| service_user_runtime_test.sh | **SKIP / NOT RUN**, brak roota, exit **77**, nie PASS |
| Rzeczywiste apt/systemd/root-worker/reboot/firewall/probes | **NOT RUN / REQUIRES 9C** |
| Access/IdP/Edge i publiczny rollout | **NOT RUN / 9D** |

Z root repo (binarki mogą być wypakowane poza checkoutem):

```bash
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> WORK_TRACKER_CLOUDFLARED_BIN=<CLOUDFLARED_BIN> \
  backend/.venv/bin/pytest deploy/tests/nginx_provision_test.py \
  deploy/tests/ingress_cutover_test.py deploy/tests/ingress_audit_test.py \
  deploy/tests/host_validation_test.py deploy/tests/split_update_test.py \
  deploy/tests/infrastructure_privacy_test.py deploy/tests/ingress_security_test.py \
  deploy/tests/ingress_config_test.py deploy/tests/cloudflared_config_test.py -q
```

Walidatory Host wykonują właściwy kod na kontrolowanych obserwacjach systemd i
plikach proc. HTTP status fragmentation korzysta z prawdziwego TCP. Test updatera
uruchamia staged update.sh, realne lokalne Git fetch/FF/reset, SQLite backup,
Uvicorn/Nginx i tokenowe webhooki/korekty na tymczasowej DB. Uprawnienia systemowe,
systemd, instalacja zależności, build oraz wywołania Alembic w tym teście mają adaptery;
pełne migracje są osobno w backend pytest. Nie są to testy produkcyjnego boot.
Migracje artefaktów sprawdzają ponowienie po błędzie przed/po atomowej wymianie;
cutover nadal sprawdza rzeczywiste SIGTERM/SIGKILL osobnego procesu.
Nie ma końcowych FAIL; brak możliwości wykonania testu nie oznacza PASS.

## 9. Rozstrzygnięcie audytu

| Punkt | Status i rozwiązanie |
|---|---|
| P1 active recovery a archiwum | **FIXED**: ścisły journal przed decyzją, alarm utraty rollbacku, sukces aktywnego boot/manual recover bez stopu; rollback nadal wymaga backupu |
| P1 finalny snapshot | **FIXED**: wszystkie wyniki drugiego planu przypisane; ponowna kontrola recovery i ochrona zmian po snapshot |
| P2 znane wersje własnych artefaktów | **FIXED**: dokładne v1/v2, prywatny backup i dziennik, atomowe ponowienie; obce/nieznane pliki odrzucane |
| P2 ExecStart/properties | **FIXED**: ścisły kontrakt pliku i obserwowanej konfiguracji, resource overrides tylko z jawnej listy; odmowa utraty flag |
| P2 updater w split | **FIXED**: zgodny update zachowuje unit/archiwum/generację, niezgodny blokowany przed merge; test success, build/migration/restart failure i LAN rollback |
| Pokrycie Host | **FIXED**: pozytywne/negatywne testy wszystkich wskazanych metod; obserwacje systemd/proc są kontrolowane, a nie dowodem realnego systemd |
| HTTP / umask / EnvironmentFile | **FIXED**: pełna ograniczona linia statusu, nowe katalogi 0755, dekodowanie quotes/escaping bez shella i ujawniania wartości |
| Diagnostyka apt / SKIP | **FIXED**: etap i kod/timeout, operator naprawia dpkg; brak roota zwraca 77 |
| Privacy / docs | **FIXED**: IPv4/IPv6 oraz hostname w URL/polach, prywatna denylista, skan bez wartości; LAN/HTTPS oraz lokalne/realne dowody rozdzielone |
| Rzeczywiste apt/systemd/root/boot i sieć | **DEFERRED do 9C**, środowisko bez roota i docelowej infrastruktury; wymagana checklista powyżej |

Skan prywatności jest heurystyką: nie wykrywa dowolnej obfuskacji ani każdego
hostname poza rozpoznanymi kontekstami. Zachowaj prywatną denylistę i ręczny
przegląd przed publikacją. Nie przebudowywano historii Git ani danych domenowych.
Nie poprawiano nieistniejącego curl health-checku updatera; nowy hook sprawdza
wymaganą gotowość split po restarcie. Brak deploymentu, merge i automatycznego review.

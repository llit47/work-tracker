# Phase 9B2 — instalacja i prywatny cutover

Narzędzia są dla Debiana/Ubuntu z systemd, Pythonem >= 3.10 i OpenSSL 3.
Nie konfigurują Cloudflare, DNS, routera, firewalla, hypervisora ani HA.
HTTPS oznacza przygotowanie prywatnego ingressu, **nie działającą publiczną stronę**.
Publiczny rollout jest osobną Phase 9D po pełnych bramkach ingressu i Phase 8.
[Zamknięcie 9C z 27.10.2026 i produkcyjny runbook 8B2 → 9B1 → 9B2](phase9c-production-upgrade.md)
obejmują zweryfikowany LAN/first-upgrade, nie odbiór całego splitu.
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
w konkretnej dystrybucji nadal wymaga rzeczywistego testu; zakres LAN 9C
i lokalny mock apt nie są takim dowodem.

## 3. Przygotowanie istniejącej instalacji

### Pierwsza aktualizacja z 5d974b1

To **procedura wspomagana przez operatora**, dla dokładnego, czystego wydania
`5d974b100a92f0b7aed6aefa9939f0d5383c9cac` w standardowym layoutcie LAN.
Stary updater kopiuje siebie do `/tmp` i po merge nadal wykonuje stary kod.
Zwykłe uruchomienie go ani skopiowanie samego nowego update.sh **nie chroni
pierwszej migracji**. Nie uruchamiaj ich do pierwszego przejścia.

Dla produkcji startującej z 8B2 najpierw wykonaj
[kontrolowane przejście do dokładnego 9B1](phase9c-production-upgrade.md#4-kontrolowane-przejście-8b2--9b1).
Stary updater pobrałby obecny main, więc nie służy również do wybrania tego baseline.

Wymagane: root, działający standardowy LAN/backend, Debian/Ubuntu, Python,
Git/SQLite/OpenSSL/iproute2 z dotychczasowej instalacji, dostęp do origin oraz
niezależna konsola. Sprawdź aktualny Alembic head i stan usługi bez wypisywania
sekretów. Wybierz pełny **przejrzany SHA** zawierający ten bootstrap. Produkcyjnie
użyj go po zatwierdzeniu celu i ocenie wyników/ograniczeń 9C; poniższe polecenia nie są zgodą na
deployment. Jedynym dopuszczonym refem jest `refs/heads/main`, także w CLI. Pełny SHA
pozostaje obowiązkowy, musi być niezależnie przejrzany i równy aktualnemu
tipowi main. Po przesunięciu main trzeba przejrzeć nowy SHA, także gdy zmieniono
tylko dokumentację; nie podmieniaj refa. Nigdy nie resetuj
produkcyjnego Git do oczekiwanego SHA w celu obejścia odmowy innego baseline.

Zarezerwuj wyłączne okno: **żaden inny administrator, cron ani automat nie może
uruchamiać starego updatera**. Ten stary skrypt nie respektuje nowej blokady.
Bootstrap odmawia przy wykryciu już uruchomionego updatera/staged procesu;
wszystkie nowe bootstrapy/updatery współdzielą flock. Nie jest to ochrona przed
innym rootem świadomie ignorującym okno i uruchamiającym zachowaną starą kopię.
Nie zabijaj znalezionego zadania na ślepo — ustal, czy już migrowało dane.

Na prywatnej konsoli roota, po zastąpieniu placeholdera zatwierdzonym SHA:

```bash
sudo -i
set -euo pipefail
umask 077
cd /opt/work-tracker
test "$(git rev-parse HEAD)" = 5d974b100a92f0b7aed6aefa9939f0d5383c9cac
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git remote get-url origin)" = https://github.com/llit47/work-tracker.git
TARGET='<REVIEWED_40_HEX_SHA>'
UPGRADE_REF=refs/heads/main
git fetch --no-tags origin "$UPGRADE_REF"
test "$(git rev-parse FETCH_HEAD)" = "$TARGET"
install -d -m 0700 /root/work-tracker-first-upgrade
git show "${TARGET}:deploy/first_upgrade.py" > /root/work-tracker-first-upgrade/first_upgrade.py
chmod 0700 /root/work-tracker-first-upgrade/first_upgrade.py
/usr/bin/python3 -I /root/work-tracker-first-upgrade/first_upgrade.py start \
  --expected-sha "$TARGET" --ref "$UPGRADE_REF" --readiness-timeout 60
```

Przerwij po każdym nieudanym preflight; nie kontynuuj kolejnych poleceń po błędzie.
Nie używaj `curl | bash`, obcego repozytorium ani nieznanego commita.
Bootstrap ponownie sprawdza origin, stary HEAD, czystość, brak ukrytych zmian
indexu, przekierowań/hooków Git, ref/SHA/ancestry oraz własne bajty z Git.
Konfiguracja/unit/kod wymagają właściciela root; katalog danych wymaga użytkownika
usługi, trybu 0700/0750, a SQLite i istniejące WAL/SHM/journal jego UID/GID oraz
0600/0640. Narzędzie nie poprawia tych praw przez chmod/chown ani nie usuwa sidecarów.
Przed guardem/FF/Alembic walidowany jest manifest i walidatory z przypiętego
commita: brakujące klucze i nieprawidłowe wartości blokują bez zapisu env, stopu
usługi i aktywnego update-state. Komunikat podaje tylko nazwy kluczy. Uzupełnij
konfigurację świadomie przed ponowieniem; nie ma późniejszych interaktywnych
dopisków env w first-upgrade. Nowe wymagane klucze działają, jeśli są już poprawnie
skonfigurowane. Zwykłe późniejsze update zachowuje dotychczasowy workflow pytań.
Niestandardowy unit/drop-in, obcy checker/guard, inna wersja lub istniejący
update-state blokują operację. Nie przejmuje plików administratora.

Kolejność pod nieprzerwaną blokadą FD 9:

1. Walidacja starego hosta i przypiętego celu, prywatny katalog
   `update-first-<ID>` z online.db, env.before, unit.before i sprawdzonym kodem.
2. Fsync journala `preparing`; atomowy checker root 0644 w
   `/etc/work-tracker/update-safety.py`, niezależny od checkoutu i archiwum.
   Własny `90-work-tracker-update-safety.conf` wskazuje właśnie ten checker,
   uruchamiany przez Python `-I` bez wpływu PYTHONPATH/PYTHONHOME z env aplikacji.
3. Daemon-reload i kontrola efektywnego ExecStartPre/DropInPaths/NeedDaemonReload.
   Brak potwierdzenia blokuje Git FF, zależności i Alembic.
4. Staging przypiętego nowego updatera, handoff.json, Git FF do tego samego SHA
   (zastępuje stare wejście update.sh), następnie `exec` z tym samym PID i FD 9.
   Nowy updater sprawdza własne bajty, helpery, journal i odziedziczoną blokadę.
   Nie zwalniamy blokady ani nie przepisujemy właściciela podczas handoffu.
5. Istniejący updater 9B2 wykonuje build, potwierdzony stop, checkpoint i trwałe
   `migrating` **przed** Alembic, potem `verifying`, restart i ograniczone
   oczekiwanie LAN HTTP. Na koniec ścisły unit/env/runtime/socket/health i SQLite
   quick_check jako użytkownik usługi. Dopiero sukces zamyka aktywny journal.

Nie zmienia profilu LAN, URL/tokenów HA ani konfiguracji Nginx/Cloudflare.
Odbiór HA trwa podczas przygotowania/build; przerwa zaczyna się przy stopie
backendu przed migracją. HA nie musi ponowić żądania w tym oknie. Sukces nie
upoważnia do splitu: jego aktywacja nadal ma osobne bramki i okno.

Przerwanie przed zainstalowaniem guarda nie zmienia kodu ani schematu. Od guarda
każdy niezatwierdzony boot/start jest blokowany, także po SIGKILL, utracie
zasilania, w trakcie FF i handoffu. Guard działa, nawet gdy checkout nie ma helpera.
Przed Alembic obsłużony błąd może przywrócić stary kod z **bieżącą DB**; trwały
guard poza checkoutem pozostaje. Po próbie Alembic nie cofamy kodu ani bazy.
Ponowienie po częściowym bootstrapie bezpiecznie odmawia zamiast zgadywać fazę.

Inspekcja na prywatnej konsoli:

```bash
sudo systemctl status work-tracker
sudo journalctl -u work-tracker -n 100
sudo python3 /etc/work-tracker/update-safety.py allow-start
sudo python3 -m json.tool /etc/work-tracker/update-state.json
```

Journal nie zawiera tokenu ani danych zdarzeń. Nie publikuj go wraz z lokalnymi
ścieżkami/logami. Zachowaj także katalog update-first i snapshoty. Dalsze kroki:
[odzyskiwanie bez restore SQLite](#odzyskiwanie-po-nieudanej-aktualizacji).
Nie usuwaj stanu/guarda, aby wymusić start. Po pre-Alembic cancel lub przerwanym
przygotowaniu kolejne first-upgrade może odmówić przez pozostawione artefakty;
operator najpierw sprawdza fazę, brak migracji i spójność kodu/deps. Usunięcie
wyłącznie własnych osłon lub dopuszczenie startu jest osobną świadomą naprawą.
Znana historyczna v1 trwałego checkera jest rozpoznawana po katalogowanym SHA-256,
a bieżąca v2 po dokładnych bajtach zaufanego kodu. Nieznana treść/właściciel/tryb
i symlinki są odrzucane. Kolejna zmiana checkera wymaga jawnego podniesienia
CURRENT_CHECKER_VERSION i katalogowania poprzednich zaufanych hashy wraz z testami
zgodności formatu stanu; nie ma automatycznego uznawania zmodyfikowanego pliku. Procedura wymiany v1 → v2 poniżej; nazwa nie dowodzi własności.

Rzeczywisty restart systemd oraz odmowę przez guard/systemd po SIGKILL w
`preparing` potwierdzono w 9C na Debian LXC, 27.10.2026. Reboot, fizyczna utrata
zasilania i SIGKILL w `migrating`/`verifying` nie są zaliczone. Lokalne testy
5d974b1/Git/SQLite/Alembic/SIGKILL mają symulowane systemctl/uprawnienia;
[osobny dowód operatora](phase9c-production-upgrade.md#1-potwierdzone-wyniki-i-granice-weryfikacji)
nie rozszerza ich na nieprzetestowane scenariusze.

### Kolejne aktualizacje i przygotowanie cutoveru

Po udanym first-upgrade wykonuj zwykły update.sh. Nie instaluje Nginx i nie
przełącza sieci; niedokończony update lub cutover blokuje kolejne operacje.
Zachowaj konsolę/SSH, backupy oraz poprzedni LAN origin.
Nie pokazuj env/tokenów w terminalu, zgłoszeniu ani PR.

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
Nice/CPUWeight/IOWeight/MemoryHigh/MemoryMax/TasksMax oraz dokładny własny guard aktualizacji opisany poniżej; inne wymagają osobnego planu.
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
wykonuje osobne oczekiwanie readiness, a następnie jednorazową pełną kontrolę
split/backend/health/proxy/TLS/ACL/uprawnień. `Type=simple` i `is-active` nie
są dowodem gotowości. Backend może ruszyć później niż restart systemctl, a proxy
jeszcze później. Ponawiany jest wyłącznie brak startującego procesu/socketu,
connection refused/timeout oraz dynamiczny obraz procesu przed exec: niepełne
argv/env (także przejściowo niedostępne proc), brak workera i jego zmiana UID
z root na www-data. Dwa kolejne zgodne obrazy PID/backendu/mastera/workera
poprzedzają finalną walidację. Trwale niezgodne argv/env/liczba workerów lub
worker pozostający root kończą się odmową do deadline; timeout odróżnia
niezgodny runtime od niedostępnych usług. Obcy listener, zły bind/master UID
i worker z obcym UID są natychmiastowym błędem. Pliki, TLS/CA/ACL i polityki
nie podlegają retry. Końcowe ścisłe sprawdzenie wykonuje się raz po readiness.

Zwykły update LAN również wymaga HTTP 200 z `GET /api/health` przed commit,
z aktywną usługą aplikacji sprawdzaną przed próbami i po odpowiedzi. Używa
skonfigurowanego portu i lokalnego prywatnego IP, bez DNS/publicznej trasy;
wildcardy `0.0.0.0` i `::` są sondowane przez odpowiednie loopback.
Po readiness jednorazowo sprawdza proces, właściciela/bind listenera i zachowanie
DB/tokena. Błąd po Alembic zachowuje bieżącą SQLite i stan manual recovery.

Wcześniej, przed guardem, Git FF, zależnościami, stopem i Alembic, read-only
preflight sprawdza istniejący `APP_HOST`/port wspólnym walidatorem readiness.
Przyjmuje numeryczne IPv4/IPv6: wildcard, loopback i lokalne prywatne adresy,
bez DNS/HTTP. `localhost`, inne nazwy oraz publiczne/nielokalne IP są odrzucane
z nazwą klucza i instrukcją dla operatora. Odmowa zachowuje usługę, env, SQLite
i Git HEAD, bez transakcji manual recovery. Operator musi jawnie zmienić bind
po analizie dostępności LAN/HA; nie ma automatycznej zamiany na loopback.
First-upgrade wywołuje ten sam walidator przed guardem; kontrola split bez zmian.

W LAN, first-upgrade i split connection reset/refused, broken pipe, timeout
oraz EOF przed pełną linią statusu są transportowym brakiem gotowości w tym
samym deadline. Pełna błędna linia statusu HTTP to odmowa, podobnie jak obcy
listener, błędny bind, TLS, UID i statyczna polityka; nie są ponawiane.

Domyślny budżet readiness wynosi **60 s**, zakres **5–300 s**:

```bash
sudo /opt/work-tracker/update.sh --readiness-timeout 120
# Sama kontrola po restarcie (bez aktualizacji):
sudo python3 /opt/work-tracker/deploy/ingress_cutover.py update-verify --readiness-timeout 120
```

Budżet ogranicza również operacje I/O wewnątrz readiness; build/migracje oraz
statyczna i końcowa walidacja mają własne limity operacji i są poza tym budżetem.
Trwała niedostępność kończy się kontrolowanym błędem i stanem odzyskiwania poniżej.
Domyślne 60 s pozostaje bez zmian; na wolniejszym hoście po sprawdzeniu 9C
praktyczną wartością początkową jest 120 s, nadal maksymalnie 300 s.

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

### Migracja trwałego checkera v1 → v2

Dotyczy instalacji z `/etc/work-tracker/update-safety.py` i istniejącym trwałym
guardem bootstrapu; guard wskazujący checker w checkoutcie pozostaje obsługiwany.
Nowy walidator akceptuje tylko dokładnie katalogowaną historyczną v1 z d3cb1a6
lub bieżącą v2, z root ownership/0644 i bez symlinków. Modyfikacja/comment/nazwa
nie dowodzą własności. Format update-state v1 oraz stary guard pozostają zgodne.

Stary staged updater i jego staged checker nadal wykonują starą wersję po FF.
Dlatego **nie wymieniamy checkera w trakcie normalnego update**. Nowe walidatory
akceptują v1 do końca starej transakcji. Po sukcesie i zakończeniu procesu, jako
osobną operację administracyjną uruchom nowy kod:

```bash
sudo python3 /opt/work-tracker/deploy/update_safety.py migrate-checker
```

Komenda bierze tę samą blokadę co updater/cutover. Przy aktywnej/pending/manual
transakcji update odmawia. v2 → v2 jest no-op. Instalacje tworzone bieżącym
bootstrapem od razu mają v2. Nie ma restartu usługi, Alembic ani zapisu do DB.

Kolejność v1 → v2: prywatny backup `update-checker-<ID>/checker-before.py`,
fsync standardowego update-state `preparing` (rozumie go także stary v1),
fsync `checker-migration.json`, atomowa wymiana/0644/fsync, zapis zakończenia,
usunięcie update-state, a na końcu checker-migration.json. Guard nigdy nie jest
usuwany. Wzorzec prywatnego backupu + write-ahead journal + atomowe ponowienie
jest taki jak dla ingress_artifacts; implementacja pozostaje stdlib, ponieważ
checker musi działać przez Python -I poza checkoutem.

Przed wymianą start blokuje stary checker na podstawie preparing. Po wymianie
v2 blokuje start także przez checker-migration.json, również gdy update-state
został już usunięty. SIGKILL/zasilanie nie otwierają okna startu. Powtórz tę samą
komendę zgodnym kodem: sprawdza katalogowane bajty, backup/hash, docelową wersję
oraz dokładne powiązanie journala ze swoją transakcją. Nie adoptuje normalnego
update i nie nadpisuje obcych zmian. Backup pozostaje po zakończeniu.

Jeśli przerwanie nastąpiło przed zapisaniem journala migracji, a preparing już
istnieje, brakuje dowodu do automatycznego wznowienia: **bezpieczna odmowa** i
analiza na konsoli. Nie usuwaj stanu/guarda. Przy uszkodzonym backupie/journalu
lub innej wersji docelowej też odmawia. Przerwany normalny update napraw według
poniższej procedury, zanim uruchomisz migrację checkera. Realny restart/boot,
UID/sandbox migracji checkera i utrata zasilania nadal wymagają rzeczywistej
weryfikacji; zamknięcie LAN 9C ich nie zalicza, lokalny SIGKILL nie jest rebootem.

### Ręczna retencja backupów

Nie ma automatycznej kasacji. Procedura tylko po **jawnej zgodzie operatora na
konkretne katalogi**; ta dokumentacja nie upoważnia narzędzia do cleanupu.

1. Sprawdź prywatnie update-state, checker-migration, cutover/active i journals
   migracji artefaktów oraz katalogi update-*, update-first-*, update-checker-*
   i ingress. Przy nieznanym/orphan/niedokończonym stanie wstrzymaj cleanup.
2. Zachowaj wszystko wskazane przez aktywne/niepewne transakcje i wymagane do
   ręcznej naprawy, także backup LAN używany przez zatwierdzony aktywny split.
3. Zawsze zachowaj najnowszy **zweryfikowany** punkt odzyskania aplikacji,
   checkera i ingressu. Sam wiek/nazwa/phase=commit nie dowodzą zbędności.
4. Dopiero po porównaniu referencji i potwierdzeniu niezależnej kopii operator
   wybiera konkretną starszą, zakończoną, nieużywaną kopię i zatwierdza jej
   usunięcie. Żadnych wildcard rm, automatycznego limitu dni ani operacji na
   `/var/lib/work-tracker` lub bieżących SQLite/WAL/SHM.

Updater nie wykonuje destrukcyjnego restore. **SQLite może normalnie wykonywać
checkpointy, tworzyć, skracać lub usuwać WAL/SHM**. Nie gwarantujemy ich inode,
ciągłej obecności ani czasu życia. Gwarancją są zachowane zatwierdzone dane,
nieodtwarzanie starszego snapshotu i brak ręcznego kasowania sidecarów przez updater.

### Odzyskiwanie po nieudanej aktualizacji

Backup nie dowodzi braku późniejszych zapisów. SQLite online backup daje spójny
snapshot, ale działająca usługa nadal może przyjmować zdarzenia, korekty i loginy
([SQLite backup API](https://www.sqlite.org/backup.html)). Updater nie porównuje
samych liczników eventów ani hashy, aby zgadywać bezpieczeństwo restore.
**Automatyczny restore SQLite/WAL/SHM i downgrade Alembic są zabronione.**

Każde uruchomienie tworzy prywatny katalog
`/var/backups/work-tracker/update-<UTC>.<ID>/`: `online.db` przed Git/build,
`pre-migration.db` po potwierdzeniu ActiveState=inactive i MainPID=0 backendu,
`unit.before`, kopię checkera i `transaction.json` z identyfikatorami commitów.
Checkpoint po zatrzymaniu obejmuje zapisy przyjęte po online backupie; również
on może być starszy od zapisów przyjętych po restarcie kandydata. Żaden nie jest
samodzielnym upoważnieniem do zastąpienia bieżącej DB. Bieżąca baza jest nadrzędna.

Przed zmianą kodu zapisywany jest fsync stanu
`/etc/work-tracker/update-state.json` (root 0600) i dokładny własny drop-in
`90-work-tracker-update-safety.conf` (0644, nowy katalog 0755). Guard nie zmienia
bazowego unitu ani snapshotu LAN; cutover rozpoznaje tylko jego znane bajty.
Nieznana treść, właściciel lub uprawnienia blokują operację zamiast przejmowania
pliku administratora. ExecStartPre checkera działa z poświadczeniami roota,
pozostawiając sandbox usługi ([prefiks systemd !](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#ExecStart=)).
Checker nie odczytuje sekretów i nie zapisuje DB. Zmiany stanu są atomowe/fsync.

- `preparing`: Alembic nie został wywołany. Przy obsłużonym błędzie możliwe
  cofnięcie kodu/deps/frontend/unit i wznowienie starej usługi z **bieżącą DB**.
  Guard jest zdejmowany dopiero po udanej kompensacji. Staged checker pozwala
  wrócić także do wydania bez checkera, usuwając tylko dokładnie własny drop-in.
- `migrating`: zapisane **przed** wywołaniem Alembic. Nawet przerwana migracja
  może pozostawić DDL lub zmiany danych. Nie uruchamiamy starego kodu.
- `verifying`: tylko żywy właściciel update (PID + czas startu procesu + boot ID)
  upoważnia start kandydata. Zakończenie procesu/SIGKILL/reboot blokuje kolejny
  start. Sukces końcowej weryfikacji usuwa aktywny stan, zachowując archiwum.
- `manual`: obsłużony błąd od próby migracji, nawet no-op, restartu lub weryfikacji.
  Zatrzymujemy backend i zależne proxy; zostają kod/deps/unit kandydata, bieżąca
  DB i oba backupy. Nowy update, start, activate i rollback-lan są blokowane.
- Uszkodzony/nieznany stan aktualizacji: odmowa startu/aktualizacji. Nie usuwaj
  pliku w celu obejścia blokady. Przerwane preparing też wymaga analizy: śmierć
  updatera nie dowodzi spójności kodu i zależności.

To celowy kompromis: nawet gdy Alembic prawdopodobnie nic nie zmienił,
automatyczna ścieżka nie dowodzi kompatybilności poprzedniego kodu. Zachowuje
zapisy i blokuje niepewny start kosztem dłuższej przerwy. Odbiór HA jest
niedostępny od stopu backendu do gotowości usług, a przy stanie manual do naprawy.
HA nie musi ponowić webhooku; zaplanuj okno bez wejść/wyjść i korekt. Nie zmieniamy
URL/token auth/konfiguracji HA i nie dodajemy kolejki.

Procedura operatora na prywatnej konsoli (bez publikowania env/DB/logów):

1. Potwierdź zatrzymanie backendu i zależnego ingressu. Jeżeli stop nie został
   potwierdzony, ustal proces i zapisy przed kopiowaniem; nie uruchamiaj drugiego
   backendu na tej samej DB. Zachowaj stan update, cutover i konfigurację.
2. Zabezpiecz **bieżące** DB/WAL/SHM razem przy potwierdzonym braku procesów,
   istniejące backupy i kod/deps obu wersji. Wykonaj dodatkowy spójny SQLite
   backup bieżącego stanu do nowego prywatnego pliku; nie zastępuj żadnej kopii.
   Waliduj integralność na kopiach, nie eksperymentuj na oryginale.
3. Na odizolowanych klonach sprawdź `alembic_version`, rzeczywisty schemat i
   wszystkie efekty DDL/danych przerwanej migracji. Sam numer revision ani
   checksum nie dowodzi kompletności. Sprawdź kompatybilność kodu kandydata;
   preferuj naprawę do przodu. Stary kod wolno wybrać wyłącznie po analizie jego
   zgodności z **bieżącą** bazą; żadnego ślepego downgrade/reset/restore.
4. Zachowaj wszystkie raw events, korekty, konta, sesje i próby logowania.
   Ewentualna naprawa wymaga osobnego, przejrzanego planu i walidacji na klonie,
   nigdy zamiany bazy starszą kopią kosztem późniejszych zapisów.
5. Dopiero po sprawdzeniu kodu/deps/unitu i zgodności zachowanej DB administrator
   może prywatnie zarchiwizować aktywny `update-state.json` poza jego ścieżką
   (pozostawiając własny guard) i świadomie dopuścić restart. Narzędzie nie ma
   automatycznego polecenia force/unblock. Najpierw napraw stan, potem zdejmuj
   blokadę. Stan cutover pozostaje; nie edytuj jego SHA ani bramek.
6. Zweryfikuj readiness/pełne `verify` split lub LAN health, sockety, profil,
   tokenowe ścieżki HA i stan danych. Na produkcji tylko probes bez poprawnych
   zdarzeń i obserwacja legalnego ruchu. Przy problemie zatrzymaj ponownie,
   zachowaj wszystkie nowe zapisy i wróć do analizy, nie do starego restore.

Guard/systemd w `preparing` i recovery z zachowaniem guarda potwierdzono w 9C.
Nie potwierdzono rebootu, utraty zasilania ani recovery po częściowej migracji;
[wynik i ograniczenia](phase9c-production-upgrade.md#1-potwierdzone-wyniki-i-granice-weryfikacji).
Pierwsze przejście jest obsługiwane wyłącznie przez przypięty bootstrap opisany
powyżej; stary staged updater nadal nie otrzymuje ochrony wstecz. Nie uruchamiaj
go do pierwszej aktualizacji. Stabilny checker nie zależy od archiwum i pozostaje
po pre-Alembic rollbacku do starego checkoutu.

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
brak poprawnego backupu pozostaje błędem recovery i blokuje niebezpieczne odtwarzanie.
Uszkodzony/nieznany **journal cutover** blokuje recovery i Requires backendu
(fail-closed); brak archiwum aktywnego splitu to inny przypadek. Niedokończony
rollback pozostawia fazę do ponowienia po usunięciu przyczyny. Awaria aplikacji
lub updatera nie oznacza uszkodzenia jej danych i nie upoważnia do restore DB.
Stan update wymagający naprawy ma odrębny guard opisany wyżej.
Nie ma automatycznego cofania już
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

**9C DONE w zakresie LAN/first-upgrade i recovery `preparing`, operator-confirmed
27.10.2026.** Udane 8B2 → 9B1 → 9B2 potwierdziło aplikację, SQLite/head, health,
login i restart systemd. Osobny SIGKILL po trwałym `preparing` potwierdził
journal, odmowę guarda/systemd i manual recovery do 9B1 bez restore starszej DB.
[Pełne wyniki i runbook produkcyjny](phase9c-production-upgrade.md) rozdzielają
te dowody od niezaliczonych scenariuszy. Poniższa pełna checklista nadal
określa pozostałe bramki przed aktywacją splitu/publicznym rolloutem.

Lokalny zestaw używa prawdziwego OpenSSL/Nginx/Uvicorn i tymczasowego SQLite.
Sockety są na loopback, apt i systemd symulowane. Osobny proces naprawdę otrzymuje
SIGKILL; reboot jest symulacją kolejności recovery, nie prawdziwym restartem hosta.
Komendy i wyniki lokalne: README i opis PR. Lokalny PASS nie ustawia flag evidence.

Potwierdzono na rzeczywistym Debian LXC:

- [x] LAN 8B2 → 9B1 → first-upgrade 9B2, poprawny backend/frontend/Alembic,
      `20261025_08 (head)`, SQLite quick_check ok, health 200, login i restart systemd.
- [x] Trwały guard/systemd odmawiają startu z niedokończonym `preparing`;
      rzeczywisty SIGKILL bootstrapu i manual recovery na bieżącej DB z pozostawieniem guarda.
- [ ] SIGKILL w `migrating`/`verifying`, fizyczna utrata zasilania, pełne recovery
      po częściowej migracji Alembic — **NOT TESTED**.

Przed rzeczywistą aktywacją splitu pozostają testy na odizolowanym Debianie/Ubuntu:

- [ ] Świeży LAN i HTTPS od końca do końca, realne apt/postinst/needrestart,
      brak domyślnego Nginx po instalacji, awarii apt, upgrade i reboot.
- [ ] Istniejący obcy Nginx/policy: odmowa lub odczyt bez zmian cudzych usług/plików.
- [ ] Rzeczywisty systemd: root master/www-data worker, uprawnienia, sandbox,
      stop/restart updatera, boot aktywnego split i nieaktywnego przygotowania.
- [ ] Pozostałe awarie first-upgrade/handoff i szczegółowe kontrole ciągłości PID/blokady,
      SIGKILL migracji i reboot bez startu niekompatybilnego kodu. Udany first-upgrade
      i SIGKILL `preparing` opisane wyżej nie dowodzą pozostałych faz.
- [ ] Zimny/opóźniony start: PID przed exec, argv/env, socket przed workerem,
      UID workera i dwa stabilne obrazy, potem pojedyncza finalna walidacja.
- [ ] Rzeczywisty update d3cb1a6/v1 → nowe wydanie/v2, osobna migracja checkera,
      SIGKILL/zasilanie przed/po wymianie i odzyskanie bez zdjęcia guarda.
- [ ] Pozostałe kontrole guarda/boot: SIGKILL w migrating/verifying, utrata zasilania
      także w preparing, brak startu niekompatybilnego kodu i wznowienie dopiero po naprawie;
      zgodny update → rollback-lan w split. Runtime sandbox poza zakresem dowodu LAN.
- [ ] Opóźniony start backendu/proxy i timeout; przyjęte przed/po restarcie raw
      events, korekty, sesje/limiter zachowane przy błędzie. Ręczna naprawa
      częściowo wykonanej migracji na izolowanym klonie bez restore starszej DB.
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
| Deploy/ingress, pełny zestaw poniżej | **PASS: 374** |
| 9B1 provisioning / 9B2 cutover | **PASS: 55 / 48** |
| Regresje audytu / walidatory Host | **PASS: 42 / 62** |
| Rzeczywisty update.sh, awarie/SQLite i LAN rollback na lokalnym Git/proxy | **PASS: 12**, systemd/build/package adapters symulowane, rzeczywisty Alembic/SQLite/SIGKILL |
| Osobna readiness / trwały guard i runner SKIP | **PASS: 10 / 25** |
| First-upgrade: rzeczywisty 5d974b1, Git/SQLite WAL/Alembic/Uvicorn | **PASS: 27**, delayed LAN, walidacja ref/SHA/hosta, obce pliki, blokady, przerwanie bootstrap/handoff/migracji, boot refusal; historyczny updater jako kontrola błędu |
| Stabilizacja pre-exec/transportu / migracja rzeczywistego checkera v1 → v2 | **PASS: 18 / 17**, kontrolowane proc; SIGKILL i atomowa wymiana rzeczywiste |
| Zwykły update LAN / HTTP readiness | **PASS: 32**, readonly bind preflight, parser HTTP, bounded retry reset/broken-pipe/EOF, aktywność usługi, odmowa obcego listenera i rzeczywisty updater z zachowaniem SQLite |
| Integralność historycznych fixture’ów i jawny wymóg historii bazowej | **PASS: 11**, SHA-256 każdego pliku, zgodność CHECKER_V1_SHA, odmowa zmienionego fixture’u i brakującej historii |
| Osobna próba shallow/squash bez commita PR | **PASS: 54, FAIL: 0, SKIP: 0**, 17 checker bez historii; 37 fixture/first-upgrade po lokalnym dostarczeniu BASE + rodzica, dodatkowo 2 kontrole przygotowania PASS |
| Privacy / proxy security / ingress config / cloudflared config | **PASS: 3 / 8 / 3 / 1** |
| JWTValidator cloudflared | **PASS: 6 scenariuszy**, lokalny JWKS TLS, bez tunelu |
| Frontend npm test i npm run build | **PASS** |
| config_update / update_rollback / application_permissions shell | **PASS** |
| bash syntax, py_compile, diff-check, Alembic heads | **PASS**, head nadal 20261025_08 |
| backend_package_install_test.sh | **PASS**, ponownie wykonany w bieżącym środowisku |
| service_user_runtime_test.sh | **SKIP / NOT RUN**, brak roota, exit **77**, nie PASS |
| Zbiorczy runner shell | **PASS=4 SKIP=1 FAIL=0**, exit 0; runtime bez roota nadal SKIP, nie PASS |
| Pozostałe rzeczywiste apt/Nginx/root-worker/boot splitu/firewall/probes | **NOT VERIFIED**, poza zamkniętym zakresem LAN 9C |
| Access/IdP/Edge i publiczny rollout | **NOT RUN / 9D** |

Z root repo (binarki mogą być wypakowane poza checkoutem):

```bash
WORK_TRACKER_NGINX_BIN=<NGINX_BIN> WORK_TRACKER_CLOUDFLARED_BIN=<CLOUDFLARED_BIN> \
  backend/.venv/bin/pytest deploy/tests/nginx_provision_test.py \
  deploy/tests/ingress_cutover_test.py deploy/tests/ingress_audit_test.py \
  deploy/tests/host_validation_test.py deploy/tests/split_update_test.py \
  deploy/tests/update_readiness_test.py deploy/tests/update_safety_test.py \
  deploy/tests/first_upgrade_test.py deploy/tests/checker_migration_test.py \
  deploy/tests/historical_fixtures_test.py \
  deploy/tests/startup_stabilization_test.py \
  deploy/tests/lan_readiness_test.py \
  deploy/tests/infrastructure_privacy_test.py deploy/tests/ingress_security_test.py \
  deploy/tests/ingress_config_test.py deploy/tests/cloudflared_config_test.py -q
```

Walidatory Host wykonują właściwy kod na kontrolowanych obserwacjach systemd i
plikach proc. HTTP status fragmentation korzysta z prawdziwego TCP. Test updatera
uruchamia staged update.sh, realne lokalne Git fetch/FF/reset, SQLite backup,
Uvicorn/Nginx i tokenowe webhooki/korekty na tymczasowej DB. Uprawnienia systemowe,
systemd, instalacja zależności i build mają adaptery. Alembic uruchamia rzeczywiste
migracje SQLite, w tym testową częściową DDL; pełne migracje są też w backend pytest. Nie są to testy produkcyjnego boot.
Migracje artefaktów sprawdzają ponowienie po błędzie przed/po atomowej wymianie;
cutover nadal sprawdza rzeczywiste SIGTERM/SIGKILL osobnego procesu. Updater dodatkowo otrzymuje SIGKILL po zaakceptowanym zapisie HA; bieżące wiersze pozostają, a kolejny start jest blokowany.
Brak końcowych błędów wykonanych testów. SKIP runtime nie jest zaliczany jako PASS.

Historyczny checker v1 i jego rzeczywisty updater/helpery są fixture’ami z
przypiętymi SHA-256, w tym zgodnością checkera z `CHECKER_V1_SHA`; testy nie
odczytują już commita PR z Git. First-upgrade nadal świadomie testuje rzeczywiste
drzewo `5d974b1` i jego rodzica. Wymaga pełnej historii albo jawnego przygotowania
płytkiego checkoutu testowego; brak tych obiektów to FAIL, nigdy SKIP.
[Proweniencja, komendy przygotowania historii i test shallow/squash](../../deploy/tests/fixtures/README.md)
opisują również izolowany `history_portability_check.py`. Nie uruchamiać tych
testów ani przygotowania historii w checkoutcie produkcyjnym.

## 9. Rozstrzygnięcie audytu

| Punkt | Status i rozwiązanie |
|---|---|
| P1 active recovery a archiwum | **FIXED**: ścisły journal przed decyzją, alarm utraty rollbacku, sukces aktywnego boot/manual recover bez stopu; rollback nadal wymaga backupu |
| P1 finalny snapshot | **FIXED**: wszystkie wyniki drugiego planu przypisane; ponowna kontrola recovery i ochrona zmian po snapshot |
| P2 znane wersje własnych artefaktów | **FIXED**: dokładne v1/v2, prywatny backup i dziennik, atomowe ponowienie; obce/nieznane pliki odrzucane |
| P2 ExecStart/properties | **FIXED**: ścisły kontrakt pliku i obserwowanej konfiguracji, resource overrides tylko z jawnej listy; odmowa utraty flag |
| P2 updater w split | **FIXED**: zgodny update zachowuje unit/archiwum/generację, niezgodny blokowany przed merge; sukces i błąd przed Alembic umożliwiają LAN rollback, błąd od Alembic wymaga najpierw naprawy |
| P1 pierwszy upgrade | **FIXED w jawnym kontrakcie LAN**: przypięty bootstrap, niezależny trwały checker, guard efektywny przed FF/Alembic, exec z tym samym PID/FD; brak automatycznego wznowienia niepewnej fazy; 9C potwierdza LAN/systemd i SIGKILL preparing, pozostałe fazy bez potwierdzenia |
| P1 stabilizacja procesów | **FIXED**: dynamiczne argv/env/workery mają wspólny deadline i dwie zgodne obserwacje, obce sockety/UID odrzucane od razu, finalna walidacja raz |
| P1/P2 checker v1 → v2 | **FIXED**: dokładny katalog historyczny, zgodność przez FF, oddzielna migracja po starym updaterze, root backup/journal/fsync i odzyskanie bez zdjęcia guarda |
| P2 target config / P3 ref | **FIXED**: readonly target-manifest przed transakcją, nazwy kluczy bez wartości, brak first-upgrade prompts, wyłącznie main i jawny pełny SHA |
| P1 update readiness | **FIXED**: oddzielny limit 60 s (5–300), retry tylko Pending, pełna walidacja raz po gotowości; restart w teście wraca natychmiast |
| P1 SQLite rollback | **FIXED**: nigdy automatycznie nie odtwarza DB/sidecarów; przed Alembic cofa kod z bieżącą DB, od próby Alembic zatrzymuje i blokuje start do ręcznej naprawy; testy prawdziwych zapisów i częściowej migracji |
| Kontrakt unitu / runner SKIP | **FIXED**: test czyta dostarczony unit i porównuje walidator; zbiorczy runner rozróżnia 0/77/inne pod set -e |
| Pokrycie Host | **FIXED**: pozytywne/negatywne testy wszystkich wskazanych metod; obserwacje systemd/proc są kontrolowane, a nie dowodem realnego systemd |
| HTTP / umask / EnvironmentFile | **FIXED**: pełna ograniczona linia statusu, nowe katalogi 0755, dekodowanie quotes/escaping bez shella i ujawniania wartości |
| Diagnostyka apt / SKIP | **FIXED**: etap i kod/timeout, operator naprawia dpkg; brak roota zwraca 77 |
| Privacy / docs | **FIXED**: IPv4/IPv6 oraz hostname w URL/polach, prywatna denylista, skan bez wartości; LAN/HTTPS oraz lokalne/realne dowody rozdzielone |
| Rzeczywista infrastruktura | **PARTIAL**: 9C LAN/first-upgrade/systemd i recovery preparing potwierdzone 27.10.2026; apt/Nginx/root-worker/boot splitu/sieć i pozostałe awarie nadal wymagają checklisty powyżej |

Skan prywatności jest heurystyką: nie wykrywa dowolnej obfuskacji ani każdego
hostname poza rozpoznanymi kontekstami. Zachowaj prywatną denylistę i ręczny
przegląd przed publikacją. Nie przebudowywano historii Git ani danych domenowych.
Nie poprawiano nieistniejącego curl health-checku updatera; nowy hook sprawdza
wymaganą gotowość split po restarcie. Brak deploymentu, merge i automatycznego review.

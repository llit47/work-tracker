# Phase 9C — wyniki i produkcyjna aktualizacja LAN do 9B2

## 1. Potwierdzone wyniki i granice weryfikacji

**Status: DONE — operator-confirmed 27.10.2026, zakres LAN/first-upgrade oraz
manual recovery po SIGKILL w `preparing`.** Testy wykonano na rzeczywistych
kontenerach Debian LXC w Proxmox VE. Poniżej zapis wyników przekazanych przez
operatora; poleceń tego runbooka nie wykonywano na produkcji podczas jego pisania.
Identyfikatory kontenerów, adresy, konfiguracja i surowe logi pozostają prywatne.

| Próba | Potwierdzony wynik |
|---|---|
| Udany upgrade | 8B2 `7f47c1e` → 9B1 `5d974b1` → first-upgrade 9B2 `d70365093503289296d627f3c07abeabbaf71e9f` |
| Kontrole aplikacji | Backend, frontend i Alembic poprawne; `PRAGMA quick_check = ok`, `20261025_08 (head)`, `/api/health` HTTP 200, login działa, restart systemd poprawny |
| Trwały guard | Zainstalowany ExecStartPre; niedokończone `preparing` blokuje checker i systemd; pełną sprawność testowego środowiska przywrócono ze snapshotu `post-9b2-success` |
| Drugi, niezależny test | Start z 8B2, kontrolowane przejście do 9B1, rzeczywisty first-upgrade i SIGKILL bootstrapu po trwałym zapisie `preparing` |
| Stan po SIGKILL | Git nadal `5d974b1`, `update-state.json` przetrwał, checker exit 1, systemd odmówił startu |
| Diagnoza przed recovery | Backend zatrzymany, `MainPID=0`, SQLite `integrity_check=ok`, revision `20261025_08`, backup bieżącej SQLite, Git czysty, `pip check` poprawny, `NeedDaemonReload=no` |
| Manual recovery | Po analizie zgodności `preparing` zarchiwizowano aktywny journal, pozostawiono guard; odzyskano 9B1 bez restore starszej DB; health 200, usługa active, SQLite ok |

Stan na 27.10.2026: **produkcja nadal LAN-only na 8B2 `7f47c1e`; nie była
aktualizowana**. Jedno środowisko testowe działa na 9B2 `d703650`, drugie na
odzyskanym 9B1. Odtworzenie snapshotu dotyczyło izolowanego testu, nie procedury
cofania produkcyjnej bazy.

**NOT TESTED — nie zaliczono:**

- SIGKILL w fazach `migrating` i `verifying`;
- fizycznej utraty zasilania;
- pełnej procedury recovery po częściowo wykonanej migracji Alembic.

Trwałość journala po SIGKILL procesu nie dowodzi odporności na utratę zasilania.
`preparing` nie dowodzi recovery po Alembic. Nie potwierdzono tu rebootu hosta,
pełnego split/cutoveru, apt/Nginx/root-worker, migracji checkera v1 → v2, izolacji
sieci ani X2 Phase 8B2. Istniejące bramki TLS/CA, HA/connector peers, IPv4/IPv6,
firewalla i negatywnych probes pozostają w [checkliście ingressu](phase9b2-cutover.md#8-walidacja-i-bramka-9c).
Phase 9D i publiczny dostęp nadal wymagają odrębnego odbioru i zatwierdzenia.

## 2. Warunki dopuszczenia produkcyjnej aktualizacji

Ten runbook dotyczy wyłącznie istniejącego LAN w standardowym layoutcie:
`/opt/work-tracker`, `/etc/work-tracker/work-tracker.env`,
`/var/lib/work-tracker/work_tracker.db`, `work-tracker.service`.
Nie uruchamiaj instalatora, provisioningu Nginx, cutoveru ani Cloudflare.

Wymagane narzędzia pochodzą z istniejącej instalacji: Python >= 3.10, Git,
SQLite CLI, OpenSSL, iproute2, curl, runuser, systemd, backendowy virtualenv
oraz Node/npm do builda 9B2. Runbook nie wymaga apt ani zmian usług
infrastruktury; zależności aplikacji instaluje istniejący updater.

Przed rozpoczęciem operator potwierdza:

- wyłączne okno serwisowe, niezależną konsolę i brak innego updatera/automatu;
  stary updater nie respektuje nowej blokady;
- okno bez wejść/wyjść i korekt HA: webhook wysłany podczas stopu może przepaść,
  bo ponowienie przez HA nie jest gwarantowane;
- czysty, root-owned checkout 8B2 o pełnym SHA z sekcji 3, poprawny origin,
  działający backend, login, frontend i SQLite; brak niedokończonych transakcji;
- dostępne snapshot/backup, miejsce na kopie DB, staging, zależności i build;
  snapshot obejmuje także zewnętrzne wobec Git dane i konfigurację aplikacji;
- wspierany numeryczny `APP_HOST` (lokalny prywatny IP, wildcard lub loopback
  z zachowaniem wymaganej dostępności HA), profil LAN, dotychczasowe URL/tokeny
  i poprawny manifest; `localhost` jako APP_HOST nie spełnia kontraktu 9B2;
- standardowy unit bez obcych drop-inów; prawa kodu/env root, katalogu danych
  work-tracker 0700/0750 oraz DB/istniejących sidecarów work-tracker 0600/0640.
  Odmowa preflight wymaga diagnozy, nie ślepego chmod/chown lub usuwania plików;
- świadomą akceptację pozostałych ryzyk z sekcji 1, możliwej dłuższej przerwy
  oraz ręcznego recovery. Udany test nie gwarantuje powodzenia na innym hoście.

**Kontrola SHA:** zweryfikowany kod 9B2 to
`d70365093503289296d627f3c07abeabbaf71e9f`. Bootstrap dopuszcza wyłącznie
`refs/heads/main` i wymaga, żeby jego aktualny tip był równy `--expected-sha`.
Nie wystarczy, że testowany commit jest przodkiem `main`. Po przesunięciu `main`
(także po scaleniu tej dokumentacji) operator musi przejrzeć nowy pełny SHA.
Jeżeli różnica względem testowanego 9B2 obejmuje tylko dokumentację, odnotuj to
w prywatnym protokole; wyniki 9C nadal opisują dokładnie testowany kod. Zmiany
wykonywalnego kodu/config/migracji wymagają oceny i walidacji ich wpływu przed
wyborem celu. Nie podmieniaj zdalnego refa i nie zmieniaj reguł bootstrapu.

## 3. Preflight, snapshot i niezależny backup

Polecenia wykonuje operator na prywatnej konsoli roota, blok po bloku, w jednej
sesji Bash. **Po każdym błędzie przerwij i przejdź do sekcji 6.** Nie wklejaj
kolejnych bloków bez sprawdzenia wyników. Nie włączaj `set -x`, nie wypisuj env,
haseł ani tokenów; nie publikuj surowych logów i kopii.

```bash
sudo -i
set -euo pipefail
umask 077
cd /opt/work-tracker
WT_8B2=7f47c1ef58081212ee3df94fb1c0e1ae7031167f
WT_9B1=5d974b100a92f0b7aed6aefa9939f0d5383c9cac
WT_VERIFIED_9B2=d70365093503289296d627f3c07abeabbaf71e9f
WT_TARGET='<REVIEWED_MAIN_40_HEX_SHA>'
WT_REF=refs/heads/main
WT_HEALTH_URL='http://<LOCAL_LAN_IP>:<APP_PORT>/api/health'
[[ "$WT_TARGET" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse HEAD)" = "$WT_8B2"
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git remote get-url origin)" = https://github.com/llit47/work-tracker.git
git fetch --no-tags origin "$WT_REF"
test "$(git rev-parse FETCH_HEAD)" = "$WT_TARGET"
git merge-base --is-ancestor "$WT_8B2" "$WT_9B1"
git merge-base --is-ancestor "$WT_9B1" "$WT_VERIFIED_9B2"
git merge-base --is-ancestor "$WT_VERIFIED_9B2" "$WT_TARGET"
git diff --name-only "$WT_VERIFIED_9B2" "$WT_TARGET"
test ! -e /etc/work-tracker/update-state.json
test ! -L /etc/work-tracker/update-state.json
test ! -e /etc/work-tracker/checker-migration.json
test ! -L /etc/work-tracker/checker-migration.json
test ! -e /etc/work-tracker/ingress/cutover.json
test ! -L /etc/work-tracker/ingress/cutover.json
systemctl is-active --quiet work-tracker.service
systemctl is-enabled --quiet work-tracker.service
test "$(systemctl show -p NeedDaemonReload --value work-tracker.service)" = no
systemctl show work-tracker.service -p FragmentPath -p DropInPaths -p ExecStartPre
backend/.venv/bin/pip check
test "$(runuser -u work-tracker -- sqlite3 -readonly \
  /var/lib/work-tracker/work_tracker.db 'PRAGMA quick_check;')" = ok
test "$(runuser -u work-tracker -- sqlite3 -readonly \
  /var/lib/work-tracker/work_tracker.db 'SELECT version_num FROM alembic_version;')" = 20261025_08
(
  cd backend
  runuser -u work-tracker -- env \
    DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db \
    .venv/bin/alembic current
)
```

Oczekiwane: `20261025_08 (head)`, standardowy FragmentPath
`/etc/systemd/system/work-tracker.service`, brak drop-inów/ExecStartPre na tym
baseline. Sprawdź także login i istniejący frontend w dotychczasowym LAN originie.
`WT_HEALTH_URL` uzupełnij prywatnym lokalnym adresem/portem obecnej aplikacji;
przy wildcard IPv4 można użyć loopback. Nie korzystaj z publicznego proxy.

```bash
wt_health() {
  test "$(curl --noproxy '*' --retry 20 --retry-connrefused --retry-delay 1 \
    --retry-max-time 60 --max-time 5 --fail --silent --show-error \
    --output /dev/null --write-out '%{http_code}' "$WT_HEALTH_URL")" = 200
}
wt_health
install -d -o root -g root -m 0700 /var/backups/work-tracker
WT_RUN_DIR="$(mktemp -d /var/backups/work-tracker/production-upgrade.XXXXXX)"
install -o root -g root -m 0600 /etc/work-tracker/work-tracker.env "$WT_RUN_DIR/env.before"
install -o root -g root -m 0600 /etc/systemd/system/work-tracker.service "$WT_RUN_DIR/unit.before"
```

Funkcja poniżej tworzy spójny backup żywej bazy jako użytkownik usługi, następnie
przenosi kopię do prywatnego katalogu roota. Usuwa wyłącznie własny plik tymczasowej
kopii, nigdy główną DB ani jej sidecary. Każda docelowa nazwa musi być nowa.

```bash
wt_backup() {
  local wt_destination="$1" wt_stage
  test ! -e "$wt_destination"
  wt_stage="$(mktemp -d /tmp/work-tracker-backup.XXXXXX)"
  chown work-tracker:work-tracker "$wt_stage"
  runuser -u work-tracker -- sqlite3 -readonly \
    /var/lib/work-tracker/work_tracker.db ".backup '$wt_stage/current.db'"
  install -o root -g root -m 0600 "$wt_stage/current.db" "$wt_destination"
  test "$(sqlite3 -readonly "$wt_destination" 'PRAGMA integrity_check;')" = ok
  rm -- "$wt_stage/current.db"
  rmdir -- "$wt_stage"
}
```

Zatrzymaj backend, potwierdź brak procesu i wykonaj niezależny backup:

```bash
systemctl stop work-tracker.service
test "$(systemctl show -p ActiveState --value work-tracker.service)" = inactive
test "$(systemctl show -p MainPID --value work-tracker.service)" = 0
wt_backup "$WT_RUN_DIR/current-8b2.db"
```

W tym miejscu, przy zatrzymanym backendzie, wykonaj snapshot kontenera przez
prywatną konsolę hypervisora. Potwierdź, że obejmuje kod, env i dane; jeżeli dane
są na mountcie wyłączonym ze snapshotu, zabezpiecz je osobno. Odnotuj identyfikator
poza repozytorium. Dopiero po potwierdzeniu zakresu i ukończenia kopii przejdź
do sekcji 4.

Nie traktuj snapshotu ani tego backupu jako zgody na późniejszy restore produkcji:
po wznowieniu aplikacji mogą istnieć nowe zdarzenia, korekty, sesje i loginy.

## 4. Kontrolowane przejście 8B2 → 9B1

**Nie uruchamiaj starego `update.sh`.** Pobiera najnowszy `main`, a nie wybrane
historyczne 9B1. Poniższy fast-forward przygotowuje dokładny baseline wspierany
przez first-upgrade. Dla tych dwóch SHA backend, frontend, historie Alembic,
unit i poniższe helpery/updater są identyczne; sprawdzamy to przed zmianą.
Nie ma potrzeby instalacji zależności, przebudowy ani wywołania migracji na tym
kroku. Backend pozostaje zatrzymany do kontroli gotowego 9B1.

```bash
test "$(git rev-parse HEAD)" = "$WT_8B2"
test -z "$(git status --porcelain --untracked-files=all)"
git diff --exit-code "$WT_8B2" "$WT_9B1" -- \
  backend frontend deploy/work-tracker.service deploy/common.sh \
  deploy/update_rollback.sh update.sh
git merge --ff-only "$WT_9B1"
test "$(git rev-parse HEAD)" = "$WT_9B1"
source deploy/common.sh
set_application_permissions /opt/work-tracker root work-tracker root
cmp /etc/systemd/system/work-tracker.service deploy/work-tracker.service
cmp /etc/work-tracker/work-tracker.env "$WT_RUN_DIR/env.before"
validate_required_config deploy/config.manifest /etc/work-tracker/work-tracker.env
backend/.venv/bin/pip check
systemctl daemon-reload
test "$(systemctl show -p NeedDaemonReload --value work-tracker.service)" = no
systemctl start work-tracker.service
wt_health
systemctl is-active --quiet work-tracker.service
test -z "$(git status --porcelain --untracked-files=all)"
test "$(runuser -u work-tracker -- sqlite3 -readonly \
  /var/lib/work-tracker/work_tracker.db 'PRAGMA quick_check;')" = ok
(
  cd backend
  runuser -u work-tracker -- env \
    DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db \
    .venv/bin/alembic current
)
wt_backup "$WT_RUN_DIR/current-9b1.db"
```

Oczekiwane: dokładny SHA 9B1, `20261025_08 (head)`, health 200, SQLite ok.
Sprawdź login i frontend. Zachowaj checkpoint 9B1 i prywatny protokół kontroli.
Przy niepowodzeniu nie uruchamiaj ani starego updatera, ani bootstrapu 9B2.

## 5. First-upgrade 9B1 → 9B2 i odbiór

Backend musi znów działać na dokładnym, czystym 9B1. Nie kopiuj samego nowego
`update.sh`: stara kopia staged w `/tmp` nie instaluje ochrony przed pierwszą
migracją. Pobierz **bootstrap z wybranego pełnego SHA**, poza checkout.
Szczegółowe warunki i kolejność gwarancji są w
[procedurze first-upgrade](phase9b2-cutover.md#pierwsza-aktualizacja-z-5d974b1).

```bash
test "$(git rev-parse HEAD)" = "$WT_9B1"
test -z "$(git status --porcelain --untracked-files=all)"
systemctl is-active --quiet work-tracker.service
git fetch --no-tags origin "$WT_REF"
test "$(git rev-parse FETCH_HEAD)" = "$WT_TARGET"
install -d -o root -g root -m 0700 /root/work-tracker-first-upgrade
git show "${WT_TARGET}:deploy/first_upgrade.py" > /root/work-tracker-first-upgrade/first_upgrade.py
chmod 0700 /root/work-tracker-first-upgrade/first_upgrade.py
cmp /root/work-tracker-first-upgrade/first_upgrade.py \
  <(git show "${WT_TARGET}:deploy/first_upgrade.py")
/usr/bin/python3 -I /root/work-tracker-first-upgrade/first_upgrade.py start \
  --expected-sha "$WT_TARGET" --ref "$WT_REF" --readiness-timeout 60
```

Bootstrap ponownie sprawdza SHA/ref i standardowy host. Przed FF/Alembic zapisuje
`preparing`, instaluje niezależny checker `/etc/work-tracker/update-safety.py`
i trwały drop-in `90-work-tracker-update-safety.conf`, wykonuje daemon-reload
i sprawdza efektywny ExecStartPre. Pod tą samą blokadą/PID przekazuje sterowanie
nowemu updaterowi. Te wydania 9B1/9B2 nie dodają migracji ponad `20261025_08`;
updater mimo to wywołuje Alembic, a błąd od tej próby (także no-op) wymaga
ręcznej naprawy. Updater aktualizuje zależności, buduje frontend, zatrzymuje backend,
potwierdza brak PID, tworzy `pre-migration.db`, zapisuje `migrating` przed Alembic,
następnie `verifying` przed restartem. Kończy transakcję po readiness, ścisłych
kontrolach i SQLite quick_check. Nie zmienia profilu LAN ani HA URL/tokenów.

**Tylko po exit 0 bootstrapu** wykonaj odbiór:

```bash
test "$(git rev-parse HEAD)" = "$WT_TARGET"
test -z "$(git status --porcelain --untracked-files=all)"
backend/.venv/bin/pip check
cmp /etc/work-tracker/work-tracker.env "$WT_RUN_DIR/env.before"
cmp /etc/systemd/system/work-tracker.service "$WT_RUN_DIR/unit.before"
cmp deploy/update_safety.py /etc/work-tracker/update-safety.py
systemctl show work-tracker.service -p ExecStartPre -p DropInPaths -p NeedDaemonReload
test "$(systemctl show -p NeedDaemonReload --value work-tracker.service)" = no
/usr/bin/python3 -I /etc/work-tracker/update-safety.py allow-start
test ! -e /etc/work-tracker/update-state.json
test ! -L /etc/work-tracker/update-state.json
test ! -e /etc/work-tracker/checker-migration.json
test ! -L /etc/work-tracker/checker-migration.json
test "$(runuser -u work-tracker -- sqlite3 -readonly \
  /var/lib/work-tracker/work_tracker.db 'PRAGMA quick_check;')" = ok
(
  cd backend
  runuser -u work-tracker -- env \
    DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db \
    .venv/bin/alembic current
)
systemctl restart work-tracker.service
wt_health
systemctl is-active --quiet work-tracker.service
systemctl is-enabled --quiet work-tracker.service
wt_backup "$WT_RUN_DIR/current-9b2.db"
```

Oczekiwane: `20261025_08 (head)`, checker exit 0, aktywny guard wskazujący
`!/usr/bin/python3 -I /etc/work-tracker/update-safety.py allow-start`, brak
aktywnej transakcji, health 200 również po dodatkowym restarcie i SQLite ok.
Sprawdź login, frontend i miesięczne dane bez tworzenia sztucznych zdarzeń.
Potwierdź zachowanie dotychczasowych URL/tokenów i obserwuj legalny ruch HA;
nie testuj ingestion poprawnymi sztucznymi payloadami na produkcyjnej DB.
Zachowaj prywatne backupy `update-first-*`, journal archiwalny `transaction.json`
(zakończony `commit`), snapshot i kopie z tego runbooka. Odnotuj SHA, wyniki
kontroli i koniec okna; można utworzyć kolejny snapshot po sukcesie.
Nie wykonuj testu awarii przez wpisanie fikcyjnego `preparing` na produkcji.

## 6. Postępowanie po błędzie

**Przerwij wdrożenie. Nie uruchamiaj starego updatera, nie wymuszaj checkoutu,
nie przywracaj starszej SQLite, nie kasuj sidecarów i nie obchodź guarda.**
Nie ponawiaj bootstrapu, dopóki nie ustalisz, czy powstał trwały stan. Zachowaj
niezależną konsolę i zatrzymaj ewentualny nadal działający updater w sposób
uzgodniony po ustaleniu jego fazy; nie zabijaj procesu na ślepo.

1. Odczytaj prywatnie `systemctl status work-tracker.service`,
   `journalctl -u work-tracker.service -n 100`, Git HEAD/status, aktywny
   `/etc/work-tracker/update-state.json` i wskazany `transaction.json`.
   Zanotuj fazę, kod/checker/unit oraz katalog `update-first-*`. Brak journala
   sam nie dowodzi spójności; przerwanie mogło nastąpić przed jego zapisem.
2. Potwierdź brak aktywnego updatera i zatrzymanie backendu; przy standardowym
   LAN użyj poniższych poleceń. Nie kopiuj surowych plików jako spójnego kompletu,
   dopóki nie wykluczysz innych procesów zapisujących tę samą DB.

   ```bash
   systemctl stop work-tracker.service
   test "$(systemctl show -p MainPID --value work-tracker.service)" = 0
   test "$(systemctl show -p ActiveState --value work-tracker.service)" = inactive
   ```

3. W nowym katalogu root 0700 zachowaj bieżącą DB wraz z istniejącymi WAL/SHM/journal,
   aktywny stan, env/unit/guard/checker, backupy oraz kod i zależności. Wykonaj
   dodatkowy spójny backup bieżącej DB (`wt_backup` z nową nazwą, jeżeli sesja
   przygotowania nadal istnieje). Na kopii sprawdź `PRAGMA integrity_check` i
   `alembic_version`; analizuj zgodność kodu, schematu i zależności. Nie opieraj
   decyzji tylko na numerze revision albo wyniku `ok`.
4. **Preflight bez zmian:** po udowodnieniu braku transakcji/migracji usuń przyczynę
   odmowy i ponownie oceń warunki. Błąd podczas ręcznego przejścia do 9B1 również
   wymaga sprawdzenia aktualnego HEAD, praw i runtime; nie ma wtedy nowego guarda.
   **Przerwane `preparing`:** jedyna przetestowana naprawa obejmowała Git nadal 9B1,
   czyste drzewo, poprawne deps/`pip check`, unit/`NeedDaemonReload=no`, niezmieniony
   zgodny schemat, brak Alembic i backup bieżącej DB. `preparing` po FF/build może
   mieć inny stan kodu/deps i wymaga osobnej analizy. Nie kopiuj decyzji z testu
   na podstawie samej nazwy fazy.
5. **`migrating`, `verifying`, `manual` lub stan nieznany:** zachowaj kod kandydata,
   wszystkie dane i blokadę. Alembic mógł się wykonać, także częściowo; preferuj
   analizę i naprawę do przodu na izolowanym klonie. To nieprzetestowane realnie
   scenariusze. Wymagają osobnego planu zgodności/danych przed restartem, zgodnie z
   [procedurą recovery](phase9b2-cutover.md#odzyskiwanie-po-nieudanej-aktualizacji).
6. Dopiero po udokumentowanej analizie zgodności kod/deps/unit/DB i sprawdzeniu
   naprawy administrator może zarchiwizować aktywny journal poza jego aktywną
   ścieżką. **Guard i niezależny checker pozostają zainstalowane.** To końcowy
   krok ręcznej naprawy, nie sposób wymuszenia startu. Brak podstaw do oceny
   oznacza pozostawienie usługi zatrzymanej i guarda w stanie fail-closed.
   Po dopuszczeniu startu powtórz health/systemd/SQLite/login i kontrolę danych;
   przy błędzie ponownie zatrzymaj usługę, zachowując nowe zapisy.

Snapshot/backup jest materiałem do odzyskiwania, nie poleceniem cofnięcia
produkcyjnej historii. Nigdy nie zastępuj bieżącej bazy starszą kopią kosztem
późniejszych raw events, korekt, użytkowników, sesji lub historii limitera.

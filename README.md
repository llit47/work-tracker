# Work Tracker

Prosta aplikacja działająca lokalnie w sieci LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy. Backend przyjmuje zabezpieczone webhooki Home Assistanta, zapisuje każde zdarzenie w SQLite i wylicza sesje pracy bez zmiany danych źródłowych. Frontend React pokazuje bieżący status pracy, miesięczne sesje i anomalie, korekty, wynagrodzenie oraz eksporty CSV/PDF.

Nie konfiguruje publicznego dostępu, domeny, proxy, tunelu ani HTTPS.

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
- originy CORS, jeśli frontend ma działać z innego originu.

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

Frontend można sprawdzić komendą:

```bash
cd frontend
npm test
npm run build
```

## Endpointy

- `POST /api/webhook/home-assistant` — przyjmuje JSON webhooka, zapisuje immutable raw event i zwraca jego `id`, status oraz dane prezentacyjne; wymaga nagłówka `X-Webhook-Token`.
- `POST /api/webhook/home-assistant/correction` — ustawia audytowalną korektę godziny konkretnego raw eventu na podstawie time-only inputu; wymaga nagłówka `X-Webhook-Token`.
- `GET /api/work-events?year=2026&month=9` — zwraca chronologicznie zdarzenia dla wskazanego miesiąca kalendarzowego w offsetcie przekazanym przez Home Assistanta.
- `GET /api/work-summary?year=2026&month=9` — wylicza sesje, dni, miesięczny czas pracy i anomalie na podstawie effective events oraz zwraca metadane audytowe korekt.
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
dodatkowy kontekst potrzebny przyszłej integracji interaktywnych powiadomień:

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

Pole `time` akceptuje `H:MM`, `HH:MM`, `H.MM` i `HH.MM` z zewnętrznym
whitespace. Backend wyznacza datę wyłącznie względem raw UTC instantu i IANA
timezone jego canonical location, uwzględnia dzień poprzedni/bieżący/następny,
oba `fold` DST oraz okno ±4 godzin. Brak lub błędna timezone, nonexistent lub
nierozstrzygalny ambiguous local time, błędny format i wyjście poza okno zwracają
HTTP 422 bez zmiany danych. Błędy integracyjne mają stabilny kod w
`detail.code`, m.in. `invalid_time`, `location_timezone_missing`,
`location_timezone_invalid`, `time_not_resolvable`, `time_out_of_range`,
`correction_conflict` i `raw_event_not_found`.

Ten etap nie dodaje actionable notifications, handlera
`mobile_app_notification_action`, kill switcha ani YAML Home Assistanta. Żadna
produkcyjna automatyzacja Home Assistanta nie jest przez niego zmieniana ani
aktywowana; to pozostaje zakresem Phase 7C.

Frontend pokazuje `missing_exit` jako „Trwająca zmiana” tylko wtedy, gdy jego wejście odpowiada tej samej chwili UTC co jednoznaczna sesja zwrócona przez dashboard. Pierwszy snapshot i późniejsze istotne zmiany dashboardu odświeżają aktualnie wybrane podsumowanie czasu i płac; sam upływ czasu bieżącej zmiany nie powoduje dodatkowych żądań miesięcznych.

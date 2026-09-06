# Work Tracker

Prosta aplikacja działająca lokalnie w sieci LAN do rejestrowania zdarzeń wejścia i wyjścia z pracy. Backend przyjmuje zabezpieczone webhooki Home Assistanta, zapisuje każde zdarzenie w SQLite i udostępnia API. Frontend React wyświetla zdarzenia bieżącego miesiąca.

Nie konfiguruje publicznego dostępu, domeny, proxy, tunelu ani HTTPS.

## Quick install

Na świeżym Debianie lub Ubuntu, w tym w kontenerze LXC z działającym systemd:

```bash
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
bash -n ../install.sh ../update.sh ../deploy/common.sh ../deploy/update_rollback.sh ../deploy/tests/config_update_test.sh ../deploy/tests/update_rollback_test.sh
../deploy/tests/config_update_test.sh
../deploy/tests/update_rollback_test.sh
```

Frontend można sprawdzić komendą:

```bash
cd frontend
npm run build
```

## Endpointy

- `POST /api/webhook/home-assistant` — przyjmuje JSON webhooka; wymaga nagłówka `X-Webhook-Token`.
- `GET /api/work-events?year=2026&month=9` — zwraca chronologicznie zdarzenia dla wskazanego miesiąca kalendarzowego w offsetcie przekazanym przez Home Assistanta.
- `GET /api/health` — prosty status API.

Przykładowy webhook:

```bash
curl -X POST http://127.0.0.1:8000/api/webhook/home-assistant \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Token: TWOJ_SEKRET' \
  -d '{"event":"entry","location":"gabinet_zabki","timestamp":"2026-09-06T08:14:32+02:00","source":"home_assistant"}'
```

Format lokalizacji to małe litery, cyfry i `_`, np. `gabinet_zabki`; dzięki temu można później dodać kolejne lokalizacje bez zmiany modelu.

`source` w obecnej wersji musi mieć wartość `home_assistant`. Zdarzenia zapisują oryginalny ISO-8601 timestamp wraz z offsetem oraz osobny, znormalizowany timestamp UTC. Ten drugi będzie podstawą przyszłego liczenia czasu pracy, a pierwszy zachowuje lokalną datę i godzinę zdarzenia również przy zmianie czasu letniego/zimowego.

# Phase 9 — procedura operatora dla profilu ingress 8B2

**Nie wykonywać w Phase 8B2.** Repozytorium zawiera nieaktywne przykłady.
Nie utworzono DNS, tunelu ani aplikacji Access. Obecny LAN HTTP działa z
`INGRESS_PROFILE=lan` (domyślne), dotychczasowymi origins i Secure=false.
Aktualizacja kodu sama nie instaluje Nginx i nie zmienia listenerów/firewalla.

Stan bazowy potwierdzony przez operatora: Debian 13 LXC na Proxmox, main
`de963ae`, head `20261025_08`, jeden worker z `--no-proxy-headers`, działające
8B1-A/B, login, UI i dane; package-install/service-user runtime zaliczone przez
operatora. Codex nie ma dostępu do tego LXC i nie powtarzał tam tych testów.

## Architektura i wartości do ustalenia

```text
Internet browser --HTTPS--> Access (allowlista + default-deny)
    --> własny Tunnel / Protect with Access (JWT)
    --> cloudflared LXC --TLS z weryfikacją CA/SAN-->
        WT LXC: Nginx <LAN_IP>:<BROWSER_TLS_PORT> [ACL: tylko cloudflared]
            --TCP bind źródłowy 127.0.0.2-->
                Uvicorn 127.0.0.1:<BACKEND_PORT> [1 worker; no-proxy-headers]
                    --> SQLite /var/lib/work-tracker
HA --prywatny LAN HTTP + X-Webhook-Token-->
        WT LXC: Nginx <LAN_IP>:<HA_PORT> [ACL: tylko HA]
            --TCP bind źródłowy 127.0.0.3--> ten sam Uvicorn
```

Nginx jest minimalną dodatkową infrastrukturą potrzebną przy osobnych LXC.
Repo nie zawiera jednostki ani efektywnej konfiguracji uruchomionego cloudflared;
Codex nie ma dostępu do jego LXC. Operator musi ustalić wersję, local/remote
management, repliki i peery, nie publikując tokenów z ExecStart/env.
Loopback 127.0.0.2/.3 to adresy **wewnątrz WT LXC**, nie IP cloudflared.
Nie zmieniać ich: backend rozpoznaje te dwa peery i odrzuca pozostałe, także
127.0.0.1. Nie stosować Nginx `real_ip`, PROXY protocol ani proxy headers Uvicorn.
`scope.client` pozostaje adresem transportowym; osobny klucz w ASGI state służy
tylko limiterowi. Sama nazwa hosta i nagłówek nie otwierają żadnego ingressu.

Granica zaufania obejmuje root/operatorów obu LXC, proces cloudflared i Nginx,
loopback WT oraz chroniony routing LAN. Złośliwy proces w WT może zbindować
127.0.0.2: izolacja nie chroni przed przejęciem zaufanego LXC. Nginx i env muszą
być zarządzane przez roota, bez zapisu przez użytkownika aplikacji. Nie dodajemy
osobnej usługi auth, Redis ani drugiego procesu aplikacji.

Operator musi podać wszystkie placeholdery:

| Placeholder | Znaczenie |
|---|---|
| `<PUBLIC_HOSTNAME>` | Jedyny browser hostname; bez portu, aliasów i ścieżki |
| `<WORK_TRACKER_LAN_IP>` | Prywatny IP WT LXC; dla IPv6 w URL/listen użyć `[adres]` |
| `<BROWSER_TLS_PORT>` | Oddzielny port TLS dostępny tylko cloudflared |
| `<HA_PORT>` | Dotychczasowy port URL HA, zachowany bez zmian automatyzacji |
| `<BACKEND_PORT>` | Nowy, różny port wyłącznie na 127.0.0.1 |
| `<CLOUDFLARED_SOURCE_IP>` | Faktyczny, stabilny peer z LXC connectora, host /32 lub /128 |
| `<HA_SOURCE_IP>` | Faktyczny, stabilny LAN peer HA, host /32 lub /128 |
| `<ORIGIN_TLS_NAME>` | SAN certyfikatu Nginx; nie wymaga publicznego DNS |
| `<ORIGIN_CERT_PATH>`, `<ORIGIN_KEY_PATH>` | Certyfikat i klucz TLS na WT LXC poza checkoutem |
| `<CA_PEM_PATH_ON_CLOUDFLARED_LXC>` | Zaufana CA na LXC cloudflared, czytelna dla connectora |
| `<ACCESS_TEAM_SLUG>` | Slug organizacji; issuer `https://<slug>.cloudflareaccess.com` |
| `<ACCESS_APPLICATION_AUD>` | AUD konkretnej aplikacji WT, nie aplikacji o szerszej polityce |
| `<ALLOWED_USER_EMAIL>` | Dokładna tożsamość z IdP; powtórzyć email include dla każdej osoby |
| `<TUNNEL_UUID>`, `<CREDENTIALS_FILE_OUTSIDE_CHECKOUT>` | Własny tunnel i chroniona ścieżka credentials dla local management |

Nie zakładać adresu connectora. Ustalić go z routingu i kontrolowanego połączenia,
uwzględniając NAT. Wspólny SNAT dla HA/connectora/innych klientów nie daje
wymaganej izolacji: najpierw rozdzielić routing/sieci albo zapewnić odrębne
uwierzytelnienie transportu. Nie allowlistować całego LAN, podsieci ani gatewaya.
Dodatkowe repliki connectora wymagają osobnej, dokładnej ACL i powtórzenia testów.

## 1. Przygotowanie i dostęp awaryjny

Zachować aktywną konsolę Proxmox/SSH niezależną od browser UI. Potwierdzić konto
WT i hasło. Wykonać aktualny backup SQLite przez istniejącą procedurę oraz kopię
env, konfiguracji systemd/Nginx/firewalla i Access/tunnel poza checkoutem.
Nie wyświetlać tokenów, haseł ani zawartości credentials w logach/terminalu.
Backupy i sekrety: katalogi 0700/0750, pliki 0600/0640 zgodnie z odbiorcą;
service user WT czyta env i zapisuje tylko dane, nie kod, cert keys ani backupy.
Nie kopiować ich do `frontend/dist`, static root ani `/opt/work-tracker`.

Użyć normalnego updatera z clean-worktree, backupem, fast-forward-only i
rollbackiem. 8B2 nie zmienia schematu; head pozostaje `20261025_08`.
Nie kasować `login_attempts`, nie odwoływać sesji i nie resetować liczników.
Przy zmianie ścieżki ta sama osoba/IP zachowuje zapisany budżet, jeśli jej
rzeczywisty adres się nie zmienia. Przejście NAT LAN → inny publiczny IP z natury
oznacza inny klucz IP; konto nadal ma tę samą historię.

## 2. Access przed routingiem

Dopiero w Phase 9 utworzyć self-hosted application dla **całego hostname**
`<PUBLIC_HOSTNAME>` (bez path). Użyć wzorca
[`access-policy.json.example`](../../deploy/ingress/access-policy.json.example).
Jedyna polityka Allow zawiera jawne osoby; brak dopasowania = odmowa.
Bez Bypass, Everyone, Service Auth, wyjątków health/API/webhook i oddzielnych
path applications o słabszych regułach. Skontrolować istniejące aplikacje,
aliasy, wildcard routes, load balancery, quick tunnels i alternatywne hosty.
Access nie tworzy sesji WT: po Access nadal jest zwykły login i CSRF WT.
Zapisać AUD i issuer/team. Przed zmianami sprawdzić dostępność funkcji w planie
konta. Test wykluczonej osoby musi obejmować rzeczywiste logowanie w IdP.

## 3. Tunnel i walidacja JWT

Minimalna zweryfikowana lokalnie wersja: **cloudflared 2026.10.0**. Przed
wdrożeniem wybrać aktualną wspieraną wersję i powtórzyć testy. Na **każdej**
replice ustawić `TUNNEL_DISABLE_PATH_NORMALIZATION=false` i sprawdzić skuteczny
env procesu po restarcie. Wersja 2026.10.0 domyślnie tego nie włącza.
Normalizacja dot segments, backslash i slash dotyczy wyboru reguły; oryginalny
path nadal dociera do originu i jest tam odrzucany, jeśli niekanoniczny.

Locally-managed: wypełnić
[`cloudflared.yml.example`](../../deploy/ingress/cloudflared.yml.example),
wykonać `cloudflared --config <PLIK> tunnel ingress validate` oraz
`cloudflared --config <PLIK> tunnel ingress rule https://<PUBLIC_HOSTNAME>/api/auth/login`.
Te komendy nie uruchamiają tunelu. Cały hostname musi trafić w jedną regułę
z `access.required: true`, `teamName`, jednym właściwym `audTag`; catch-all 404.
Nie dodawać reguł path, osobnego publicznego HA ani `httpHostHeader` maskującego
błędny hostname. HTTPS do Nginx z `noTLSVerify: false`, CA i poprawnym SAN/SNI.
Certyfikaty oraz credentials umieścić poza checkoutem.

Remotely-managed: dashboard → route → Additional application settings →
**Protect with Access**; podać ten sam team i AUD. Wzorzec payloadu:
[`cloudflared-remote.json.example`](../../deploy/ingress/cloudflared-remote.json.example).
API GET `/accounts/<ACCOUNT_ID>/cfd_tunnel/<TUNNEL_UUID>/configurations` musi
potwierdzić efektywne `originRequest.access` w **regule hostname**. Sprawdzić
version/config_version wszystkich connectorów, nie tylko zapis formularza.
Nie kopiować local YAML z nadzieją, że nadpisze zdalną konfigurację. CA path
musi istnieć na każdym connectorze. Nie publikować outputu z credentials.

Protect with Access weryfikuje podpis przez JWKS organizacji, dokładny issuer,
expiry i audience. Sprawdzono rzeczywisty `NewJWTValidator` z RSA/JWKS, nie samo
istnienie `Cf-Access-Jwt-Assertion`. Nie weryfikuje lokalnej listy email:
polityka osób działa w Access przed wydaniem tokenu. Błędny team/AUD/JWKS ma
zamknąć browser path; nie rozluźniać ochrony dla przywrócenia działania.
Istniejące JWT mogą żyć do expiry po zmianie uprawnień — sprawdzić revocation
Access i czas sesji Access, niezależnie od niezmienionych 30/180 dni WT.

Źródła sprawdzone 2026-10-25: [origin parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/),
[remote configuration API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/subresources/configurations/),
[normalizacja 2026.10.0](https://developers.cloudflare.com/changelog/post/2026-10-22-cloudflared-path-normalization/),
[kod walidatora przypiętej wersji](https://github.com/cloudflare/cloudflared/blob/2026.10.0/ingress/middleware/jwtvalidator.go).

## 4. Odrębne wejścia i firewall

Zainstalować Nginx na WT LXC. Wypełnić
[`nginx.conf.example`](../../deploy/ingress/nginx.conf.example) i włączyć wewnątrz
`http {}`. Usunąć inne generic proxy/listenery WT. `nginx -t` przed reload.
Przy IPv6 dodać konkretne listen/allow i te same restrykcje firewalla; brak
reguły IPv6 nie może pozostawiać alternatywnej ścieżki do aplikacji.
Nginx browser słucha TLS tylko z connectora; HA słucha prywatnego HTTP tylko z HA.
Nie przekazuje Cookie/Origin/CSRF/Access do HA. Token HA jest nadal sprawdzany
w istniejącym handlerze. Brak redirectów/canonicalizacji webhooków.
Backend odrzuca wszystkie inne ścieżki/metody HA, a browser cały `/api/webhook*`.
Zakodowane ścieżki, `//`, backslash i segmenty `.`/`..` nie trafiają do routingu.
Procenty w query nie wpływają na tę kontrolę. Nie używać proxy_pass z URI.

W firewallu WT/Proxmox wprowadzić następujące reguły PRZED ogólnymi LAN allow:

| Cel | Dozwolone źródło | Pozostałe źródła, IPv4 i IPv6 |
|---|---|---|
| BROWSER_TLS_PORT | wyłącznie rzeczywisty cloudflared peer | DROP |
| HA_PORT | wyłącznie rzeczywisty HA peer | DROP |
| BACKEND_PORT | loopback wewnątrz WT LXC | DROP |

Przykładowa logika dla istniejącego nftables input chain (operator dopasowuje
składnię, interfejsy i numery; nie zastępuje całego rulesetu ani reguł SSH):

```text
iifname "lo" tcp dport <BACKEND_PORT> accept
tcp dport <BACKEND_PORT> drop
ip saddr <CLOUDFLARED_SOURCE_IPV4> tcp dport <BROWSER_TLS_PORT> accept
tcp dport <BROWSER_TLS_PORT> drop
ip saddr <HA_SOURCE_IPV4> tcp dport <HA_PORT> accept
tcp dport <HA_PORT> drop
```

Jeżeli używane są adresy IPv6, wstawić konkretne `ip6 saddr` przed odpowiednim
drop. Nie otwierać portów na routerze ani publicznego origin DNS. Pozostawić
wyłącznie wymagane egress cloudflared oraz JWKS/NTP/DNS. Sprawdzić skuteczne
reguły, bind `ss -ltnp`, brak DNAT i dostęp z niedozwolonego trzeciego hosta.
ACL Nginx działa dodatkowo; Edge path filtering nie jest granicą separacji.

## 5. HTTPS, IP i kontrolowany cutover

Przed publicznym routingiem: Always Use HTTPS na Edge; brak Worker routes,
Worker subrequests i transformów zmieniających visitor IP dla hostname;
blokować Worker subrequests na Edge (`cf.worker.upstream_zone`), sprawdzić
realnym testem cross-zone i same-zone. Wyłączyć Pseudo IPv4 (co najmniej nigdy
Overwrite Headers), Remove visitor IP headers i inne modyfikacje IP. Włączyć
normalizację URL na Edge i do originu; origin pozostaje bezpieczny również bez
niej. Brak reguł Access path zapobiega różnicom dopasowania na warstwach.
[Semantyka nagłówków Cloudflare i wyjątki Workers/Pseudo IPv4](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
[Pole identyfikujące Worker subrequest](https://developers.cloudflare.com/ruleset-engine/rules-language/fields/reference/cf.worker.upstream_zone/)
i [ustawienia normalizacji Edge/origin](https://developers.cloudflare.com/rules/normalization/settings/)
wymagają sprawdzenia efektywnych reguł na koncie operatora.

Tylko peer 127.0.0.2 może dostarczać CF-Connecting-IP. Jest to wynik łańcucha:
Access → własny tunnel z walidacją JWT → źródłowa ACL/firewall → Nginx →
izolowany loopback. Cloudflare nadpisuje klientowski IP w tej trasie, przy
wyłączonych powyższych wyjątkach. Nginx usuwa XFF/Forwarded/X-Real-IP;
aplikacja ich nie używa. Nie przepisywać `scope.client` na publiczny IP.
Brak, niepoprawna wartość lub duplikaty CF-Connecting-IP → login 503 bez
cookie/Argon2/admission; brak fallbacku do adresu connectora. Nginx łączy
duplikaty przecinkiem, co też zostaje odrzucone. CF-Worker i znany cross-zone
Worker IP są odrzucane; pseudo IPv4 240/4 również. Te dodatkowe kontrole nie
zastępują weryfikacji konfiguracji strefy. LAN profile nadal ignoruje CF/XFF.

Wykonać kopie plików z kroku 1. Przygotować lokalny przetestowany plan cofnięcia
bez udziału przeglądarki. W krótkim oknie serwisowym, nadal bez publicznej trasy:

1. Zatrzymać WT, by zwolnić dotychczasowy port HA; surowych zdarzeń nie usuwać.
2. W env zastosować [`https.env.example`](../../deploy/ingress/https.env.example):
   INGRESS_PROFILE=split, jeden dokładny HTTPS origin, CORS pusty, Secure=true,
   HSTS=0. Nie zmieniać DB, tokenu HA ani parametrów sesji.
3. Zainstalować systemd drop-in z
   [`work-tracker.service.override.example`](../../deploy/ingress/work-tracker.service.override.example).
   Stały backend bind 127.0.0.1 na innym porcie; APP_HOST/PORT w env mogą pozostać
   jako poprzednie wartości do kontrolowanego rollbacku, drop-in je zastępuje.
4. `systemctl daemon-reload`, uruchomić WT i Nginx, sprawdzić bindy oraz ACL.
   Nie uruchamiać dwóch aplikacji/workerów do tej samej DB.
5. Z HA sprawdzić dotychczasowy URL entry/exit/correction, dobry/zły/brak tokenu,
   bez Cookie/Origin/Access. Nie zmieniać istniejącej konfiguracji HA.
6. Jeśli kontrola nie przejdzie, natychmiast przywrócić zapisane LAN env/unit/bindy
   i zwolnić HA_PORT w Nginx. Nie czekać na naprawę Access kosztem HA.
7. Dopiero po testach negatywnych originu i zatwierdzonej bramce Phase 9 uruchomić
   właściwy tunnel i DNS. Do tego czasu browser UI jest czasowo niedostępne;
   konsola operatora i prywatny HA pozostają niezależne.
8. Przez HTTPS sprawdzić Access, dodatkowy login WT, Secure/HttpOnly/SameSite=Lax,
   brak Domain, Origin/heartbeat/logout, eksporty i dashboard. Odświeżyć stary
   frontend po cutover. Brak CORS i HTTP fallbacku. Dawne cookie dla LAN IP nie
   przechodzi do innego hosta; zalogować się ponownie, bez nowej polityki sesji.
9. Sprawdzić na realnych dwóch sieciach IP budżety i spoofing. Wykonywać na
   kontrolowanym koncie, po czym odczekać pełne 15 minut; nie czyścić tabeli.
10. Po poprawnej weryfikacji HTTPS włączyć HSTS najpierw 300 s, potem 86400,
    docelowo 31536000. Bez includeSubDomains/preload. HSTS nie wymusza zmian HA.

Zegar/NTP obu LXC musi być poprawny. Przerwa w krótkim przełączeniu listenerów
może wymagać ponowienia webhooka przez HA; nie obiecujemy kolejkowania ani
nie wprowadzamy zmian automatyzacji. Uzgodnić okno bez przejść entry/exit.

## 6. Testy pozytywne, negatywne i bramka Phase 9

Pełna macierz i lokalne dowody:
[security-8b2-verification.md](../architecture/security-8b2-verification.md).
Powtórzyć rzeczywiste proxy i JWT testy na wybranej wersji przed rolloutem.
Następujące kontrole pozostają **BLOCKED / REQUIRES PHASE 9 VERIFICATION**:

- Z Internetu bez Access JWT, z forged/expired/wrong AUD/team oraz wykluczoną
  osobą w IdP nie ma dostępu do UI ani API. Zweryfikować wszystkie metody,
  aliasy i warianty ścieżek; poprawny Access nadal wymaga sesji WT.
- Publiczny HA odrzucany również po poprawnym Access i loginie WT;
  bezpośrednie wejścia LAN/Internet i alternatywne DNS nie omijają ochrony.
- Rzeczywiste CF-Connecting-IP dla dwóch publicznych IP, NAT i IPv6 /64;
  spoofing CF/XFF/Forwarded nie zmienia kluczy; testy Workers/Pseudo IPv4.
  Można sprawdzić same `ip_key`/daty w SQLite jako operator bez logowania tokenów,
  haseł, cookie lub JWT. Nie dodajemy diagnostycznego publicznego endpointu IP.
- TLS zaufany na każdym łączu, brak HTTP UI, cookie/Origin/heartbeat i HSTS.
- Wyłączyć trasę WT / zatrzymać connector WT: oba webhooki HA nadal działają.
  Powtórzyć przy wyczerpanym limiterze, niedostępnym Access i Cloudflare.
- Faktyczne reguły firewalla/permissions/backupów/systemd na obu LXC.

Przygotowano opt-in `deploy/tests/access_live_test.py`. Uruchamia go operator
**wyłącznie w Phase 9** ze zrealizowanymi fixtures, nigdy na podstawie samych
spreparowanych nagłówków. `WT_PHASE9_ACCESS_FIXTURE` wskazuje plik JSON 0600
poza checkoutem: `origin` i `tokens` z kluczami allowed, expired, wrong_audience,
wrong_team, denied_user. Wartości to rzeczywiste tokeny testowe; nie umieszczać
ich w CLI, Git ani logach i nie używać pytest `--showlocals`/pełnych tracebacków.
Brak fixture → BLOCKED, nie PASS. Test wykluczonego IdP może wymagać ręcznego
logowania, gdy Access nie wydaje żadnego tokenu; ten dowód nadal trzeba zapisać.
Probes nie zastępują testu dwóch prawdziwych klientów ani firewalla.

## 7. Natychmiastowe wyłączenie i rollback

Najpierw zamknąć **wyłącznie browser port** w firewallu WT. Usunąć/wyłączyć
route WT/DNS albo zatrzymać dedykowany connector WT. Jeśli tunnel współdzieli
inne aplikacje, usunąć tylko regułę WT (catch-all 404); nie wyłączać cudzych tras.
Nie usuwać Access, nie wprowadzać Bypass i nie otwierać originu. Nginx HA i
WT split pozostają uruchomione: lokalne entry/exit/correction działają dalej.
Ta operacja nie dotyka DB ani konfiguracji HA. Sprawdzić to natychmiast.

Jeśli potrzeba wrócić do LAN UI: po wyłączeniu publicznej trasy przywrócić
zapisany LAN env (INGRESS_PROFILE=lan, poprzednie origins/CORS, Secure=false,
HSTS=0); zatrzymać HA listener Nginx, usunąć **tylko ingress drop-in**,
`daemon-reload` i restart WT na dawnym LAN porcie. Odtworzyć kontrolowany
LAN firewall z zapisanej kopii. Nigdy nie udostępniać tego profilu publicznie.
Przeglądarka nadal pamięta HSTS publicznego hosta — użyć poprzedniego LAN IP/origin,
nie próbować HTTP publicznej domeny. Konsola/SSH zapobiega trwałemu odcięciu.

Rollback kodu: zachować bieżącą DB i `login_attempts`, backend/frontend cofnąć
razem przez dotychczasową procedurę; brak migracji 8B2. Nie odtwarzać starej
DB kosztem nowych zdarzeń. Jeśli restore jest nieunikniony, odwołać odtworzone
sesje i odczekać 15 minut zgodnie z istniejącą procedurą. Backup env/drop-in/
Nginx/firewalla i konfiguracji Access/tunnel jest osobny od rollbacku Git;
updater nie zarządza tymi plikami operatora.

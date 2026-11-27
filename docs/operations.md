# F9: Betrieb und reproduzierbare Leistungsabnahme

## Container und vollständige Tests

Das Laufzeitimage verwendet Python 3.13, `uv.lock`, genau einen Uvicorn-Worker und
UID/GID `10001:10001`. Es enthält weder uv noch Browser-/Testwerkzeuge. Schrift,
Quelltext und Lizenzen werden explizit kopiert. Die editierbare Installation unter
`/app/src` erhält den vorhandenen relativen Asset-Pfad `/app/assets`. Fehlende
Schriften werden beim Start erkannt. Logs gehen ausschließlich nach stdout;
Access-Logs sind deaktiviert. `.dockerignore` erlaubt nur benötigte Build-Eingaben.
HarfBuzz und FriBidi sind auch im schlanken Runtime-Image installiert: ohne diese
dynamisch geladenen Bibliotheken schaltet Pillow still von RAQM auf BASIC um.
Ein echter Host-CLI-/Container-API-Bytevergleich erkannte diesen Verpackungsfehler;
nach Ergänzung der Bibliotheken bestand dieselbe unveränderte Prüfung.

Auf einem normalen Docker-Host:

```sh
docker build --target runtime -t meme-generator:f9 .
docker run --rm --name meme-f9 -p 8000:8000 --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges meme-generator:f9
```

Der Healthcheck prüft `/health`; erst bei `healthy` testen. `/tmp` ist flüchtiges,
begrenztes RAM, kein Host-Verzeichnis. Die API verwendet es nicht für Uploads.
Der optionale Target `test` ergänzt gesperrte Dev-Abhängigkeiten, Chromium und
**alle** Unit-, HTTP- und Browser-Tests. Auch eigene Testserver/CLI-Prozesse laufen
dann innerhalb desselben nicht-root, read-only Containers:

```sh
docker build --target test -t meme-generator:f9-test .
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,size=512m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges meme-generator:f9-test
```

Im eingeschränkten Amp-Orb benötigt Docker `sudo` und `--network host` bei Builds
und Runs. Das ist keine Empfehlung für einen öffentlichen Produktionshost. Der
hier tatsächlich gemessene Runtime-Container wird durch einen lokalen,
supervisierten Orb-Service gestartet:

```sh
amp orb service start f9-container --port 8000 --command \
  'sudo docker run --rm --name meme-f9 --network host --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges meme-generator:f9'
sudo docker inspect meme-f9 --format \
  'image={{.Image}} readonly={{.HostConfig.ReadonlyRootfs}} user={{.Config.User}} health={{.State.Health.Status}}'
```

Start/Restart ist asynchron. Auf `healthy` warten; Status und Logs liefern
`amp orb service status f9-container` / `amp orb service logs f9-container`.
Stoppen mit `amp orb service stop f9-container`. Kein Produktionsdeployment.

Optional testet auch der Host gegen das tatsächlich ausgelieferte Runtime-Image:

```sh
MEME_TEST_BASE_URL=http://127.0.0.1:8000 uv run pytest e2e \
  --output .amp/in/orb-backup/f9-external-tests
```

Diese Variable betrifft das `base_url`-Fixture. Tests mit absichtlich eigenem
Prozess (Dateischreib-Guard, Logs, CLI) bleiben dabei Host-Prozesse; erst der
vollständige `test`-Container oben prüft auch diese innerhalb des Containers.
Alle Loopback-Adressen in diesen Befehlen gelten nur auf dem jeweiligen Host.

## Messverfahren und Wiederholung

`scripts/benchmark.py` verwendet HTTPX und Pillow aus den gesperrten
Dev-Abhängigkeiten. Es erzeugt ein deterministisches **2000×1000 RGB-Rauschbild**,
Seed 9, JPEG-Qualität 85, Subsampling 2. SHA-256, Bytezahl und beide Captions stehen
im Rohbericht. Das ist ein ausdrücklich definiertes 2-MP-JPEG, kein Versprechen
für beliebige Bilder oder Internetverbindungen.

Nach Healthcheck, einer vollständig dekodierten Kontrollantwort und fünf Warmups:

- Drei Wiederholungen mit je **100 seriellen Anfragen**, danach **20 parallelen
  Nutzern über 60 Sekunden**. Ein offener POST je Nutzer, keine Denkzeit/Retry.
- Clientseitige Zeit über vollständigen Upload, Warteschlange, Verarbeitung und
  vollständigen Antwort-Download. p95 nach Nearest Rank über **alle** Versuche,
  auch Fehler/Timeouts. Beim Ablauf gestartete Requests werden abgewartet und
  mitgezählt, nicht verworfen. Status, MIME und JPEG-Anfang/-Ende werden geprüft.
- Ein langlebiger `docker exec`-Helper liest `/proc/1/status` alle 100 ms. Er
  verlangt PID 1 als expliziten Einzelworker. `VmHWM` erfasst zusätzlich Spitzen
  zwischen Samples, konservativ über die ganze Worker-Lebensdauer. Das ist
  Prozess-RSS, nicht ein durch die Workerzahl geteilter Container-Mittelwert.
- Speichergrenze **300.000.000 Bytes**, strenger als 300 MiB. Kein OOM-Limit, das
  höheren Bedarf versteckt. Jede Wiederholung muss beide Latenzziele erfüllen;
  der beschreibende Median verdeckt keine misslungene Wiederholung.
- JSON enthält alle Einzelanfragen und RSS-Samples, Image-/Container-ID,
  Ressourcenlimits, Benchmark-Hash, CPU/Host, anfängliche Load-Average und Optionen.
  Daneben wird ein Markdown-Bericht geschrieben. Exitcode 1 bei Zielverfehlung.
  Ein verkürzter Smoke-Test kann niemals `acceptance_passed=true` erhalten.

Client und Server teilen sich hier einen Linux-x86_64-Orb mit vier logischen
CPUs. Kein TLS, Proxy, Internetpfad oder externer Lastgenerator. Nach allen Builds
und funktionalen Tests den Runtime-Container für einen frischen HWM neu starten;
während der Messung keine Browser, Builds oder weitere Last laufen lassen:

```sh
amp orb service restart f9-container
# Erst nach health=healthy:
uv run python scripts/benchmark.py --container meme-f9 \
  --docker-command 'sudo docker' --duration 60 --users 20 \
  --single-requests 100 --warmup 5 --repetitions 3 \
  --output .amp/in/orb-backup/f9-final.json
```

## Gemessener Vorher-/Nachher-Vergleich

Der unveränderte integrierte Stand vor F9 wurde mit genau demselben Lastverfahren
dreimal gemessen. Das Lastziel wurde **verfehlt**: 1525,62 / 1568,14 / 1576,89 ms
p95 bei jeweils null Fehlern. Seriell: 86,10 / 78,42 / 79,43 ms. Worker-HWM:
107,39 MB. Image: `sha256:dde715fa1582068420d6f9cd40316cdcb086704fcedbf772b9e47a0ca4981421`.
Dieses frühe Container-Image hatte zusätzlich noch den oben beschriebenen
RAQM-Verpackungsfehler. Der finale Stand behebt ihn; das wird nicht als isolierte
Messung nur einer einzelnen Codezeile ausgegeben.

Die bisherige einzelne Zulassung serialisierte die gesamte Pipeline. F9 lässt
drei Uploads/Renderings gleichzeitig zu, reserviert beim Dekodieren/Verkleinern
aber gemeinsam höchstens 25 MP Originalbildfläche. GIF-Verarbeitung reserviert
das ganze Budget. Lanczos, Encoderqualität und Caption-Fitting bleiben erhalten;
eine unnötige RGB-Kopie entfällt. Ein globaler Dekodier-Lock plus Bicubic wurde
als Diagnose verworfen: weiterhin über 1 s p95 und unnötige Qualitätsänderung.

Ein zusätzlicher Test mit vier gleichzeitigen gültigen 25-MP-RGBA-PNGs deckte
543,22 MB Worker-HWM auf. Die feste glibc-MMap-Schwelle von 128 KiB verhindert,
dass freigegebene große Pillow-Puffer in Thread-Arenen verbleiben; allein damit
blieben noch 309,37 MB. Die Pipeline gibt daher zusätzlich das Original frei,
bevor sie dessen vormultiplizierte Kopie resampelt. Das erhält Pillows Ergebnis
pixelgenau, statt Alpha-Kanäle unabhängig zu skalieren oder die Filterqualität
zu senken. Zwei HTTP-Kontrollen für RGBA und Graustufen-Alpha vergleichen die
unberührte Bildmitte mit Pillows ursprünglicher Lanczos-Verarbeitung.

Mit dieser Speicherstrategie verfehlten zwei aktive Renderings weiterhin das
Lastziel (1086,16 / 1066,45 / 1107,14 ms p95); die finale Zulassung beträgt drei.
Diese Diagnosewerte sind keine erfolgreichen Abnahmeläufe.

### Erfolgreiche finale Abnahme, 2026-11-27 23:30 UTC

Runtime-Image:
`sha256:83b8a25ab34964d2c9efb69921a3756577f4d62babf28df6ccaa0fc8883d9cc7`.
Das Eingabe-JPEG hat 1.514.419 Bytes und SHA-256
`ddb02a0e7c57079fe286415868333b9258a233fb1876fd688bb603131749dd18`.

| Wiederholung | Seriell p95, Ziel <300 ms | Last p95, Ziel <1000 ms | Lastfehler |
| --- | ---: | ---: | ---: |
| 1 | 93,51 ms | 951,49 ms | 0 / 1406 |
| 2 | 95,90 ms | 943,76 ms | 0 / 1407 |
| 3 | 91,86 ms | 940,88 ms | 0 / 1414 |

Jeweils 100 serielle Anfragen ohne Fehler und 60 Sekunden Last mit 20 Nutzern.
Median der drei Last-p95: **943,76 ms**, vorher 1568,14 ms. Größte RSS-Stichprobe:
**132,94 MB**; konservativer Worker-Lebenszeit-HWM: **135,08 MB**, Ziel ≤300 MB.
Alle Kriterien und `acceptance_passed` sind true, Exitcode 0. UID 10001,
read-only Root-FS, kein Container-Neustart und keine CPU-/Speicher-Zwangsgrenze.

Anschließend im selben Worker: vier parallele 5000×5000-RGBA-PNGs, je 109.700
Upload-Bytes, alle **200**, alle **1600×1600**, Alpha 128 in der unberührten Mitte.
Der Lebenszeit-HWM einschließlich dieser zusätzlichen Kontrolle beträgt
**268.242.944 Bytes (268,24 MB)**, ebenfalls unter 300 MB. Diese Anfragen sind
nicht Teil der JPEG-Latenzstatistik.

Vollständiges `test`-Image, das direkt vom Runtime-Image erbt, mit read-only
Root-FS und UID 10001: **76 passed** (21,56 s). HTTP-/Chromium-Suite gegen das
schlanke Runtime-Image: **69 passed** (17,87 s); ihre absichtlich eigenen
Host-Prozesse sind wie oben abgegrenzt. Host-Suite: **76 passed**. Ruff, Format
und strikte Typprüfung bestanden. Eine bestehende Starlette/httpx-Warnung bleibt.

Rohberichte und alle Einzelmessungen: `f9-before.json` / `f9-before.md` sowie
`f9-after.json` / `f9-after.md` im Implementierungsthread, zusätzlich unter
`.amp/in/orb-backup/` gesichert. Der PR enthält auch den Reproducer der
zusätzlichen 25-MP-Speicherkontrolle. Keine Produktions- oder Cross-Browser-Abnahme.

# Meme Generator

Web-App und API, die eine Caption im Meme-Stil auf ein Bild schreibt: Top text oben, Bottom text unten, in Großbuchstaben, weiße Schrift mit schwarzem Rand, im Font Anton.

Die Social-Media-Agentur des ZDF erstellt damit Memes für ihre Kanäle. Bilder und Texte sollen nicht auf fremden Meme-Seiten landen, denn viele Bilder sind nur für die eigenen Kanäle lizenziert. Deshalb hat die Agentur einen eigenen Generator, und sie ist auf ihn angewiesen.

Der Dienst ist über einen ersten Stand nie hinausgekommen. Was er heute kann, steht unter [Was der Dienst kann](#was-der-dienst-kann). Der letzte Entwickler ist nicht mehr dabei. Die offene Arbeit hat er als Issues mit Priorität hinterlegt, siehe [Offene Arbeit](#offene-arbeit). Neue Entwickler kommen als Externe dazu und arbeiten in einem Fork, siehe [Mitarbeiten](#mitarbeiten).

## Was der Dienst kann

- Seite unter `/`: JPEG hochladen, Top text, Bottom text oder beides eingeben, „Create meme“. Die Seite zeigt das Meme oder die Fehlermeldung der API.
- API: `POST /memes` mit `multipart/form-data` (`image`, `top_text`, `bottom_text`), Antwort ist das Meme als JPEG. Swagger UI unter `/docs`.
- Fehler kommen als JSON `{"error": "…", "message": "…"}`: ohne Text 400, Bild kein JPEG oder nicht lesbar 415.
- Top text steht am oberen, Bottom text am unteren Rand, in fester Schriftgröße (1/10 der Bildhöhe) und ohne automatischen Umbruch.

## Aufsetzen

Du brauchst [uv](https://docs.astral.sh/uv/getting-started/installation/). Python 3.13 lädt uv bei Bedarf selbst.

```sh
uv sync
uv run playwright install chromium
```

Der zweite Befehl lädt Chromium für die Browser-Tests (Download etwa 300 MB, auf der Platte etwa 600 MB). Unter Linux: `uv run playwright install --with-deps chromium`.

## Starten

```sh
uv run python -m meme_generator
```

Dann <http://127.0.0.1:8000> im Browser öffnen. Anderer Port: Umgebungsvariable `MEME_PORT`.

### Lokal ohne HTTP rendern

```sh
uv run python -m meme_generator render input.jpg output.jpg --top-text "Hallo" --bottom-text "Welt"
```

CLI und API benutzen dieselbe Bildpipeline und erzeugen dieselben Bytes. Nur die CLI schreibt
die ausdrücklich angegebene Ausgabedatei; die API hält Uploads und Ergebnisse im Speicher.
`python -m meme_generator` (oder `serve`) startet weiterhin den Server.

### Request-ID und private Logs

Jede Antwort trägt `X-Request-ID`, Fehler-JSON zusätzlich `request_id`. Eine übergebene ID
aus 1–64 ASCII-Buchstaben, Ziffern oder `-` bleibt erhalten; andere werden ersetzt.
Der Server schreibt genau eine JSON-Metadatenzeile pro Anfrage auf stdout: ID, Status,
Bildformat/-größe, Textlängen und Zeiten für Warteschlange, Upload, Dekodieren, Rendern und
Kodieren, soweit der jeweilige Schritt erreicht wurde. Caption, Dateiname, URL/Query,
Bilddaten und Exception-Inhalte werden nicht geloggt. Der mitgelieferte Server deaktiviert
Uvicorns separate Access-Logs; bei eigenem Uvicorn-Aufruf ebenfalls `--no-access-log` setzen.

## Testen

```sh
uv run pytest       # Unit-Tests, schnell, ohne Browser
uv run pytest e2e   # startet den Server selbst, testet über HTTP und im Browser
```

### Video aufnehmen

```sh
uv run pytest e2e --video on --output videos --slowmo 500
```

Danach liegt je Browser-Test ein Video in `videos/<test>/video.webm`; es lässt sich z. B. in Chrome oder Firefox abspielen. `--slowmo 500` verlangsamt den Test; ohne fehlt das Ergebnis oft im Video. Dieser Befehl leert `videos/` vorher; andere Testläufe lassen den Ordner in Ruhe.

## Offene Arbeit

Die offene Arbeit steht als Issues auf GitHub, die Prio als Label am Issue (`Prio 1` ist die höchste):

- [F1 Regeln für Captions mit Meldung in der Oberfläche](https://github.com/BoP-haeger/meme-generator-kata/issues/1) (Prio 2)
- [F2 Lesbarkeit beweisen](https://github.com/BoP-haeger/meme-generator-kata/issues/2) (Prio 3)
- [F3 Lange Captions](https://github.com/BoP-haeger/meme-generator-kata/issues/3) (Prio 1)
- [F4 PNG und WebP](https://github.com/BoP-haeger/meme-generator-kata/issues/4) (Prio 3)
- [F5 Einheitliche Fehler](https://github.com/BoP-haeger/meme-generator-kata/issues/5) (Prio 3)
- [F6 Uploads nie auf der Platte](https://github.com/BoP-haeger/meme-generator-kata/issues/6) (Prio 2)
- [F7 Request-ID und Logs ohne Inhalte](https://github.com/BoP-haeger/meme-generator-kata/issues/7) (Prio 4)
- [F8 Grenzen und Überlast](https://github.com/BoP-haeger/meme-generator-kata/issues/8) (Prio 3)
- [F9 Leistung und Betrieb](https://github.com/BoP-haeger/meme-generator-kata/issues/9) (Prio 4)
- [F10 Animierte Memes (optional)](https://github.com/BoP-haeger/meme-generator-kata/issues/10) (Prio 4)

## Mitarbeiten

Repo auf GitHub forken und den eigenen Stand in den eigenen Fork pushen.

In einem Fork sind Issues auf GitHub standardmäßig abgeschaltet. Lesen auch aus dem Fork: `gh issue view <n> -R BoP-haeger/meme-generator-kata`.

## Lizenzen

- Code: MIT-Lizenz, siehe [`LICENSE`](LICENSE).
- Die Anforderungen beruhen auf der Meme-Generator-Kata in [vanonselenp/agentic-katas](https://github.com/vanonselenp/agentic-katas), Copyright (c) 2026 Peter van Onselen, MIT-Lizenz. Lizenztext: [`LICENSE-agentic-katas`](LICENSE-agentic-katas).
- Font Anton: Copyright 2020 The Anton Project Authors, SIL Open Font License 1.1, siehe [`assets/fonts/OFL.txt`](assets/fonts/OFL.txt).

Fiktives Szenario für eine Schulung. Es besteht keine Verbindung zum ZDF.

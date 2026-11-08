# PBworks → Nextcloud Collectives und Team-Ordner

Mehrstufige Migration aus zwei lokalen Export-Verzeichnisbäumen: PBworks-HTML-Seiten
und exportierte Dateien. Die Originale bleiben unverändert; alle Arbeitsschritte
verwenden ein neues, prüfbares Arbeitsverzeichnis („Bundle“).

Dieses Repository enthält keine Wiki-Inhalte, Zugangsdaten oder Serverkonfiguration.
Die Server-Schritte werden bewusst nicht automatisch nach der Konvertierung ausgeführt.

## Ablauf und Haltepunkte

```text
HTML-Export ─┐
            ├─ prepare.py ─ check_bundle.py ─ MANUELLE KONTROLLE
Dateiexport ┘       │
                   ├─ collective/   Markdown ohne Frontmatter
                   ├─ media/        nur eingebettete lokale Bilder
                   └─ team-files/   alle übrigen Dateien, einschließlich unreferenzierter
                            │
                 Bundle auf Server übertragen
                            │
        MANUELL: Team-Ordner und neues Collective auswählen/anlegen
                            │
        server.py copy      Dateien per Nextcloud-Dateisystem-API kopieren
                            SHA-256 prüfen, IDs erfassen, groupfolders:scan
                            │
        finalize.py         normale Dateilinks → https://cloud/f/<ID>
                            │
        server.py patch     geprüften Collectives-Importer patchen
                            │
        server.py import    Markdown + Bilder als Collective importieren
                            │
        MANUELLE KONTROLLE   Seiten, Tabellen, Anhänge, Rechte, mobile Ansicht
                            │
        server.py restore   Original-Importer wiederherstellen
```

**Wichtig:** Ein Scan aktualisiert den serverseitigen Dateikatalog. Aktive
Nextcloud-Sync-Clients müssen den Team-Ordner ausgewählt haben und holen Änderungen
in ihrem eigenen Sync-Zyklus ab; dieses Repository erzwingt keinen sofortigen
Download auf allen Geräten. Der verwendete Scanbefehl gehört zur
[Team-folders-App](https://github.com/nextcloud/groupfolders).

## Was wohin geht

| Quelldatei / Verwendung | Ziel |
|---|---|
| HTML-Seite im Seitenexport | Collective-Seite, ursprünglicher Dateiname mit `.md` |
| Datei über `<img src>` eingebettet | Anhang bei jeder referenzierenden Seite |
| Datei nur als normaler Link, z. B. PDF | Team-Ordner; Link wird auf `/f/<fileId>` umgeschrieben |
| Datei überhaupt nicht referenziert | Team-Ordner |
| Bild sowohl eingebettet als auch normal verlinkt | Anhang **und** Team-Ordner, damit beide Nutzungen funktionieren |
| Externe Bild-URL | Bleibt extern; wird nicht heruntergeladen |

Die Klassifizierung erfolgt nach Verwendung, nicht nach Dateiendung. Auch eine
unreferenzierte `.md`-Datei aus dem **Dateiexport** bleibt eine normale Datei.
In `collective/` liegen nur Seiten: Der Medienbaum liegt als Geschwisterverzeichnis
`media/` daneben. Dadurch entstehen durch reine Bilderordner keine leeren
Collective-Seiten. Der gepatchte Importer erlaubt Anhänge aus diesem `media/`-Baum.

## Voraussetzungen

- Lokal: Linux oder macOS, Python ≥ 3.11, `uv`, `pandoc`.
- Server: Linux, Python ≥ 3.11 mit den Projekt-Abhängigkeiten, PHP CLI in einer
  zur Nextcloud passenden Version, `patch`, installierte Apps Collectives und Team folders.
- Ausführung der Server-Schritte als Nextcloud-Webserverbenutzer, häufig `www-data`.
- Der ausgewählte Nextcloud-Benutzer benötigt Schreibrechte auf den Team-Ordner
  sowie ausreichende Rechte auf das neue Collective.
- Vorher Nextcloud-Datenbank, Konfiguration und Daten gemäß deinem Backup-Verfahren
  sichern. Während der Migration keine parallelen Schreibzugriffe auf die Ziele.

```bash
git clone git@github.com:axeolotl/pbworks-nextcloud-migration.git
cd pbworks-nextcloud-migration
uv sync --locked
```

Alle folgenden Befehle werden vom Repository-Verzeichnis ausgeführt. Beispielwerte
für Serverpfade, Benutzer, IDs und Cloud-URL müssen ersetzt werden.

## Lokal vorbereiten

```bash
uv run python scripts/prepare.py \
  --pages /export/pbworks-pages \
  --files /export/pbworks-files \
  --out /migration/bundle

uv run python scripts/check_bundle.py /migration/bundle
```

Der Zielordner darf noch nicht existieren und nicht innerhalb eines Exports liegen.
Datei- und Verzeichnisnamen werden in den Kopien nach UTF-8/NFC normalisiert.
Dateien mit Legacy-Dateinamen benötigen bei Bedarf beispielsweise
`--from-encoding cp1252`; der **HTML-Inhalt** muss UTF-8 sein. Namenskollisionen
(auch unterschiedliche Groß-/Kleinschreibung) und Symlinks führen zum Abbruch,
statt Dateien zu überschreiben. Die HTML-Änderungszeit wird auf die Markdown-Datei
übertragen; es wird keine YAML-Frontmatter erzeugt.

Die Auflösung von Dateiverweisen verwendet exakte Pfade und danach einen
**eindeutigen** Dateinamen. Mehrdeutige oder fehlende Ziele erzeugen Fehler.
PBworks-Links wie `/w/page/123/Titel`, `/w/file/fetch/123/Datei` und `/f/Datei`
sowie relative HTML-Dateilinks werden unterstützt.

### Manuelle Kontrolle

- `report.json`: Normalisierungen, Dateizuordnungen, Fehler und Warnungen prüfen.
- `conversion_report.json`: insbesondere komplexe Tabellen prüfen.
- `manifest.json`: Anzahlen und Dateimengen gegen den Export abgleichen.
- Einige Markdown-Seiten lokal öffnen; Text, Tabellen und Bilder kontrollieren.

`ready: false` blockiert die Server-Schritte. Roh-HTML-Links/-Bilder in komplexen
Tabellen werden bewusst als Fehler gemeldet: Der Collectives-Importer verarbeitet
keine beliebigen HTML-Anhänge. Solche Fälle in einer **Arbeitskopie des Exports**
korrigieren und ein neues Bundle erzeugen. Nicht einfach `ready` ändern.

Für mehrdeutige Dateiverweise kann eine JSON-Datei genutzt werden:

```json
{
  "Unterordner/Seite.html::/f/Plan.pdf": "Plaene/Plan.pdf",
  "/f/Foto.png": "Bilder/Foto.png"
}
```

Dann `prepare.py ... --overrides overrides.json` verwenden. Die Zielpfade beziehen
sich auf den normalisierten Dateiexport; Overrides sind nur für Dateiziele gedacht.
Bei nicht auflösbaren Wiki-Seiten muss die Seite im Export ergänzt bzw. der Link
in der Export-Arbeitskopie korrigiert werden.

## Bundle auf den Server übertragen

Repository und Bundle müssen im gleichen Host-/Container-Dateisystem wie `occ`
verfügbar sein. Das Bundle enthält private Inhalte und gehört **nicht** in Git
oder in ein öffentlich ausgeliefertes Webverzeichnis.

```bash
rsync -rt /migration/bundle/ admin@server:/srv/migration/bundle/
```

Auf dem Server eine eigene virtuelle Umgebung anlegen (`uv sync --locked` oder
`python3 -m venv .venv` und `.venv/bin/pip install -r requirements.txt`). Eine vom Mac kopierte
`.venv` nicht verwenden. Der Webserverbenutzer benötigt Schreibrechte auf das
Bundle und für Patch/Restore auf die Importer-Datei sowie deren Verzeichnis
`lib/Service/` (für die Sicherung); Repository und `.venv`
müssen für ihn lesbar sein. Rechte gezielt vergeben, nicht pauschal den gesamten
Nextcloud-Baum umchownen.

In Containern die Server-Befehle im Nextcloud/PHP-Container ausführen oder die
Pfade identisch einhängen; der Container muss Python und die Abhängigkeiten besitzen.

## Team-Ordner und Collective auswählen

```bash
sudo -u www-data php /var/www/nextcloud/occ groupfolders:list
```

In der Web-UI einen Team-Ordner und ein **neues Collective** anlegen oder die
gewünschten bestehenden Team-Ordner-Zugriffsrechte prüfen. Für einen erneuten Import
nicht blind das alte Collective löschen: Erst neues Collective erfolgreich prüfen,
dann das alte bewusst archivieren oder löschen.

Für die Dateikopie werden benötigt:

- `--groupfolder-id`: numerische ID aus `groupfolders:list`.
- `--team-mount`: sichtbarer Team-Ordner-Pfad im Konto von `--user`, etwa `Dokumente`;
  **kein** `__groupfolders`-Pfad. Das Skript prüft die Mount-Klasse und Ordner-ID.
- `--destination`: relativer Ziel-Unterordner darin, etwa `PBworks`. Er wird bei
  Bedarf erzeugt. Falls Dateien bereits importiert wurden, auf deren vorhandenen
  Zielordner zeigen.
- `--collective-id`: ID des neu angelegten Collectives. Diese ist nicht die
  Team-folder-ID. In der Admin-/Netzwerkansicht der Collectives-App bzw. ihrer API
  prüfen, nicht aus einem Seitennamen oder einer Datei-ID erraten.

## Dateien kopieren und serverseitig scannen

Zuerst ein Prüflauf:

```bash
sudo -u www-data .venv/bin/python scripts/server.py copy \
  --nextcloud /var/www/nextcloud \
  --bundle /srv/migration/bundle \
  --user USERID \
  --groupfolder-id 7 \
  --team-mount Dokumente \
  --destination PBworks
```

Nach Kontrolle denselben Befehl mit `--apply` wiederholen. Es wird ausschließlich
über Nextclouds Dateisystem-API geschrieben, nicht per `rsync` in das Datenverzeichnis.
Unveränderte bereits vorhandene Dateien werden per SHA-256 erkannt und wiederverwendet;
unterschiedliche vorhandene Inhalte führen zum Abbruch. Nichts wird gelöscht.
Neue Dateien erhalten den ursprünglichen mtime (Sekundengenauigkeit); bestehende
identische Dateien bleiben unverändert. Alle neuen Ziele werden nach dem Schreiben
erneut per SHA-256 verifiziert.

Der Schritt erzeugt `files-index.json` mit Datei-IDs und führt danach
`occ groupfolders:scan 7` aus. Bei einem Scanfehler kann der Scan separat wiederholt
werden:

```bash
sudo -u www-data .venv/bin/python scripts/server.py scan \
  --nextcloud /var/www/nextcloud --groupfolder-id 7 --apply
```

Die Mount-Prüfung verwendet die
[GroupMountPoint-API](https://github.com/nextcloud/groupfolders/blob/master/lib/Mount/GroupMountPoint.php).
Der PHP-Adapter benötigt daher einen kompatiblen Nextcloud-/Team-folders-Stand;
den Prüflauf immer zuerst ausführen. Ein Live-Test gegen deinen Server ist durch
die lokalen Tests nicht ersetzt.

## Dateilinks mit den tatsächlichen IDs finalisieren

```bash
sudo -u www-data .venv/bin/python scripts/finalize.py /srv/migration/bundle \
  --index /srv/migration/bundle/files-index.json \
  --base-url https://cloud.example.org
```

Dies erzeugt `import/` als separate Kopie der Markdown-Seiten. Normale Dateilinks
werden auf die Dateien im Team-Ordner umgestellt, Wiki-Links und Bildreferenzen
bleiben bis zum Import relativ. `media/` liegt weiterhin direkt neben `import/`.
`finalized.json` hält die Prüfsummen der fertigen Seiten fest.

Die vollständige Cloud-Basis-URL inklusive etwaigem Unterverzeichnis verwenden.
Die IDs müssen von **derselben** Nextcloud-Instanz stammen. Die späteren Links
vergeben keine zusätzlichen Zugriffsrechte; Collective-Leser müssen auch Rechte
auf die referenzierten Team-Dateien haben.

## Importer patchen

```bash
sudo -u www-data .venv/bin/python scripts/server.py patch \
  --nextcloud /var/www/nextcloud \
  --app-dir /var/www/nextcloud/custom_apps/collectives
```

Nach erfolgreicher Vorschau mit `--apply` wiederholen. Der Helfer akzeptiert nur
bekannte SHA-256-Stände aus `patches/versions.json`, prüft den Patch ohne Fuzz,
legt eine Original-Sicherung an und führt `php -l` aus. Bei Fehlern wird die
Originaldatei zurückkopiert. Bereits installierte exakte Patches werden erkannt.

Die zwei Patches basieren auf den Importer-Dateien von
[Collectives 4.7.1](https://github.com/nextcloud/collectives/blob/v4.7.1/lib/Service/ImportService.php)
und [4.8.0](https://github.com/nextcloud/collectives/blob/v4.8.0/lib/Service/ImportService.php);
der ältere Datei-Hash wurde auch gegen 4.5.0 und 4.6.1 geprüft. Entscheidend ist
der Datei-Hash, **nicht** nur die angezeigte App-Version.

Der Patch:

- dekodiert Linkziele genau einmal und löst sie relativ zur Quellseite auf;
- verwendet die Zuordnung der tatsächlich importierten Quelldateien zu Seite-IDs;
- verhindert eine falsche Zuordnung zu gleichnamigen Seiten am Wurzelverzeichnis;
- lässt lokale `#Anker` unverändert und erhält Anker an Seitenlinks;
- unterstützt Inline-Links mit Titel und Referenzdefinitionen;
- schützt Codeblöcke, Inline-Code und HTML-Blöcke vor Linkersetzungen;
- importiert die Index-Readme eines Unterordners nur einmal;
- löst eingebettete Bilder relativ zur Seite auf, erlaubt den Geschwisterbaum
  `media/` und kodiert die resultierenden Attachment-URLs;
- protokolliert nicht auflösbare Links ohne `-v`.

Diese Variante ist für die **hier erzeugten PBworks-Bundles** gedacht, nicht als
universeller Dokuwiki-Importer. Geänderte App-Dateien können Nextclouds
Integritätsprüfung auslösen; App-Updates können den Patch ersetzen. Während der
Migration keine Collectives-Updates durchführen.

## Collective importieren

```bash
sudo -u www-data .venv/bin/python scripts/server.py import \
  --nextcloud /var/www/nextcloud \
  --app-dir /var/www/nextcloud/custom_apps/collectives \
  --bundle /srv/migration/bundle \
  --user USERID \
  --collective-id 23
```

Die Vorschau prüft Bundle, finale Prüfsummen, Patch und Anzahl vorhandener Seiten
im Collective. Ein frisches Collective besitzt nur seine Startseite; **deren Inhalt
wird nicht als Leerheitsnachweis ausgewertet**. Die Ziel-ID deshalb selbst prüfen.

Nach Kontrolle denselben Befehl mit **`--apply --confirm-new-collective`** wiederholen.
Die Speichervorgabe ist `2G`; bei Bedarf `--memory-limit 4G` angeben.
Der eigentliche Befehl ist `occ collectives:import:markdown -v -c ID -u USER import/`,
wie in der [Collectives-Administrationsdokumentation](https://docs.nextcloud.com/server/stable/admin_manual/collectives/index.html)
beschrieben.

`import.log` enthält stdout und stderr. Fehlerzeilen führen auch dann zu einem
Fehlerstatus des Wrappers, wenn `occ` selbst Exit-Code 0 liefert. Bereits **vor**
dem Start wird `import-started.json` geschrieben. Ein Abbruch kann teilweise
importierte Seiten hinterlassen; automatisches Wiederholen wird daher blockiert.
Nicht einfach die Markierung löschen und erneut in dasselbe Collective importieren.
Für einen erneuten Versuch ein neues Collective und ein frisch erzeugtes Bundle
verwenden; bei identischen Team-Dateien wird nichts doppelt hochgeladen.

### Abschlusskontrolle und Restore

Seitenzahl, Stichproben mit Umlauten/Leerzeichen, Unterordner-Links, Bilder,
PDF-Links, Tabellen und Berechtigungen mit einem normalen Mitglied prüfen.
Danach die Originaldatei wiederherstellen:

```bash
sudo -u www-data .venv/bin/python scripts/server.py restore \
  --nextcloud /var/www/nextcloud \
  --app-dir /var/www/nextcloud/custom_apps/collectives \
  --apply
```

Restore bricht ab, wenn die Importer-Datei zwischenzeitlich verändert wurde, statt
ein App-Update zu überschreiben. Die importierten Inhalte bleiben bestehen.

## Grenzen und Sicherheit

- Kein Zugriff auf produktive Systeme wurde beim Erstellen dieses Repositorys
  ausgeführt. Die PHP-Serveradapter sind syntaktisch geprüft, aber nicht auf deiner
  Nextcloud-Installation integrationstestet.
- Die automatischen Tests prüfen lokale Konvertierung und Importer-Logik mit
  nachgebildeten Nextcloud-Klassen und echtem CommonMark-Parser.
- Keine globale Transaktion: Dateikopie oder Import kann teilweise abgeschlossen
  sein. Log und Zielbestand vor Wiederholung prüfen. Unterschiedliche vorhandene
  Dateien müssen manuell geklärt werden.
- Arbeitskopien und lokale Markdown-Dateien behalten mtime. Der Collective-Importer
  erstellt serverseitig neue Seiten; deren ursprünglichen HTML-mtime stellt dieser
  Ablauf **nicht** wieder her.
- Der Importer lädt Anhänge in den Speicher. Sehr große eingebettete Dateien
  benötigen ausreichend PHP-Speicher. Normale Team-Dateien werden gestreamt.
- Referenzlinks mit ungewöhnlichen Zeilenumbrüchen/Markdown-Escapes sind nicht
  vollständig abgedeckt; primäre Eingabe ist der vom Konverter erzeugte Markdown.
- HTML-Text wird nicht „bereinigt“, indem kaputte Bytes ersetzt werden. Ungültiges
  UTF-8 führt zum Abbruch.
- Ein NFC-normalisierendes Dateisystem auf macOS kann Namen anders darstellen als
  Linux. Deshalb den Bundle-Check nach dem Transfer auch auf dem Server ausführen.
- Das Bundle enthält auch die normalisierten HTML- und Dateikopien. Nach erfolgreich
  überprüfter Migration gemäß dem eigenen Datenschutz-/Backup-Verfahren archivieren.

## Tests und zusätzliche Werkzeuge

```bash
PYTHONPATH=scripts uv run python -m unittest discover -s tests -v
bash tests/run_php_tests.sh
```

Der PHP-Test lädt Collectives 4.8.0 und `league/commonmark` in ein temporäres
Verzeichnis. Er benötigt Git, Composer und PHP mit mbstring; er kontaktiert
**keinen Nextcloud-Server**. GitHub Actions führt beide Tests aus.

Die ursprünglichen Einzelwerkzeuge sind unter `scripts/` weiterhin vorhanden:
`convert_wiki.py`, `normalize_filenames.py`, `remove_front_matter.py`.
Für den hier beschriebenen Gesamtprozess ist **kein** separates In-place-
Normalisieren oder Entfernen von Frontmatter erforderlich.

## Lizenz und Herkunft

AGPL-3.0-or-later, siehe `LICENSE`. Die Importer-Patches verändern AGPL-lizenzierten
Code von Nextcloud GmbH und Mitwirkenden; deren Copyright-Hinweise bleiben erhalten.
Die Migration basiert auf den in diesem Projekt entwickelten PBworks-Konvertierungs-
und Normalisierungswerkzeugen. Nur synthetische Testdaten gehören ins Repository.

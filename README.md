# Bitvavo DCA Bot

Ein kleiner Docker-Dienst, der ein konfigurierbares Asset (standardmäßig BTC) gegen eine konfigurierbare Quote-Währung (standardmäßig EUR) kauft. Er liest Bitvavos aktuelle Mindestorder-Regeln und den aktuellen Preis und verteilt die ausstehende Monatsrate auf die verbleibende Zeit bis zur nächsten Einzahlung.

## Wie die Kaufplanung funktioniert

- Der Bot berechnet die kleinste zulässige Market-Order aus `minOrderInQuoteAsset` und `minOrderInBaseAsset` des Bitvavo-Markts.
- `BUY_AMOUNT` legt die gewünschte Menge des Basis-Assets je Kauf fest. `0` bedeutet automatische Mindestmenge. Liegt der konfigurierte Wert unter dem tatsächlichen Bitvavo-Minimum, verwendet der Bot mindestens die zulässige Menge und rundet auf die erlaubte Stellenzahl auf.
- Er berücksichtigt nur Guthaben in der Quote-Währung, das auf Bitvavo verfügbar ist (im Live-Modus), und überschreitet niemals das konfigurierte Monatsbudget abzüglich Gebührenpuffer.
- Aus verfügbarem Budget, Mindestorderwert und verbleibender Zeit bis zum nächsten Einzahlungstag wird das nächste Intervall ermittelt. Der Wert wird nach jedem Kauf anhand des aktuellen Guthabens und Marktpreises neu berechnet.
- `state/state.json` hält die bereits verbrauchte Monatsrate fest. Das Verzeichnis muss dauerhaft gespeichert werden.
- Bei einer unklaren API-Antwort bleibt die Order-ID in `state.json` stehen. Der Bot prüft diese Order zuerst bei Bitvavo und kauft bis zur Klärung nicht erneut. Falls Bitvavo sie nicht auflösen kann, überprüfe die Order im Bitvavo-Konto und entferne `pending_order` aus der Zustandsdatei erst, nachdem du das Ergebnis geklärt hast.
- Im Dry-Run werden keine Orders gesendet. Zum Schutz startet der Bot standardmäßig nicht mit echten Käufen.

`MONTHLY_BUDGET` ist die erwartete monatliche Einzahlung und zugleich die Ausgabengrenze pro Einzahlungzyklus. `DEPOSIT_DAY` legt den Tag des Monats fest (1–31); `TIMEZONE` bestimmt, wann der neue Zyklus beginnt (Standard `Europe/Berlin`). Gibt es den Tag in einem Monat nicht, gilt dessen letzter Kalendertag. Im Live-Modus prüft der Bot zusätzlich das tatsächlich verfügbare Quote-Guthaben auf Bitvavo. Nach einem Zykluswechsel wartet er somit auf die tatsächliche Einzahlung, falls noch nicht genug Guthaben angekommen ist.

## Starten

```sh
cp .env.example .env
```

Trage in `.env` `MONTHLY_BUDGET`, `DEPOSIT_DAY`, `BUY_AMOUNT` (Menge pro Kauf in `BASE_ASSET`), `QUOTE_CURRENCY` (z. B. `EUR` oder `USD`), `BASE_ASSET` (z. B. `BTC`) sowie Bitvavo-API-Zugangsdaten ein. Mit `BUY_AMOUNT=0` wird die jeweils minimale erlaubte Menge gekauft. Starte zunächst mit `LIVE_TRADING=false` und prüfe die Container-Logs.

Für echte Käufe muss `LIVE_TRADING=true` gesetzt werden. Erstelle dafür einen Bitvavo-API-Key mit **View access** und **Trade digital assets**. **Withdraw digital assets darf nicht aktiviert sein.** Wenn möglich, begrenze den Schlüssel in Bitvavo auf die feste IP-Adresse des Hosts. API-Key und Secret nur als Umgebungsvariablen/Secrets bereitstellen und niemals committen.

```sh
docker compose up -d --build
docker compose logs -f
```

`CHECK_INTERVAL_SECONDS` legt fest, wie oft der Bot Guthaben und Marktregeln prüft (Minimum 60 Sekunden). Das ist die Prüfungsfrequenz; der Bot kauft erst, wenn der dynamisch berechnete nächste Kaufzeitpunkt erreicht ist. `FEE_BUFFER_PERCENT` lässt einen Anteil des Budgets für Gebühren frei.

## GitHub / GitHub Container Registry

Das Image kann mit `docker build -t bitvavo-dca .` lokal gebaut werden. Für private oder öffentliche Veröffentlichung auf GHCR sollte ein GitHub Actions Workflow das Image bauen und mit `GITHUB_TOKEN` in `ghcr.io/<owner>/<repository>` veröffentlichen. Zugangsdaten gehören in die Runtime-Umgebung des Servers, niemals in das Image oder Repository.

## Grenzen

Market-Orders sind nicht preisgarantiert; der tatsächliche Ausführungspreis und Gebühren können abweichen. Bei einer festen `BUY_AMOUNT` ist der geplante Quote-Gegenwert daher eine Schätzung anhand des letzten Preises. Beobachte die Logs und halte den Host bzw. seine IP stabil, falls du IP-Whitelisting nutzt. Das Projekt ist ein technisches Beispiel und keine Anlageberatung.

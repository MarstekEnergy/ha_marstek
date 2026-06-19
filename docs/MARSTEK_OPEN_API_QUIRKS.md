# Marstek Open API – bekannte Glitches & Integrations-Entscheidungen

Diese Datei sammelt **Firmware- und API-Eigenheiten**, die beim Entwickeln der
`ha_marstek`-Integration immer wieder zu falschen Annahmen führen. Stand: Juni 2026,
primär Erfahrungen mit **Venus D**; viele Punkte gelten auch für Venus A und andere
Open-API-Geräte.

**Ziel:** Nicht noch einmal dieselben Bugs einbauen (z. B. PV1 pauschal ÷10,
`ES.pv_power` als Solarleistung, vollständiger Rollback bei Null-Frames).

Referenzen: [Marstek Open API PDF](https://static-eu.marstekcloud.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf),
[jaapp/ha-marstek-local-api](https://github.com/jaapp/ha-marstek-local-api),
ioBroker/Domoticz-Community, eigene Venus-D-Feldtests.

---

## 1. UDP / Transport (generell)

| Problem | Symptom | Was wir tun / Regel |
|--------|---------|---------------------|
| **Unzuverlässige Antworten** | Einzelne Requests timeouten; Antwort kommt spät oder gar nicht | Bounded Retries; Zyklus startet mit `dict(self.data)` – partielle Fehler überschreiben nicht alles |
| **Unvollständige Frames** | Nicht alle Felder in jedem Paket; fehlende Keys | Vorherigen Snapshot behalten; `py-marstek` setzt fehlende PV-Felder auf **0** (nicht `null`) |
| **„Suspicious zero“-Frames** | Plötzlich ≥85 % aller Leistungsfelder = 0, obwohl vorher aktiv | Selektiver Merge (`_merge_after_suspicious_zero`): SOC/Modus/Energie vom alten Snapshot, **Leistungsfelder vom aktuellen Frame** (auch 0) |
| **API-Burst stört Gerät** | Nach HA-Reload bricht Einspeisung/MPPT ab | Startup-Delay (Default 45 s); kein Discovery-Burst beim Reload; Pause zwischen Requests (Default 4 s) |
| **Zu schnelles Polling** | WLAN/Gerät überlastet, mehr Glitches | Default Scan 60 s; konfigurierbar; nicht mehrere schwere Calls parallel feuern |

**Anti-Pattern:** Bei transientem 0-Frame den **gesamten** Snapshot verwerfen und den letzten Export-Stunden lang festhalten.

---

## 2. PV.GetStatus – Solar / MPPT

### 2.1 PV1-Leistung in Deziwatt (×10)

| Feld | Venus D/A (typisch) | PV2–PV4 |
|------|---------------------|---------|
| `pvN_power` | Kanal 1 oft **Deziwatt** (÷10 für Watt) | Watt |
| `pvN_voltage` | Volt | Volt |
| `pvN_current` | **oft 0** trotz Leistung | meist plausibel |

**Symptom:** PV1 ≈ 10× zu hoch (z. B. 10 410 W statt ~1 040 W); Total PV summiert falsch (~12 kW).

**Regel:** PV1 **nicht pauschal** ÷10 (korrigierte Firmware existiert). Korrektur **nur über Watt-Vergleich mit PV2–PV4**:

1. Mindestens ein Kanal PV2–PV4 mit `power > 0`
2. `pv1_power / Durchschnitt(PV2–PV4)` liegt zwischen **5× und 25×**
3. `pv1_power / 10` ist näher am PV2–PV4-Durchschnitt als der Rohwert → ÷10

**Spannung und Strom werden ignoriert** – auf Venus D/A sind die A-Werte für PV1 unzuverlässig; PV2–PV4-Watt sind der Referenzmaßstab.

**Einschränkung:** Läuft nur PV1 allein (morgens, andere Kanäle noch 0), gibt es keine Referenz → PV1 bleibt unskaliert bis PV2+ aktiv wird.

**Anti-Pattern:**

- Total PV aus `ES.GetStatus.pv_power` ableiten
- PV1 aus `voltage × current` berechnen oder korrigieren
- Mehrere konkurrierende Heuristiken (V×I, impliziter Strom, …) – verwirrt nur

### 2.2 PV-Strom / -Spannung (nur Anzeige)

| Beobachtung | Konsequenz |
|-------------|------------|
| `pv1_current` oft **0** oder inkonsistent zu `pv1_power` | **Nicht** für PV1-Skalierung verwenden |
| PV2–PV4 Watt zuverlässig | Einzige Referenz für PV1-Korrektur |
| V/A-Sensoren weiter anzeigen | Nur Diagnose; keine Logik daran koppeln |

### 2.3 Total PV Input Power (HA-Sensor)

| Quelle | Zuverlässigkeit Venus D/A |
|--------|---------------------------|
| Summe `pv1_power`…`pv4_power` (nach PV1-Skalierung) | **Ja** – bevorzugt |
| `ES.GetStatus.pv_power` | **Nein** – oft dauerhaft 0 |

Separater Diagnose-Sensor: **PV Power (ES)** = Rohfeld `pv_power`.

### 2.4 PV.GetStatus fällt aus

Wenn alle Spannungen im aktuellen Frame 0 sind, aber vorher PV aktiv war: vorherigen PV-Snapshot behalten (`_restore_previous_pv_if_missing`). Kategorie `pv` ggf. frisch halten, wenn retained Spannung > 0.

---

## 3. ES.GetStatus – Energiesystem

| Feld | Problem | Integrations-Umgang |
|------|---------|---------------------|
| `pv_power` | Auf Venus D/A oft **0** oder unbrauchbar | Nicht für Total PV; eigener Roh-Sensor |
| `ongrid_power` | **Vorzeichen je Gerät unterschiedlich** (s. u.) | `grid_export_power_w()` mit Geräteprofil |
| `ongrid_power` | Kurz **0** während laufender Einspeisung | 1 Poll Haltezeit (`_preserve_ongrid_if_zero_glitch`) |
| `ongrid_power` | Stundenlang **0** bei leerem Speicher / kein Export | Legitim 0 anzeigen – **nicht** alten Export festhalten |
| `bat_power` | Auf manchen Firmwares falsch skaliert (Community/evcc: Faktor ~10) | Battery Status primär aus `bat_power` + `ongrid`; nicht blind vertrauen |

### 3.1 Grid Power / Export – Vorzeichen-Konvention

| Gerätetyp | Export (Einspeisung) | Import (Bezug) |
|-----------|----------------------|----------------|
| jaapp / viele Geräte | `ongrid_power` **negativ** | positiv |
| **Venus D / Venus A** | `ongrid_power` **positiv** | negativ oder 0 |

**HA-Sensor „Grid Power“:** nur **nicht-negative** Export-Watt (`grid_export_power_w`).

**Anti-Pattern:** Venus D mit jaapp-Konvention (negativ = Export) behandeln → dauerhaft 0 W.

---

## 4. ES.GetMode – Betriebsmodus

| Problem | Symptom | Umgang |
|---------|---------|--------|
| `parse_es_mode_response` leitet `battery_status` nur aus `ongrid_power` ab | Flapping Idle/Selling bei UDP-Glitches | Eigenes `_update_battery_status` aus `bat_power` + frischem `ongrid_power` |
| `battery_power` im Parser = `abs(ongrid_power)` | **Nicht** die echte Batterieleistung | Grid Power liest `ongrid_power`, nicht `battery_power` |
| `ongrid_power` in GetMode vs. GetStatus weicht ab | GetStatus = 0, GetMode noch Export | `_ongrid_power_mode` speichern; Fallback für Grid Power wenn GetStatus 0 meldet |

**Anti-Pattern:** Nach HA-Reload sofort `ES.SetMode` oder Discovery-Flut – kann Einspeisung unterbrechen. Schreibzugriffe nur bei expliziter User-/Automations-Aktion loggen.

---

## 5. HA-Integration – Verhalten bei Ausfällen

| Mechanismus | Zweck |
|-------------|--------|
| Kategorie-Frische (`es`, `pv`, `energy`) | Kurz kein Update → Wert `unknown`, Entity bleibt `available` |
| `unavailable_after_seconds` (Default 600 s) | Erst nach langer Stille komplett `unavailable` |
| `UpdateFailed` nur beim ersten Setup ohne Antwort | Kein Flapping nach erstem erfolgreichen Poll |
| Startup-Delay | Gerät nach HA-Start nicht sofort mit UDP überfahren |

---

## 6. Entwickler-Checkliste (vor jedem PV/Grid-Fix)

- [ ] Wird **PV1** nur gegen **PV2–PV4-Watt** skaliert (nicht pauschal ÷10, nicht über V/A)?
- [ ] Läuft PV1-Skalierung **nach** Restore/Merge am **Ende** des Zyklus?
- [ ] Kommt **Total PV** aus der **Kanalsumme**, nicht aus `ES.pv_power`?
- [ ] Ist **Venus D/A** positive `ongrid` für Export berücksichtigt?
- [ ] Verhindert suspicious-zero-Merge **kein** legitimes Grid-0 (leerer Speicher)?
- [ ] Gibt es **keinen** vollständigen Snapshot-Rollback für Leistungsfelder?
- [ ] Sind API-Calls beim Reload **gedrosselt** (Delay, kein Extra-Burst)?
- [ ] Wurde getestet mit **mehreren aktiven MPPT-Kanälen** (Ratio > 17)?

---

## 7. Relevante Code-Stellen

| Thema | Datei | Funktion / Sensor |
|-------|-------|-------------------|
| PV1-Skalierung | `coordinator.py` | `_scale_pv1_power_if_needed` |
| Total PV | `sensor.py` | `MarstekTotalPVPowerSensor` |
| Grid Export | `coordinator.py` / `sensor.py` | `grid_export_power_w`, `MarstekPowerSensor` |
| Null-Frame | `coordinator.py` | `_is_suspicious_zero_snapshot`, `_merge_after_suspicious_zero` |
| ongrid-Glitch | `coordinator.py` | `_preserve_ongrid_if_zero_glitch` |
| Battery Status | `coordinator.py` | `_update_battery_status` |
| Polling-Defaults | `const.py` | `DEFAULT_REQUEST_DELAY`, `DEFAULT_STARTUP_DELAY`, … |

---

## 8. Changelog dieser Erkenntnisse (Kurz)

| Datum | Erkenntnis |
|-------|------------|
| 2026-04 | PV1 Deziwatt; Strom oft 0 |
| 2026-04 | `ES.pv_power` auf Venus D unbrauchbar für Solar |
| 2026-04 | Venus D: positives `ongrid` = Export |
| 2026-06 | Suspicious-zero: selektiver Merge statt Voll-Rollback |
| 2026-06 | Grid Power darf bei leerem Speicher auf 0 fallen |
| 2026-06 | PV1-Ratio-Obergrenze 17× zu niedrig bei 3+ MPPT-Kanälen |
| 2026-06 | GetMode-`ongrid` als Fallback wenn GetStatus 0 |

**Bei neuen Firmware-Versionen diese Datei aktualisieren**, sobald ein Verhalten bestätigt oder widerlegt ist.

# Marstek Open API – Known Glitches & Integration Decisions

This document collects **firmware and API quirks** that repeatedly lead to wrong
assumptions when developing the `ha_marstek` integration. Last updated: June 2026,
primarily based on **Venus D** experience; many points also apply to Venus A and other
Open API devices.

**Goal:** Avoid reintroducing the same bugs (e.g. always dividing PV1 by 10,
using `ES.pv_power` as solar output, full rollback on zero frames).

References: [Marstek Open API PDF](https://static-eu.marstekcloud.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf),
[jaapp/ha-marstek-local-api](https://github.com/jaapp/ha-marstek-local-api),
ioBroker/Domoticz community, and our own Venus D field tests.

---

## 1. UDP / Transport (general)

| Problem | Symptom | What we do / rule |
|--------|---------|-------------------|
| **Unreliable responses** | Individual requests time out; response arrives late or not at all | Bounded retries; each cycle starts with `dict(self.data)` – partial failures do not overwrite everything |
| **Incomplete frames** | Not all fields in every packet; missing keys | Keep previous snapshot; `py-marstek` sets missing PV fields to **0** (not `null`) |
| **“Suspicious zero” frames** | Suddenly ≥85% of all power fields = 0 despite prior activity | Selective merge (`_merge_after_suspicious_zero`): SOC/mode/energy from old snapshot, **power fields from current frame** (including 0) |
| **API burst disturbs device** | After HA reload, feed-in/MPPT stops | Startup delay (default 45 s); no discovery burst on reload; pause between requests (default 4 s) |
| **Polling too fast** | Wi‑Fi/device overloaded, more glitches | Default scan 60 s; configurable; do not fire multiple heavy calls in parallel |

**Anti-pattern:** On a transient zero frame, discard the **entire** snapshot and pin the last export value for hours.

---

## 2. PV.GetStatus – Solar / MPPT

### 2.1 PV1 power in deciwatts (×10)

| Field | Venus D/A (typical) | PV2–PV4 |
|------|---------------------|---------|
| `pvN_power` | Channel 1 often **deciwatts** (÷10 for watts) | watts |
| `pvN_voltage` | volts | volts |
| `pvN_current` | **often 0** despite power | usually plausible |

**Symptom:** PV1 ≈ 10× too high (e.g. 10,410 W instead of ~1,040 W); Total PV sums incorrectly (~12 kW).

**Rule:** Do **not** always divide PV1 by 10 (corrected firmware exists). Correct **only via watt comparison with PV2–PV4**:

1. At least one PV2–PV4 channel with `power > 0`
2. `pv1_power / average(PV2–PV4)` is between **5× and 25×**
3. `pv1_power / 10` is closer to the PV2–PV4 average than the raw value → ÷10

**Voltage and current are ignored** – on Venus D/A, amp readings for PV1 are unreliable; PV2–PV4 watts are the reference.

**Limitation:** If only PV1 is active or the other channels have very low/zero power (early morning or evening at low irradiance), there is no reliable reference → PV1 may stay unscaled (or show wrong value) until multiple channels have decent positive power. This is the most common reason for "PV1 plötzlich falsch skaliert" reports.

**Anti-patterns:**

- Deriving Total PV from `ES.GetStatus.pv_power`
- Calculating or correcting PV1 from `voltage × current`
- Multiple competing heuristics (V×I, implied current, …) – adds confusion only

### 2.2 PV current / voltage (display only)

| Observation | Consequence |
|-------------|------------|
| `pv1_current` often **0** or inconsistent with `pv1_power` | Do **not** use for PV1 scaling |
| PV2–PV4 watts reliable | Only reference for PV1 correction |
| Keep showing V/A sensors | Diagnostics only; do not couple logic to them |

### 2.3 Total PV Input Power (HA sensor)

| Source | Reliability on Venus D/A |
|--------|--------------------------|
| Sum of `pv1_power`…`pv4_power` (after PV1 scaling) | **Yes** – preferred |
| `ES.GetStatus.pv_power` | **No** – often stuck at 0 |

Separate diagnostic sensor: **PV Power (ES)** = raw `pv_power` field.

### 2.4 PV.GetStatus fails

If all voltages in the current frame are 0 but PV was active before: keep the previous PV snapshot (`_restore_previous_pv_if_missing`). Keep category `pv` fresh if retained voltage > 0.

---

## 3. ES.GetStatus – Energy system

| Field | Problem | Integration handling |
|------|---------|---------------------|
| `pv_power` | On Venus D/A often **0** or unusable | Not used for Total PV; separate raw sensor |
| `ongrid_power` | **Sign convention varies by device** (see below) | `grid_export_power_w()` with device profile |
| `ongrid_power` | Briefly **0** during active export | Hold for 1 poll (`_preserve_ongrid_if_zero_glitch`) |
| `ongrid_power` | **0 for hours** with empty battery / no export | Show legitimate 0 – do **not** pin old export |
| `bat_power` | Wrong scale on some firmware (community/evcc: ~10× factor) | Battery status mainly from `bat_power` + `ongrid`; do not trust blindly |

### 3.1 Grid Power / export – sign convention

| Device type | Export (feed-in) | Import (draw) |
|-------------|------------------|---------------|
| jaapp / many devices | `ongrid_power` **negative** | positive |
| **Venus D / Venus A** | `ongrid_power` **positive** | negative or 0 |

**HA sensor “Grid Power”:** **non-negative** export watts only (`grid_export_power_w`).

**Anti-pattern:** Treating Venus D with the jaapp convention (negative = export) → stuck at 0 W.

---

## 4. ES.GetMode – Operating mode

| Problem | Symptom | Handling |
|---------|---------|----------|
| `parse_es_mode_response` derives `battery_status` from `ongrid_power` only | Idle/Selling flapping on UDP glitches | Custom `_update_battery_status` from `bat_power` + fresh `ongrid_power` |
| `battery_power` in parser = `abs(ongrid_power)` | **Not** real battery power | Grid Power reads `ongrid_power`, not `battery_power` |
| `ongrid_power` in GetMode vs. GetStatus diverges | GetStatus = 0, GetMode still shows export | Store `_ongrid_power_mode`; fallback for Grid Power when GetStatus reports 0 |

**Anti-pattern:** Calling `ES.SetMode` or discovery flood immediately after HA reload – can interrupt feed-in. Log writes only on explicit user/automation actions.

---

## 5. HA integration – outage behaviour

| Mechanism | Purpose |
|-----------|---------|
| Category freshness (`es`, `pv`, `energy`) | Short gap without update → value `unknown`, entity stays `available` |
| `unavailable_after_seconds` (default 600 s) | Fully `unavailable` only after long silence |
| `UpdateFailed` only on first setup with no response | No flapping after first successful poll |
| Startup delay | Do not hammer device with UDP immediately after HA start |

---

## 6. Developer checklist (before any PV/Grid change)

- [ ] Is **PV1** scaled only against **PV2–PV4 watts** (not always ÷10, not via V/A)?
- [ ] Does PV1 scaling run **after** restore/merge at the **end** of the cycle?
- [ ] Does **Total PV** come from the **channel sum**, not `ES.pv_power`?
- [ ] Is **Venus D/A positive `ongrid`** handled for export?
- [ ] Does suspicious-zero merge **not** block legitimate grid 0 (empty battery)?
- [ ] Is there **no** full snapshot rollback for power fields?
- [ ] Are API calls on reload **throttled** (delay, no extra burst)?
- [ ] Tested with **multiple active MPPT channels** (ratio > 17)?

---

## 7. Relevant code locations

| Topic | File | Function / sensor |
|-------|------|-------------------|
| PV1 scaling | `coordinator.py` | `_scale_pv1_power_if_needed` |
| Total PV | `sensor.py` | `MarstekTotalPVPowerSensor` |
| Grid export | `coordinator.py` / `sensor.py` | `grid_export_power_w`, `MarstekPowerSensor` |
| Zero frame | `coordinator.py` | `_is_suspicious_zero_snapshot`, `_merge_after_suspicious_zero` |
| ongrid glitch | `coordinator.py` | `_preserve_ongrid_if_zero_glitch` |
| Battery status | `coordinator.py` | `_update_battery_status` |
| Polling defaults | `const.py` | `DEFAULT_REQUEST_DELAY`, `DEFAULT_STARTUP_DELAY`, … |

---

## 8. Changelog of findings (short)

| Date | Finding |
|------|---------|
| 2026-04 | PV1 deciwatts; current often 0 |
| 2026-04 | `ES.pv_power` unusable for solar on Venus D |
| 2026-04 | Venus D: positive `ongrid` = export |
| 2026-06 | Suspicious-zero: selective merge instead of full rollback |
| 2026-06 | Grid Power may show 0 with empty battery |
| 2026-06 | PV1 ratio upper bound 17× too low with 3+ MPPT channels |
| 2026-06 | GetMode `ongrid` fallback when GetStatus is 0 |

**Update this file when new firmware versions confirm or disprove a behaviour.**

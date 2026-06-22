# B1 Runtime Config — Design Spec (janus_camera_page)

**Status:** DESIGN ONLY. No implementation, no apply engine, no admin UI, no live mutation.
**Scope:** typed runtime-config schema + validation model + diff model + apply-impact *classification* (not apply) + a read-only effective-config endpoint contract + unit-test list + explicit non-goals.
**Date:** 2026-06-18 · **Target module:** `app/config/runtime_schema.py`

> Produced by a grounding workflow (5 read-only verification agents → synthesis →
> adversarial review → corrections). Every field/impact/endpoint is grounded
> against the real code; §10 lists every correction vs the original draft and the
> open questions needing an operator decision.

---

## 1. Purpose & Scope

### 1.1 Purpose
Define a single, typed, versioned **runtime-config schema** for janus_camera_page that:
- Names the operator-tunable surface of the camera-control backend (L4) with explicit field SOURCES in the current code.
- Provides a **validation model** that rejects unsafe or unsupported configurations *before* they could ever be applied.
- Provides a **diff model** (proposed vs effective) and an **apply-impact classification** so a future B2 apply engine knows the blast radius of each change.
- Exposes a **read-only effective-config endpoint** and a **dry-run validate endpoint** — and nothing that mutates state.

### 1.2 In scope (B1)
- `RuntimeConfig` / `WebRtcRuntimeConfig` / `StreamRuntimeConfig` / `DiagnosticsRuntimeConfig` pydantic models.
- Pure-function validators (no I/O side effects beyond *reading* current sources).
- `ApplyImpact` enum + a grounded change→impact mapping table (classification only).
- `GET …/runtime-config/effective` (read-only) and `POST …/runtime-config/validate` (dry-run) endpoint *contracts*.
- Unit-test list.

### 1.3 Explicit Non-Goals (B1 does NOT)
1. Apply, write, or persist any config (no `POST …/apply`, no env writes, no `sensor_allocations.json` mutation, no `jcfg` re-render).
2. Restart any service (`encoder-admin`, `systemctl`, Janus, relay).
3. Write or version config revisions / history.
4. Expose any secret in `SENSITIVE_KEYS` (plaintext or reveal flow).
5. Modify firewall rules, bind ports, or Janus listen/admin addresses.
6. Replace or edit systemd-IaC, `firewall-*.sh`, or `network_defaults.py`.
7. Re-architect always-on or safety-gated subsystems (FDIR, telemetry ingestion, watchdog reboot) into runtime toggles.

> **Grounding note (scope vs draft):** The draft modeled several subsystems as runtime-tunable that are **always-on, safety-gated, or deployment-only** in code (FDIR, telemetry ingestion, firewall, viewer tokens, watchdog reboot). These are demoted to read-only/derived or dropped — see §10.

---

## 2. Prerequisite: Canonical Sensor Identity (FDIR-KEY-001)

`stream_profiles` is keyed by the **canonical sensor key**, which is the single source of truth post-FDIR-KEY-001.

- **Format:** `"<serial>:<sensor>"` where `serial` matches the device-serial charset `[A-Za-z0-9]+` and `sensor ∈ {color, depth, ir1, ir2}`.
  Source: `mountpoint_allocator._key()` — `f"{serial}:{sensor}"` (mountpoint_allocator.py:92-93).
- **Legacy migration:** `local:color → <serial>:color` is performed one-shot, idempotently, by `migrate_color_key()` (mountpoint_allocator.py:105-125), auto-invoked in `sensor_lifecycle.initialize()` (line 189) and `stop()` (line 292). It is a no-op when `serial == LOCAL_SERIAL` (`"local"`) or the canonical key already exists.
- **B1 rule:** the schema MUST accept only canonical `<serial>:<sensor>` keys and MUST **reject any `local:*` sentinel key** (the legacy `local:color` form, and any other `local:<sensor>`). This prevents the FDIR-KEY-001 heterogeneous-identity regression.

> **Grounding note:** `"local"` is the sentinel serial (`LOCAL_SERIAL`, mountpoint_allocator.py:56), and `local:color` is the *pre-migration* shape. B1 validation treats any `local:*` key as a hard error, not a synonym for a canonical key.

---

## 3. Data Model

Three pydantic models under `app/config/runtime_schema.py`. **Every field carries a SOURCE note** describing where the value is currently read/written. Fields with **no current backend source** are flagged `[NO SOURCE — see §10]` and are *advisory-only in B1* (surfaced in `effective` as `null`/derived, never asserted as live truth).

### 3.1 `RuntimeConfig`
```python
class RuntimeConfig(BaseModel):
    version: Literal[1] = 1
    webrtc: WebRtcRuntimeConfig
    stream_profiles: Dict[str, StreamRuntimeConfig]   # key = "<serial>:<sensor>"
    diagnostics: DiagnosticsRuntimeConfig
```

### 3.2 `WebRtcRuntimeConfig`
| Field | Type / range | Default | SOURCE |
|---|---|---|---|
| `ice_policy` | `Literal["all","relay"]` | `"all"` | **currently** `Settings.ice_policy` ← env `ICE_POLICY` (settings.py:133, default `'all'`). Applied in `get_client_rtc_config()` janus.py:148-154; **conditional**: depth-camera type forces `relay` regardless. |
| `turn_credential_ttl_seconds` | `int [300..3600]` *(B1-introduced policy bound)* | `3600` | **currently** `Settings.turn_cred_ttl` ← env `TURN_CRED_TTL` (settings.py:127, default `3600`). Passed to `generate_turn_credentials(ttl=…)` janus.py:135-142. **Nothing in code enforces a min/max today**; the `300..3600` bound is a B1-introduced security policy (see R4), not existing behavior. |
| `turn_enabled` | `bool` | derived | `[NO SOURCE — see §10]` **read-only/derived**. There is no explicit toggle; TURN is included iff `nat_cfg.turn_type` set and URLs buildable (janus.py:111-146). Surfaced as a *computed* boolean, not a settable field in B1. |
| `stun_enabled` | `bool` | derived (`True`) | `[NO SOURCE — see §10]` **read-only/derived**. STUN is added unconditionally from `nat_cfg.stun_server/stun_port` (janus.py:108-109). No off-switch exists. |
| `max_reconnect_attempts` | `int [3..50]` | **unknown / client-owned** | `[NO SERVER SOURCE]` **client-side only**: `config.js:130` `dataset.maxReconnectAttempts`, clamped `[3,50]`; default is the client constant `AP.Core.MAX_RECONNECT_ATTEMPTS` (value not visible to L4). Not in L4. Advisory passthrough only. |
| `reconnect_base_delay_ms` | `int [100..5000]` | `300` | `[NO SERVER SOURCE]` client-side only: `config.js:118` `dataset.backoffBaseMs`, clamp `[100,5000]`, default `300`. |
| `reconnect_max_delay_ms` | `int [2000..60000]` | `15000` | `[NO SERVER SOURCE]` client-side only: `config.js:120` `dataset.backoffMaxMs`, clamp `[2000,60000]`, default `15000`. |

> **Grounding notes (corrections vs draft):**
> - `ice_policy` default corrected **`relay` → `all`** (settings.py:133 default is `'all'`; `relay` is only forced for depth cameras at request time).
> - `turn_enabled` / `stun_enabled` are **derived/read-only**, not settable booleans (no off-switch in code).
> - `turn_credential_ttl_seconds` bounds `300..3600` are a **B1-introduced policy** (short-lived relay creds), *not* a reflection of current code — nothing enforces a min/max today (see R4).
> - `max_reconnect_attempts` default is **unknown/client-owned** (read from the client constant `AP.Core.MAX_RECONNECT_ATTEMPTS`); B1 does **not** invent a server default. Ranges corrected (`attempts 3..50`, `base 100..5000 default 300`, `max 2000..60000 default 15000`). All three are **client-side only** — B1 carries them as advisory passthrough, classification `DEPLOYMENT_ONLY` (no server apply path).
> - The draft rule "ttl ≤ 3600" is enforced by the field's own `le=3600`; "relay ⇒ turn_enabled" is kept as a cross-field validator (§4 R3) but evaluated against the *derived* TURN availability, since `turn_enabled` is not directly settable.

### 3.3 `StreamRuntimeConfig`
Key into `stream_profiles` is the canonical sensor key (§2). `sensor_key` is **derived from the map key**, not an independent field.

| Field | Type / range | Default | SOURCE |
|---|---|---|---|
| `sensor_key` | `str` `<serial>:<sensor>` | — | derived from map key; `sensor ∈ {color,depth,ir1,ir2}` (mountpoint_allocator.py:92-93). |
| `enabled` | `bool` | from allocation | **currently** `Allocation.desired_active` in `sensor_allocations.json` (mountpoint_allocator.py:68-79). **Not** a top-level flag; toggled via `sensor_lifecycle.initialize()/stop()`. In B1 this is **read-only/derived**; an attempted write is `REJECTED`. |
| `resolution` | `str "WxH"` | per-sensor default | **currently** *color only* via `CameraStreamConfig.width/height` ← `/etc/robot/rs-color.tuning.env` (`WIDTH`,`HEIGHT`) (camera.py:43-44,121-151). depth/IR: defaults at Initialize (`sensor_lifecycle.py:140`), no runtime API. Writes for sensor ≠ color are `REJECTED` (R6b). |
| `fps` | `int` | color `15` | **currently** *color only* `CameraStreamConfig.fps` ← tuning.env `FPS` (camera.py:45). Also de-rated by `thermal.py`. depth/IR default-only; writes for sensor ≠ color are `REJECTED` (R6b). |
| `codec` | `Literal["h264"]` | `"h264"` | **NOT CONFIGURABLE** — hardcoded H.264/x264 (color); depth Z16→RGB, IR Y8. Read-only constant; any write `REJECTED`. |
| `encoder` | `Literal["libx264","h264_v4l2m2m"]` | `"libx264"` | **partial, color only** via `PRESET`/`TUNE` in tuning.env (camera.py:52-53). Not a clean enum in code; modeled but read-only in B1; any write `REJECTED`. |
| `bitrate_kbps` | `int [100..8000]` | color `900`, depth `1000`, ir `800` | **currently** *color only* `CameraStreamConfig.bitrate_kbps` ← tuning.env `BITRATE_KBPS` (camera.py:47); depth/ir defaults `sensor_lifecycle.py:141`. Writes for sensor ≠ color are `REJECTED` (R6b). |
| `gop_frames` | `int [1..300]` *(B1-introduced advisory sanity bound)* | `15` | **currently** *color only* `CameraStreamConfig.gop` ← tuning.env `GOP` (camera.py:48-51). **UNIT = FRAMES, not seconds.** The `1..300` range is a **B1-introduced advisory sanity bound**, not existing behavior (code grounds only the default `15`). Writes for sensor ≠ color are `REJECTED` (R6b). |
| `mountpoint_id` | `int` | color `1305`; depth/ir `1306..1999` | **ALLOCATED, not configured.** `mountpoint_allocator` (color static 1305; dynamic pool). **read-only;** any write `REJECTED`. |
| `rtp_port` | `int` | color `5004`; depth/ir `5006..5099` (step 2) | **ALLOCATED, not configured.** Written to `/etc/robot/rs-<sensor>.contract.env` (`PORT`) by `sensor_lifecycle._write_contract_env`. **read-only;** any write `REJECTED`. |

> **Grounding notes (corrections vs draft):**
> - **`gop_seconds` → `gop_frames`** (UNIT MISMATCH): `GOP` in tuning.env is keyframe interval in **frames** (default 15), not seconds. Duration = `gop_frames / fps`. The `1..300` range is a B1-introduced advisory sanity bound (not code-grounded).
> - `resolution`/`fps`/`encoder`/`gop_frames`/`bitrate` have a **runtime API only for color** (`POST /config`); for depth/IR they are **Initialize-time defaults** with no runtime endpoint. B1 marks depth/IR variants of these fields **read-only** and `REJECTED` on write (R6b).
> - `codec` is a **fixed constant** (no `h264` ↔ alternative selection in code).
> - `enabled` maps to `desired_active`, **not** a config flag — read-only in B1; write `REJECTED`.
> - `mountpoint_id` and `rtp_port` are **allocator outputs**, surfaced read-only; they are *not* operator-set, and edits are `REJECTED`. The draft's "unique mp_id / unique rtp_port" rules become **invariant checks over allocation state** (§4 R8), not write constraints.
> - The draft's fixed resolution/fps enum (`320x240|640x480|848x480|1280x720 @ 5|10|15|30`) **does not exist in code**. Supported color modes are queried *dynamically* via the RealSense SDK catalog through the mux (`realsense_catalog.query_catalog()`); the V4L2 color path is **retired** (permanent color→mux migration). See §4 R6 + §10 OQ-3.

### 3.4 `DiagnosticsRuntimeConfig`
| Field | Type / range | Default | SOURCE |
|---|---|---|---|
| `reboot_allowed` | `bool` | `False` (derived) | `[read-only/derived]` **currently** `Settings.watchdog_reboot_enabled` ← env `CAM_WATCHDOG_REBOOT_ENABLED` (settings.py:161, default `'0'`=False, safe-by-default per P2-REL-002). Checked in `recovery_executor.py:180-194`. **Read at process start**, not at reboot time → see §10 OQ-5. **Safety gate — read-only/derived in B1, never settable** (same treatment as `fdir_enabled`/`telemetry_enabled`); any write `REJECTED`. |
| `health_stream_stale_ms` | `int [500..30000]` | `10000` | **currently** `Settings.watchdog_stale_ms` ← env `CAM_WATCHDOG_STALE_MS` (settings.py:156, default **10000**). Used in `/health/stream`, `/healthz`. |
| `debug_stats_enabled` | `bool` | derived | `[NO SOURCE — see §10]` no server toggle; client `stats_service.js` only. Read-only/advisory. |
| `telemetry_enabled` | `bool` | `True` (derived) | `[NO SOURCE — see §10]` telemetry ingested unconditionally (`routes/telemetry.py`); no opt-out. Read-only constant. |
| `telemetry_interval_seconds` | `int [1..60]` | client `5` | `[NO SERVER SOURCE]` client-side only (`config.js` watchdog/tick datasets). Advisory passthrough. |
| `fdir_enabled` | `bool` | `True` (derived) | `[NO SOURCE — see §10]` FDIR/recovery ladder always-on when imported; no disable flag. Read-only constant. |
| `auto_recovery_enabled` | `bool` | `True` (derived) | `[NO SOURCE — see §10]` recovery escalation automatic; no toggle. Read-only constant. |

> **Grounding notes (corrections vs draft):**
> - `health_stream_stale_ms` default corrected **`5000` → `10000`** (settings.py:156).
> - `telemetry_interval_seconds` default corrected **`5` (server) → client-side only**; range `1..60` kept but advisory.
> - `reboot_allowed`, `debug_stats_enabled`, `telemetry_enabled`, `fdir_enabled`, `auto_recovery_enabled` have **no settable backend toggle** in B1. They are **read-only/derived constants**, never settable; an attempted write is `REJECTED`. `reboot_allowed` is a process-start env read identical in mechanism to the other always-on/env-start fields (settings.py:161 vs 156) — and, being a safety gate, is the *most* important to keep non-settable (see §7/§10 OQ-5).

---

## 4. Validation Rules

Each rule names the validator, the error it raises (rejected validate result), and *why*.

- **R1 — version pin.** `version == 1`. *Why:* forward-compat; unknown versions rejected, not coerced.
- **R2 — canonical sensor key.** Each `stream_profiles` key matches `^[A-Za-z0-9]+:(color|depth|ir1|ir2)$` and `serial != "local"`. **Reject any `local:*` sentinel key** (broader than just `local:color`). *Why:* FDIR-KEY-001 — legacy heterogeneous identity must not regress (§2); the tightened serial charset rejects empty/garbage/whitespace serials.
- **R3 — ice_policy ⇒ TURN availability.** If `ice_policy == "relay"`, the *derived* TURN availability (nat_cfg.turn_type set + URLs buildable, janus.py:111-146) must be true; else `warning` (not hard reject), since relay-only with no TURN yields no candidates. *Why:* `relay` policy is meaningless without a reachable TURN server. (Draft's "relay ⇒ turn_enabled" recast against the derived TURN source, since there is no settable `turn_enabled`.)
- **R4 — ttl bounds (B1-introduced policy).** `300 ≤ turn_credential_ttl_seconds ≤ 3600`. *Why:* a **B1-introduced security policy** — short-lived coturn `use-auth-secret` ephemeral creds (`generate_turn_credentials`, janus.py:135-142); ≤3600 keeps relay creds short-lived, ≥300 avoids churn. **Nothing in code enforces these bounds today** (the field merely defaults to 3600); B1 adds them as policy, not as a restatement of existing behavior.
- **R5 — reconnect ordering.** `reconnect_max_delay_ms ≥ reconnect_base_delay_ms`. *Why:* backoff ceiling must not be below the base.
- **R6 — stream mode supported (per-sensor, color via SDK catalog).** `resolution`+`fps` for a sensor must appear in that sensor's **dynamically queried** capability set:
  - color → `realsense_catalog.query_catalog()` **color profiles via the mux** (authoritative). The V4L2 path (`v4l2.list_v4l2_modes()`, v4l2.py:10-30) is the **retired** color path per the permanent color→mux migration and is **legacy/non-authoritative** — it MUST NOT be used to validate color modes.
  - depth/ir1/ir2 → `realsense_catalog.query_catalog()` (realsense_catalog.py:73-119).
  *Why:* closes the **critical validation gap** — `CameraStreamConfig` accepts arbitrary `width/height/fps` with no validator (only `rotation`); `is_supported()` (v4l2.py:33-37) exists but is **never called** in production, so unsupported modes silently break the encoder at runtime. B1 makes the SDK catalog authoritative for the validate endpoint across all sensors. *(See §10 OQ-3 — the color V4L2 source is demoted to legacy, not a competing authority.)*
- **R6b — depth/IR tuning is not settable.** Writes to `resolution / fps / bitrate_kbps / gop_frames / encoder / codec` for any sensor where `sensor ≠ color` are `REJECTED`. *Why:* there is **no runtime API** for depth/IR tuning — those values are Initialize-time defaults only (`sensor_lifecycle.py:140-141`). This mirrors the `rtp_port`/`mountpoint_id` REJECTED treatment and prevents schema-implied scope creep (the model defines these fields for all sensors, but only color has a runtime apply path).
- **R7 — bitrate sanity.** `100 ≤ bitrate_kbps ≤ 8000` and within a heuristic band for `(resolution, fps)`. *Why:* prevents encoder-killing extremes; band is advisory `warning`, not reject.
- **R8 — allocation invariants (read-only check).** Across `stream_profiles`, `mountpoint_id` values are unique and `rtp_port` values are unique. *Why:* duplicate mp_id/port would collide in Janus / RTP. **B1 checks the *current allocation state* (allocator output) for these invariants; it does not let the operator set them.** (Draft's "unique mp_id / unique rtp_port" reframed as an invariant audit, not a write rule.)
- **R9 — no secret fields.** Reject any payload field whose name intersects `SENSITIVE_KEYS` (secret_store.py:29-38: `TURN_SHARED_SECRET, JANUS_ADMIN_SECRET, STREAMING_ADMIN_KEY, JANUS_STREAMING_ADMIN_KEY, STREAMING_RGB_MP_SECRET, TEXTROOM_ROOM_SECRET, INTERNAL_API_SECRET, CAM_ADMIN_TOKEN`). *Why:* secrets are reveal-gated/masked via the existing `/api/v1/admin/config` flow only; B1 never accepts them.
- **R10 — no firewall / bind-port / deployment fields.** Reject any field referencing firewall rules, Janus listen/admin bind addresses/ports, `HOST_LAN_IP`, `VIEWER_TOKENS`, or `CAMERA_ENV`. *Why:* DEPLOYMENT_ONLY (firewall-*.sh, network_defaults.py, startup_checks.py A1). Accepting them would not change runtime and could mask a security regression that `enforce_production_security()` only re-checks at startup.
- **R11 — production-safety re-check (informational only).** The validate endpoint calls `startup_checks.production_issues(get_settings())` and returns any **current** blockers as informational `warnings[]`. It reports the *current process* production posture — **it does NOT evaluate the proposed patch.** *Why:* `production_issues(settings: Settings)` (startup_checks.py:35) reads the live `Settings` object plus the lazily-imported token module globals (`ADMIN_TOKEN`, `VIEWER_TOKENS`); a proposed runtime-config patch does not feed `Settings`/token globals, and B1's settable surface (`ice_policy`, `ttl`, color stream tuning) does not even intersect the fields `production_issues` inspects (VIEWER_TOKENS, ADMIN_TOKEN, TURN creds/host, Janus URL, HOST_LAN_IP). So R11 surfaces existing drift for operator awareness, not patch-induced drift.

---

## 5. Effective-Config Endpoint

```
GET /api/v1/admin/runtime-config/effective
```
Admin-protected (`require_admin`), rate-limited (`require_admin_rate_limit`), audit-logged — same dependency stack as `admin_config.router`.

### 5.1 Sanitization pattern (reuse the secret-exclusion discipline, not the field set)
The effective doc MUST reuse the **secret-exclusion discipline** of the existing `ClientRtcConfig` sanitization pattern in `get_client_rtc_config()` (janus.py:101-156, model janus.py:41-54):
- Assemble at **read time** from `Settings` + `nat_cfg` + allocation state; never echo persisted secrets.
- Apply the **same secrets-exclusion model**: `turn_shared_secret`/`TURN_PASS` never surface; the `/janus/nat` `turn_pwd → "***"` mask discipline (janus.py:162-171) is reused.
- ICE policy reported as the **conditional effective** value (depth-camera override + `settings.ice_policy`), matching janus.py:148-154 — not a raw settings echo.

> **Admin-gate disclosure note (corrected vs draft):** `effective` is **admin-only** and **intentionally exposes MORE than `/status`**. Whereas `/status` deliberately excludes TURN hosts/users/credentials (system.py:272-280 surfaces only `camera_type, janus_mount_id, watchdog_*, ice_policy`), `effective` additionally discloses non-secret TURN connection facts (host/realm/user/port) and allocation outputs (`mp_id`, `rtp_port`). This broader disclosure is **justified by the admin gate** (`require_admin` + rate-limit + audit). `effective` therefore reuses `/status`'s **secret-exclusion discipline**, *not* its field set — it is a deliberately wider, admin-only view.

### 5.2 Composition note (read split is real)
`effective` is an **aggregation** of sources that currently live in separate endpoints — there is no single effective doc today:
- WebRTC/ICE/TURN shape ← `/client-config` (janus.py).
- Allocations (`mp_id`, `rtp_port`, `desired_active`) ← `sensor_allocations.json` + `/cameras/streams` (devices.py:73-111).
- Per-sensor *running* state ← `is_running(sensor)` probe via `encoder-admin status` (sensor_lifecycle.py:91-101) — **separate from desired state**.
- Non-secret settings ← `/status` (system.py:272-280).

Effective therefore reports `desired_active` **and** `runtime_active` separately (two queries), never conflating them.

> **`runtime_active` contract (probe side-effect on GET):** `runtime_active` is typed **`bool | null`**, where **`null` = probe failed / indeterminate**. Deriving it calls `is_running(sensor)` (sensor_lifecycle.py:91-101), which returns the `.active` bool or `None` on probe failure. Computing `effective` therefore performs **N `encoder-admin status` subprocess calls** (one per sensor) on a GET — a side-effecting, latency-bearing operation that can hang, time out, or return `None`. Implementations MUST surface `null` rather than guessing, and SHOULD either bound the per-probe timeout or serve `runtime_active` from a cached source to keep the GET responsive.

> **Grounding note:** `effective` excludes per-sensor live media codec/pt/fmtp (those exist only post-go-live via `/health/stream` `janus_summary`), and TURN creds (ephemeral, viewer-derived per request).

---

## 6. Validate Endpoint + Diff + Impact Response

```
POST /api/v1/admin/runtime-config/validate
```
Dry-run only — same admin dependency stack; `/validate` itself writes nothing. **Apply is a separate endpoint added in B2 — `POST /apply`, live for the NEW_SESSIONS_ONLY class only (see B2_RUNTIME_CONFIG_APPLY.md).** Body = a partial or full `RuntimeConfig` patch. Returns `valid`, `diff[]`, `impact[]`, `errors[]`, `warnings[]`.

> **Grounding note (collision):** prefix is `/api/v1/admin/`**`runtime-config`** to avoid colliding with the existing `/api/v1/admin/config` router (admin_config.py:34).

> **Field semantics:** `impact[]` is the de-duplicated set of per-`diff[]`-entry `ApplyImpact` values for the parseable changes in the patch. Overall rejection reasons (secret field, legacy key, unsettable field) go in `valid:false` + `errors[]` — they are **not** "changes" and are **never** emitted as a top-level `impact:["REJECTED"]`. `REJECTED` appears only as the per-entry `impact` of a parseable change to a read-only/unsettable field.

### 6.1 Response shape (grounded examples)
Example uses real keys: color = `<serial>:color` (canonical post-migration, mp 1305 / port 5004); depth = `<serial>:depth` (dynamic pool).

```json
{
  "valid": true,
  "diff": [
    { "path": "stream_profiles.911222060123:color.fps", "from": 15, "to": 30,
      "source": "rs-color.tuning.env:FPS", "impact": "RESTART_ENCODER" },
    { "path": "webrtc.ice_policy", "from": "all", "to": "relay",
      "source": "Settings.ice_policy (env ICE_POLICY)", "impact": "NEW_SESSIONS_ONLY" },
    { "path": "diagnostics.health_stream_stale_ms", "from": 10000, "to": 8000,
      "source": "Settings.watchdog_stale_ms (env CAM_WATCHDOG_STALE_MS)", "impact": "DEPLOYMENT_ONLY" }
  ],
  "impact": ["RESTART_ENCODER", "NEW_SESSIONS_ONLY", "DEPLOYMENT_ONLY"],
  "errors": [],
  "warnings": ["webrtc.ice_policy=relay: TURN availability derived from nat_cfg is OK (no blocker)."]
}
```

Rejected example (rejection reasons in `errors[]`; **no** top-level `impact:["REJECTED"]`):
```json
{
  "valid": false,
  "diff": [],
  "impact": [],
  "errors": [
    "stream_profiles key 'local:color' rejected: legacy/sentinel identity (FDIR-KEY-001). Use '<serial>:color'.",
    "field 'TURN_SHARED_SECRET' rejected: secret (SENSITIVE_KEYS), not settable via runtime-config."
  ],
  "warnings": []
}
```

Parseable-but-unsettable change (depth tuning) → per-entry `REJECTED`, patch `valid:false`:
```json
{
  "valid": false,
  "diff": [
    { "path": "stream_profiles.911222060123:depth.fps", "from": 30, "to": 15,
      "source": "sensor_lifecycle.py:140 (Initialize-time default; no runtime API)", "impact": "REJECTED" }
  ],
  "impact": ["REJECTED"],
  "errors": ["stream_profiles.911222060123:depth.fps rejected (R6b): depth/IR tuning has no runtime API; Initialize-time defaults only."],
  "warnings": []
}
```

---

## 7. ApplyImpact Enum + Grounded Mapping

```python
class ApplyImpact(str, Enum):
    HOT                  = "HOT"                   # takes effect with no restart, affects live state
    NEW_SESSIONS_ONLY    = "NEW_SESSIONS_ONLY"     # only new /client-config sessions pick it up
    RESTART_ENCODER      = "RESTART_ENCODER"       # rs-stream@<sensor> restart needed
    RECREATE_MOUNTPOINT  = "RECREATE_MOUNTPOINT"   # destroy+recreate Janus mp; no runtime path today
    RESTART_JANUS        = "RESTART_JANUS"         # full Janus restart (destructive to all sessions)
    DEPLOYMENT_ONLY      = "DEPLOYMENT_ONLY"       # env/IaC/client-side; no runtime apply path
    REJECTED             = "REJECTED"              # per-change impact: invalid / forbidden / read-only write
```

`REJECTED` is a **per-`diff[]`-entry** impact for a parseable change to a read-only/unsettable field. Overall-payload rejection reasons (secret/legacy-key/firewall fields) are reported via `valid:false` + `errors[]`, never as a top-level `impact` (§6).

### 7.1 Grounded mapping table
| Change | Impact | Grounding |
|---|---|---|
| `stream_profiles.*.fps` / `resolution` / `bitrate_kbps` / `gop_frames` (**color**) | `RESTART_ENCODER` | `POST /config` writes tuning.env then `restart_color_encoder()` → `encoder-admin restart --family rs-stream --instance color` (camera.py:73-78,163-191). `rs-stream.sh` reads values **once** at startup. |
| Same fields for **depth/ir1/ir2** | `REJECTED` | No runtime API; values are Initialize-time defaults (`sensor_lifecycle.py:140-141`). Writes for sensor ≠ color are rejected (R6b). |
| `stream_profiles.*.rtp_port` / `mountpoint_id` | `REJECTED` *(allocator-owned; theoretical impact `RECREATE_MOUNTPOINT`)* | Would theoretically need `destroy_mountpoint` + new `create_mountpoint` (janus_admin.py:107-151) + contract.env rewrite + encoder restart. **No runtime change mechanism exists** — set only at `initialize()`. |
| `stream_profiles.*.codec` / `encoder` | `REJECTED` | codec hardcoded; encoder preset/tune is color-tuning.env only, modeled read-only. |
| `stream_profiles.*.enabled` | `REJECTED` | maps to `desired_active`; toggled only via lifecycle `initialize()/stop()` (which destroy/create the mountpoint *and* start/stop the encoder). Read-only in B1 → an attempted write is `REJECTED` (single classification; no double-label). |
| `webrtc.ice_policy` | `NEW_SESSIONS_ONLY` | read in `get_client_rtc_config()` at request time (janus.py:149-154); existing PeerConnections keep their original policy (config baked at page load). |
| `webrtc.turn_credential_ttl_seconds` | `NEW_SESSIONS_ONLY` | consumed per `/client-config` call (janus.py:135-142); existing sessions unaffected. |
| `webrtc.max_reconnect_attempts` / `reconnect_base_delay_ms` / `reconnect_max_delay_ms` | `DEPLOYMENT_ONLY` | client-side only (config.js:118-130); no L4 apply path. |
| `diagnostics.health_stream_stale_ms` | `DEPLOYMENT_ONLY` | `Settings.watchdog_stale_ms` read from env at process start (settings.py:156); needs service restart. |
| `diagnostics.reboot_allowed` | `REJECTED` | `Settings.watchdog_reboot_enabled` read at process start (settings.py:161), **not** at reboot time → stale-in-memory hazard (§10 OQ-5). **Read-only safety gate in B1, never settable**; an attempted write is `REJECTED`. |
| `diagnostics.debug_stats_enabled` / `telemetry_*` / `fdir_enabled` / `auto_recovery_enabled` | `REJECTED` (read-only) / `DEPLOYMENT_ONLY` (client-side advisory) | no backend toggle (always-on / client-side); attempted writes to read-only constants are `REJECTED`. |
| Any `SENSITIVE_KEYS` field, firewall/bind/`HOST_LAN_IP`/`VIEWER_TOKENS`/`CAMERA_ENV` | rejected via `errors[]` (not an `impact` value) | §4 R9/R10 — overall-payload rejection, not a per-change impact. |

> **Grounding notes (corrections vs draft):**
> - Draft `fps → RESTART_ENCODER` ✓ (color), but B1 splits **color = RESTART_ENCODER, depth/IR = REJECTED** (no runtime API; R6b).
> - Draft `ice_policy → NEW_SESSIONS_ONLY` ✓.
> - Draft `debug_stats → HOT` corrected to **read-only** (`REJECTED` on write; client-side advisory otherwise) — no server toggle exists, so it cannot be HOT.
> - `rtp_port/mountpoint_id`: draft `RECREATE_MOUNTPOINT` is theoretically right but **operationally invalid** (no runtime path) → B1 rejects edits.
> - `enabled` carries a **single** classification (`REJECTED`, read-only in B1) — the draft's contradictory "RESTART_ENCODER-class … → DEPLOYMENT_ONLY" double-label is removed.
> - `reboot_allowed` is a **read-only safety gate** (`REJECTED` on write), never `DEPLOYMENT_ONLY`-settable.
> - `RESTART_JANUS` is enumerated for completeness but maps to **no B1-settable field** (Janus bind/admin are DEPLOYMENT_ONLY).

---

## 8. Unit-Test List

1. `test_effective_contains_canonical_keys` — keys all `<serial>:<sensor>`; no `local:*`.
2. `test_effective_masks_secrets` — no `SENSITIVE_KEYS`/`turn_pwd`/`shared_secret`; non-secret TURN host/user present.
3. `test_effective_reports_desired_and_runtime_separately` — `desired_active` and `runtime_active` distinct; `runtime_active` is `bool | null`.
4. `test_effective_runtime_active_null_on_probe_failure` — `is_running()`→None ⇒ `runtime_active=null`.
5. `test_validate_accepts_safe_webrtc_patch` — `ice_policy all→relay` (TURN available) ⇒ valid, `NEW_SESSIONS_ONLY`.
6. `test_validate_warns_relay_without_turn` — relay + no derived TURN ⇒ warning.
7. `test_validate_ttl_bounds` — ttl 200/4000 rejected; 3600 ok (R4 policy bound).
8. `test_validate_reconnect_ordering` — max<base ⇒ rejected.
9. `test_validate_rejects_duplicate_rtp_port` — invariant failure (R8).
10. `test_validate_rejects_duplicate_mountpoint` — invariant failure (R8).
11. `test_validate_rejects_legacy_local_color_key` — `local:color` rejected (FDIR-KEY-001).
12. `test_validate_rejects_any_local_sentinel_key` — `local:depth`/`local:ir1` rejected (R2 broader).
13. `test_validate_rejects_unsupported_mode` — color `1920x1080@120` not in SDK catalog ⇒ rejected (R6).
14. `test_validate_accepts_supported_mode` — color `640x480@30` in SDK catalog ⇒ ok.
15. `test_validate_rejects_depth_ir_tuning_write` — write to `<serial>:depth.fps`/res/bitrate/gop/encoder ⇒ per-entry `REJECTED` + `errors[]` (R6b).
16. `test_classify_color_fps_restart_encoder` — color fps ⇒ `RESTART_ENCODER`.
17. `test_classify_depth_fps_rejected` — depth fps ⇒ `REJECTED` (R6b).
18. `test_classify_ice_policy_new_sessions_only` — ⇒ `NEW_SESSIONS_ONLY`.
19. `test_classify_debug_stats_rejected` — `debug_stats_enabled` write ⇒ `REJECTED` (corrected from draft HOT).
20. `test_classify_rtp_port_rejected` — `rtp_port` edit ⇒ `REJECTED` (allocator-owned).
21. `test_classify_enabled_rejected` — `enabled` write ⇒ `REJECTED` (single classification).
22. `test_classify_reboot_allowed_rejected` — `reboot_allowed` write ⇒ `REJECTED` (safety gate).
23. `test_validate_rejects_secret_fields` — `SENSITIVE_KEYS` ⇒ `valid:false` + `errors[]` (R9); no top-level `impact:["REJECTED"]`.
24. `test_validate_rejects_firewall_bind_fields` — firewall/bind/`HOST_LAN_IP`/`VIEWER_TOKENS`/`CAMERA_ENV` ⇒ `valid:false` + `errors[]` (R10).
25. `test_validate_runs_production_issues_informational` — `production_issues(get_settings())` blockers in `warnings[]` as current-state info; does NOT evaluate the patch (R11).
26. `test_no_top_level_rejected_impact` — rejected payload ⇒ `valid:false` + `errors[]`, no `impact:["REJECTED"]`.
27. `test_diff_emits_source_field` — each diff entry carries a `source` string.
28. `test_no_apply_endpoint_registered` — only `effective` (GET) + `validate` (POST); no mutating route.

---

## 9. Non-Goals / ADR

Record in the `runtime_schema.py` module docstring and an ADR. **B1 does not:** apply / restart / write revisions / expose secrets / modify firewall / edit Janus bind or admin / replace systemd-IaC / convert always-on or safety-gated subsystems (FDIR, telemetry ingestion, watchdog reboot) into runtime toggles / provide any runtime mutation path for `rtp_port`, `mountpoint_id`, depth/IR stream tuning, `enabled`, `reboot_allowed`, `VIEWER_TOKENS`, or `CAMERA_ENV`. B1 is a **typed contract + dry-run validator + read-only effective view**. A future B2 owns the apply engine, revision history, and confirmation/restart UX (building on the existing `admin_config.apply()` pattern, admin_config.py:239-285).

---

## 10. Open Questions / Grounding Notes

**Corrections vs the operator draft (consolidated):**
- Module/endpoint: use `/api/v1/admin/runtime-config` (existing `/api/v1/admin/config`, admin_config.py:34).
- `ice_policy` default `relay → all` (settings.py:133); `relay` is a depth-camera request-time override.
- `turn_enabled`/`stun_enabled` derived/read-only (no off-switch; STUN always on, TURN conditional).
- `turn_credential_ttl_seconds` bounds `300..3600` are a **B1-introduced policy** (R4), not current code.
- reconnect_* fields client-side only; ranges corrected; `max_reconnect_attempts` default unknown/client-owned (not `8`); class `DEPLOYMENT_ONLY`.
- `gop_seconds → gop_frames` (unit frames, default 15); `1..300` is a B1 advisory bound.
- codec/encoder read-only (hardcoded H.264/x264; preset/tune color-tuning.env only).
- resolution/fps/bitrate/gop runtime API **color only**; depth/IR Initialize-time defaults, writes `REJECTED` (R6b).
- `mountpoint_id`/`rtp_port` allocator outputs (read-only); edits `REJECTED`; uniqueness recast as invariant audit (R8).
- `health_stream_stale_ms` default `5000 → 10000` (settings.py:156).
- `debug_stats_enabled` corrected `HOT → read-only/REJECTED-on-write`; telemetry/fdir/auto_recovery always-on/read-only.
- `reboot_allowed` read-only/derived safety gate, never settable; write `REJECTED`.
- R6 color capability source = **RealSense SDK catalog** (mux), not V4L2 (retired).
- R11 restated: validate reports **current** `production_issues` blockers as `warnings[]`; does not evaluate the patch.
- Top-level `impact:["REJECTED"]` removed; rejection reasons in `errors[]`.
- The draft's fixed resolution/fps enum does not exist — modes queried dynamically via SDK catalog.

**Open questions — for B2/B3, NOT blockers for B1.** B1 (schema + validate + impact + read-only effective) ships without resolving these; each is a future apply-engine / safety decision. OQ-5 in particular is a **B2 safety prerequisite** (must be fixed before `reboot_allowed` is ever made runtime-settable), not a B1 task.

- **OQ-1 (no-source fields):** `turn_enabled`, `stun_enabled`, `debug_stats_enabled`, `telemetry_enabled`, `fdir_enabled`, `auto_recovery_enabled`, `telemetry_interval_seconds` have no backend source. Keep as read-only/derived (current choice) or drop? Adding real toggles is out of B1 scope.
- **OQ-2 (depth/IR runtime tuning):** Should B2 add a runtime API for depth/IR `resolution/fps/bitrate/gop` (today Initialize-only, B1-`REJECTED`)? Affects later classification.
- **OQ-3 (capability source for R6):** Per the permanent color→mux migration (2026-06-17) the V4L2 color path is retired, so B1 makes the **RealSense SDK catalog authoritative** for color/depth/IR and demotes V4L2 to legacy. Confirm no remaining live device's color modes diverge from the SDK catalog.
- **OQ-4 (enabled/desired_active):** Should `enabled` become settable in B2 (writes `desired_active` + lifecycle → `RECREATE_MOUNTPOINT`-class), or stay read-only? B1 keeps it read-only.
- **OQ-5 (reboot_allowed staleness):** `watchdog_reboot_enabled` is read at **process start**, not at reboot-decision time. If ever exposed settable in B2 without re-read-at-decision-time or mandatory restart, a stale value wins silently — and it is a **safety gate**. B1 keeps it read-only and recommends the re-read architecture change before it is ever made runtime-settable.
- **OQ-6 (admin-token rotation paradox):** `CAM_ADMIN_TOKEN` is both a `SENSITIVE_KEY` and the guard for rotation. B1 never touches it; rotation stays on `/api/v1/admin/config/rotate/CAM_ADMIN_TOKEN` (show-once). Noted so B2 does not duplicate it.

---

*Target module `app/config/runtime_schema.py` is created in the implementation phase (B1-impl), not in this design phase. Per the agreed order, B1 implementation begins only after A2 firewall persist closes Sprint 1.*

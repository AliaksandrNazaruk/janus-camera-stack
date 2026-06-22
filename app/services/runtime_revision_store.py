"""B2-0 — runtime-config revision store (journal-only, READ-ONLY w.r.t. live config).

Persists a redacted, *validated* runtime-config revision — the operator's intent
(``validated_patch``), the effective base it was validated against
(``effective_before``), and a stable ``diff_hash`` — so a future apply phase can
bind a confirmation token to one exact validated change (spec §10).

B2-0 SCOPE (deliberately narrow — ``docs/design/B2_RUNTIME_CONFIG_APPLY.md`` §4.2):
this module WRITES ONLY to the revision journal under ``REVISION_DIR``. It performs
NO live-config mutation, NO env write, NO service restart, NO ``os.environ`` or
settings-cache change, NO encoder restart, NO FDIR interaction. It READS config
files (for ``file_hashes_before``) read-only. Apply / rollback / restart are NOT
implemented here and remain prerequisite-blocked.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.store_safety import StoreCorrupt, quarantine_corrupt

log = logging.getLogger(__name__)

# Monkeypatched to a tmp dir in tests (see tests/conftest.py) — never write real state in a test.
REVISION_DIR = Path(os.environ.get("CAM_REVISION_DIR", "/var/lib/camera-fdir/runtime_revisions"))
MAX_REVISIONS = 50  # OQ-2: keep last 50. (The "last-good" pointer is an apply-phase concept; n/a in B2-0.)
# Track A: the writable non-secret env file that sources ICE_POLICY/TURN_CRED_TTL once
# relocated off the systemd Environment= directive. Presence self-clears the C2 blocker.
RUNTIME_ENV_PATH = Path(os.environ.get("CAM_RUNTIME_ENV", "/etc/robot/rs-runtime.env"))

# AE-0 (B2 NEW_SESSIONS_ONLY apply primitives):
# A distinct, matchable marker for "the bound file did not exist at hash time" — so absence
# is NOT the empty dict {} that fails open at base-match (AE-C1, proven fail-open).
RUNTIME_ENV_SENTINEL = "sha256:__ABSENT__"
# Bump when the file_hashes_before binding contract changes; revisions stamped with an older
# schema are non-applyable (AE-C5 — rejects pre-binding B2-0 journal records).
FHB_SCHEMA = 2
# webrtc fields that persist to rs-runtime.env (the NEW_SESSIONS_ONLY apply surface).
_RUNTIME_ENV_FIELDS = {"ice_policy", "turn_credential_ttl_seconds"}

# Revision status state machine (AE-C12 — net-new; B2-0 only ever wrote "validated").
STATUS_VALIDATED = "validated"
STATUS_APPLYING = "applying"
STATUS_APPLIED = "applied"
STATUS_ROLLING_BACK = "rolling_back"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_ROLLBACK_FAILED = "rollback_failed"
_STATUSES = {STATUS_VALIDATED, STATUS_APPLYING, STATUS_APPLIED,
             STATUS_ROLLING_BACK, STATUS_ROLLED_BACK, STATUS_ROLLBACK_FAILED}

# Precise secret-key markers (consistent with the B1-2 builder) — NOT broad "credential"/"token",
# which would false-match the legitimate non-secret ``turn_credential_ttl_seconds``.
_SECRET_MARKERS = ("password", "_secret", "shared_secret", "pwd", "turn_pass", "turn_pwd", "admin_token")


def _sensitive_keys() -> set:
    try:
        from app.services.secret_store import SENSITIVE_KEYS
        return set(SENSITIVE_KEYS)
    except Exception:  # pragma: no cover — defensive
        return set()


def _is_secret_key(k: Any) -> bool:
    ku, kl = str(k).upper(), str(k).lower()
    return ku in _sensitive_keys() or any(m in kl for m in _SECRET_MARKERS)


def redact(obj: Any) -> Any:
    """Defensive: replace any secret-keyed value with a sentinel, recursively.
    B2-0 records are secret-free by construction (effective_before is built by the
    secret-excluding B1 builder; no file *content* is stored — only hashes), but the
    journal is redacted on the way out so a future settable field can never leak."""
    if isinstance(obj, dict):
        return {k: ("***REDACTED***" if _is_secret_key(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


# ── canonicalization + diff hash (spec §10.1) ─────────────────────────────────
def _normalize(o: Any) -> Any:
    if isinstance(o, bool):
        return o
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {str(k): _normalize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_normalize(v) for v in o]
    return o


def _canon(obj: Any) -> str:
    """Deterministic serialization: sorted keys, compact, number-normalized. Approximates
    RFC 8785 JCS; a full JCS upgrade is tracked for the apply phase (spec §10.1, C9)."""
    return json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_bytes_or_sentinel(p: Path) -> str:
    """Full-file sha256, or RUNTIME_ENV_SENTINEL if the file is absent. Absence is a
    distinct, matchable value (NOT a missing dict key) so absent-at-validate vs
    present-at-apply mismatches at base-match instead of collapsing to {}=={} (AE-C1)."""
    return ("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else RUNTIME_ENV_SENTINEL


def file_hashes_before(patch: Dict[str, Any]) -> Dict[str, str]:
    """READ-ONLY: full-content hash of every writable target the patch would touch.
    For NEW_SESSIONS_ONLY webrtc fields, rs-runtime.env is bound UNCONDITIONALLY (sentinel
    on absence — AE-C1). Color tuning is bound only when present (RESTART_ENCODER is
    Track-B-gated and out of the AE-0 apply scope)."""
    out: Dict[str, str] = {}
    p = patch or {}
    sp = p.get("stream_profiles") or {}
    if any(str(k).endswith(":color") for k in sp):
        try:
            from app.core.settings import get_settings
            f = Path(get_settings().env_path)
            if f.is_file():
                out[str(f)] = "sha256:" + hashlib.sha256(f.read_bytes()).hexdigest()
        except Exception as e:  # pragma: no cover — defensive
            log.debug("file_hashes_before: color tuning hash failed: %s", e)
    wp = p.get("webrtc") or {}
    if any(k in _RUNTIME_ENV_FIELDS for k in wp):
        out[str(RUNTIME_ENV_PATH)] = _hash_bytes_or_sentinel(RUNTIME_ENV_PATH)
    return out


def compute_diff_hash(patch: Dict[str, Any], fhb: Dict[str, str]) -> str:
    """diff_hash = sha256( canon(patch_normalized) ‖ canon(file_hashes_before) ). Binding to
    the full-file hashes (not the secret-free effective view) closes the C4 blind spot."""
    payload = _canon(patch) + "\n" + _canon(fhb)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── persist / read (durable journaling — spec §5.2) ───────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj: dict) -> None:
    """tmp + os.replace + fsync(file) + fsync(dir) — the durability model the spec
    requires (C6: bare _flock_state does NOT fsync)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:  # best-effort dir fsync so the rename is durable
        dfd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except (OSError, AttributeError):  # pragma: no cover
        pass


def _read_revision_json(p: Path, revision_id: str) -> dict:
    """Read + parse a revision file, FAILING CLOSED on content corruption (quarantine + StoreCorrupt)
    rather than hiding it as 'not found' / silently swallowing it. The read itself may raise OSError
    (the callers decide whether an access error degrades to None or propagates)."""
    raw = p.read_text()
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        q = quarantine_corrupt(p, f"invalid JSON: {e}")
        raise StoreCorrupt(f"revision {revision_id} is not valid JSON ({e}); quarantined {q}") from e
    if not isinstance(rec, dict):
        q = quarantine_corrupt(p, "top-level is not a JSON object")
        raise StoreCorrupt(f"revision {revision_id} top-level is not a JSON object; quarantined {q}")
    return rec


def persist_validated(patch: Dict[str, Any], result: Any,
                      effective_before: Optional[dict] = None) -> Tuple[str, str]:
    """Build + durably write a redacted validated revision. Returns (revision_id, diff_hash).
    WRITES ONLY the revision journal — no live-config mutation. Raises on I/O error
    (the route wraps this best-effort so journaling can never break /validate)."""
    fhb = file_hashes_before(patch)
    diff_hash = compute_diff_hash(patch, fhb)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    revision_id = f"rev-{ts}-{diff_hash[7:15]}"
    record = {
        "revision_id": revision_id,
        "created_at": _now_iso(),
        "diff_hash": diff_hash,
        "validated_patch": _normalize(patch),
        "effective_before": effective_before,
        "diff": [d.model_dump(by_alias=True, mode="json") for d in result.diff],
        "impact": [i.value for i in result.impact],
        "file_hashes_before": fhb,
        "fhb_schema": FHB_SCHEMA,            # AE-C5: stamp the binding contract version
        "binds": sorted(fhb.keys()),         # the files this revision's hash is bound to
        "status": STATUS_VALIDATED,
    }
    # AE-1: a validated NEW_SESSIONS_ONLY revision IS applyable (POST /apply is live) — stamp the
    # structural verdict so the journaled record matches the live capability (was hardcoded False, B2-0).
    record["apply_supported"] = is_applyable(record)[0]
    _atomic_write_json(REVISION_DIR / f"{revision_id}.json", redact(record))
    _prune()
    return revision_id, diff_hash


def set_status(revision_id: str, status: str) -> bool:
    """Durably set a revision's status (AE-C12 — the net-new state machine setter that
    B2-0 lacked). Re-writes the (already-redacted) record via the fsync'd atomic writer.
    Returns False if the revision is unknown. The apply engine (AE-1) drives transitions
    under the apply lock; AE-0 provides + tests the durable setter only."""
    if status not in _STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if "/" in revision_id or "\\" in revision_id or ".." in revision_id or not revision_id.startswith("rev-"):
        return False
    p = REVISION_DIR / f"{revision_id}.json"
    if not p.is_file():
        return False
    rec = _read_revision_json(p, revision_id)        # corrupt → quarantine + StoreCorrupt (was a raw 500)
    rec["status"] = status
    rec["status_updated_at"] = _now_iso()
    _atomic_write_json(p, rec)
    return True


def is_applyable(record: dict) -> Tuple[bool, str]:
    """Structural pre-check used by the NEW_SESSIONS_ONLY apply engine (AE-1, POST /apply).
    Applyable ONLY if: status==validated, current fhb_schema, impact is exactly {NEW_SESSIONS_ONLY},
    and file_hashes_before binds rs-runtime.env. This closes the {}=={} fail-open (AE-C1) and rejects
    pre-binding B2-0 journal records (AE-C5) — independent of any hash equality."""
    if record.get("status") != STATUS_VALIDATED:
        return False, f"status is {record.get('status')!r}, not validated"
    if record.get("fhb_schema") != FHB_SCHEMA:
        return False, "revision predates the rs-runtime.env hash binding (stale fhb_schema)"
    if set(record.get("impact") or []) != {"NEW_SESSIONS_ONLY"}:
        return False, f"impact {sorted(record.get('impact') or [])} is not exactly NEW_SESSIONS_ONLY"
    if str(RUNTIME_ENV_PATH) not in (record.get("file_hashes_before") or {}):
        return False, "file_hashes_before does not bind rs-runtime.env"
    return True, "applyable"


def get_revision(revision_id: str) -> Optional[dict]:
    """Read a revision, redacted. Returns None if absent or the id is malformed
    (path-traversal guard — revision_id is opaque)."""
    if "/" in revision_id or "\\" in revision_id or ".." in revision_id or not revision_id.startswith("rev-"):
        return None
    p = REVISION_DIR / f"{revision_id}.json"
    if not p.is_file():
        return None
    try:
        rec = _read_revision_json(p, revision_id)    # corrupt content → StoreCorrupt (NOT hidden as None)
    except OSError as e:                             # access/IO error degrades to None + warn
        log.warning("get_revision: %s unreadable (%s) — treating as not found", revision_id, e)
        return None
    return redact(rec)


def list_revisions() -> List[str]:
    if not REVISION_DIR.is_dir():
        return []
    return sorted(p.stem for p in REVISION_DIR.glob("rev-*.json"))


def _prune() -> None:
    ids = list_revisions()  # sorted by ts-in-name → oldest first
    for stem in ids[:-MAX_REVISIONS] if len(ids) > MAX_REVISIONS else []:
        try:
            (REVISION_DIR / f"{stem}.json").unlink()
        except OSError:  # pragma: no cover
            pass


# ── capability report (spec §4.1/§4.2 — why apply is currently blocked) ────────
def _settings_field_is_frozen_literal(name: str) -> bool:
    """True if the Settings field carries an import-time literal default (no
    default_factory) → get_settings.cache_clear() cannot refresh it (spec C1)."""
    try:
        from app.core.settings import Settings
        for f in dataclasses.fields(Settings):
            if f.name == name:
                return f.default is not dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    except Exception:  # pragma: no cover
        pass
    return False


def _runtime_env_file_present() -> bool:
    """True once the Track A relocation has landed — a writable non-secret env file
    sources ICE_POLICY/TURN_CRED_TTL (vs. the systemd Environment= directive). Used to
    self-clear the C2 capability blocker without hardcoding deploy state."""
    return RUNTIME_ENV_PATH.is_file()


def capability_report() -> dict:
    """Operator-facing: what apply is supported today and, per impact class, why it is
    blocked. Grounded + dynamic — the C1 (frozen-literal) and C2 (writable env file)
    NEW_SESSIONS_ONLY blockers are evaluated live, so they self-clear as Track A lands."""
    ns: List[str] = []
    if _settings_field_is_frozen_literal("ice_policy") or _settings_field_is_frozen_literal("turn_cred_ttl"):
        ns.append("Settings fields are frozen import-time literals; get_settings.cache_clear() cannot refresh them (spec C1)")
    if not _runtime_env_file_present():
        ns.append(f"ICE_POLICY/TURN_CRED_TTL not relocated to a writable env file yet — {RUNTIME_ENV_PATH} absent (spec C2)")
    # The B2 NEW_SESSIONS_ONLY apply ENGINE (AE-1) is LIVE — apply is supported once the C1/C2 field-level
    # blockers clear (no more "awaiting the engine"; POST /apply applies ice_policy / turn_credential_ttl,
    # verifies, rolls back). apply_supported tracks NEW_SESSIONS_ONLY applyability dynamically.
    apply_supported = not ns
    return {
        "apply_supported": apply_supported,
        "supported_steps": ["journal_only", "apply"] if apply_supported else ["journal_only"],
        "blocked_impacts": {
            "NEW_SESSIONS_ONLY": ns,
            "RESTART_ENCODER": [
                "FDIR quiesce mechanism not implemented; a planned encoder restart may trigger the autonomous recovery ladder (spec C3/§12)",
            ],
            "RECREATE_MOUNTPOINT": ["deferred (B3/B4)"],
            "RESTART_JANUS": ["deferred; maintenance-window class"],
            "DEPLOYMENT_ONLY": ["never runtime-applyable"],
            "REJECTED": ["never applyable"],
        },
        "spec": "docs/design/B2_RUNTIME_CONFIG_APPLY.md",
    }

"""Settings → Notifications & Remote Approval: what is switched on, and the
relay this PC talks to.

The settings blob lives in app_state like every other provider's
(server/woopsocial_service.py). The device secret that signs requests to the
relay does not: it goes through core/secrets.py (DPAPI on Windows), and no
route ever returns it. The creator's phone number never reaches this PC at
all: pairing happens on the relay, which only tells us its last four digits,
for "Is this your number?".

Remote approval is off until switched on, and needs WhatsApp notifications,
a confirmed pairing and an approval PIN first (service.can_enable).
"""

import json
import secrets as _random
from pathlib import Path

from core import secrets

SETTINGS_KEY = "remote_approval"
DEVICE_SECRET = "remote_approval_device"

PLATFORMS = ("instagram", "facebook", "threads")
PLATFORM_LABELS = {
    "instagram": "Instagram Reels",
    "facebook": "Facebook Page Reels",
    "threads": "Threads",
}

DEFAULTS = {
    # WhatsApp notifications through the relay.
    "whatsapp_enabled": False,
    "relay_url": "",
    "notify_on_finish": True,
    "notify_on_fail": True,
    "notify_on_publish": True,
    # Approving publishes from the phone. Off by default, on purpose.
    "remote_approvals": False,
    # Ticked in advance on the phone. Only ever a subset of what is connected.
    "preferred_destinations": [],
    # How long a link in a message stays valid, and how long "Remind me later"
    # waits.
    "approval_hours": 24,
    "remind_minutes": 120,
    # At most this many clips in one offer, best first: a full game can make
    # thirty, and each platform caps posts per day (Facebook Reels: 30).
    "max_clips": 10,
    # Only basketball videos, which is what this edition is for. Gaming and
    # every other video are the creator's to opt in to (Settings: "Offer
    # gaming and other videos too").
    "basketball_only": True,
    # Muse Spark writes the captions (one request per finished video, kept)
    # and, separately, may add a one-line summary to the message.
    "muse_captions": False,
    "muse_summary": False,
    "muse_model": "muse-spark-1.3",
    # What the relay last told us, for Settings to show without asking it.
    "pairing": {},      # {"state": "none|pending|confirmed", "tail": "1234", "at": "..."}
    "pin_set": False,
    "accounts": {},     # {"instagram": {"connected": true, "name": "@..."}, ...}
    "templates": {},    # {"ck_highlights_ready": "APPROVED", ...}
}

#: Keys a settings PATCH from the UI may change. The rest are written by the
#: service from what the relay reports.
EDITABLE = frozenset({
    "whatsapp_enabled", "relay_url", "notify_on_finish", "notify_on_fail", "notify_on_publish",
    "remote_approvals", "preferred_destinations", "approval_hours", "remind_minutes", "max_clips",
    "basketball_only", "muse_captions", "muse_summary", "muse_model",
})


def load(db) -> dict:
    try:
        stored = json.loads(db.get_flag(SETTINGS_KEY, "") or "{}")
    except (TypeError, ValueError):
        stored = {}
    return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}


def save(db, patch: dict, *, internal: bool = False) -> dict:
    """Merge a patch in. From the UI (internal=False) only EDITABLE keys get
    through, each cleaned; the service passes internal=True for what the relay
    reported."""
    allowed = DEFAULTS.keys() if internal else EDITABLE
    clean = {k: _clean(k, v) for k, v in patch.items() if k in allowed}
    merged = {**load(db), **clean}
    db.set_flag(SETTINGS_KEY, json.dumps(merged))
    return merged


def _clean(key: str, value):
    if key == "relay_url":
        url = str(value or "").strip().rstrip("/")
        if url and not url.startswith("https://") and not _is_local(url):
            raise ValueError("The relay address must start with https://")
        return url
    if key == "preferred_destinations":
        return [p for p in PLATFORMS if p in (value or [])]
    if key == "approval_hours":
        return _bounded(value, 1, 72, 24)
    if key == "remind_minutes":
        return _bounded(value, 15, 24 * 60, 120)
    if key == "max_clips":
        return _bounded(value, 1, 25, 10)
    if key == "muse_model":
        model = str(value or "").strip()
        return model if model.startswith("muse-spark") else DEFAULTS["muse_model"]
    if key in DEFAULTS and isinstance(DEFAULTS[key], bool):
        return bool(value)
    return value


def _bounded(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _is_local(url: str) -> bool:
    """http:// is allowed for a relay on this machine only: `wrangler dev`
    and the mock relay the demo and the tests run."""
    return url.startswith(("http://127.0.0.1", "http://localhost"))


# ---- this PC's identity on the relay -----------------------------------------


def device(data_dir) -> dict:
    """{"device_id", "device_secret"} or {} before the PC is registered."""
    got = secrets.load(Path(data_dir), DEVICE_SECRET) or {}
    if got.get("device_id") and got.get("device_secret"):
        return {"device_id": str(got["device_id"]), "device_secret": str(got["device_secret"])}
    return {}


def new_device(data_dir) -> dict:
    """Make this PC's id and secret. Registered with the relay once (with the
    relay owner's enrolment key), then used to sign every request."""
    made = {"device_id": "dev_" + _random.token_hex(8), "device_secret": _random.token_urlsafe(32)}
    secrets.save(Path(data_dir), DEVICE_SECRET, made)
    return made


def forget_device(data_dir) -> bool:
    return secrets.wipe(Path(data_dir), DEVICE_SECRET)


def public_view(db, data_dir) -> dict:
    """What Settings shows. No secret, no phone number: the last four digits
    are all this PC is ever told."""
    s = load(db)
    return {
        **{k: s[k] for k in DEFAULTS},
        "registered": bool(device(data_dir)),
        "platform_labels": PLATFORM_LABELS,
    }

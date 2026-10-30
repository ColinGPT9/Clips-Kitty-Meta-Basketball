"""Settings → Notifications & Remote Approval (new for the Meta hackathon edition).

The routes over remote_approval/, which holds every rule (and is tested in
CI's main job, which has no FastAPI). Every route that changes something
needs the desktop app's session secret in the X-Clips-Kitty-Session header,
the same value the plugin and Discord routes take, so a web page or a stray
script can't pair a number, connect an account, switch approvals on or send
a message. The reads return no secret and no phone number (the last four
digits only).

Nothing here listens for the phone: the PC only ever calls out to the
creator's relay (remote_approval/relay.py), so this engine stays on
127.0.0.1. Every route is internal (server/api_stability.py).
"""

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from plugins import session
from remote_approval import service, states, store, usage
from remote_approval import settings as ra_settings
from remote_approval.relay import RelayError
from server.events import broadcaster


class SettingsPatch(BaseModel):
    whatsapp_enabled: bool | None = None
    relay_url: str | None = None
    notify_on_finish: bool | None = None
    notify_on_fail: bool | None = None
    notify_on_publish: bool | None = None
    remote_approvals: bool | None = None
    preferred_destinations: list[str] | None = None
    approval_hours: int | None = None
    remind_minutes: int | None = None
    max_clips: int | None = None
    basketball_only: bool | None = None
    muse_captions: bool | None = None
    muse_summary: bool | None = None
    muse_model: str | None = None


class RegisterIn(BaseModel):
    relay_url: str = ""
    enroll_key: str


class ConfirmIn(BaseModel):
    confirm: bool


class PinIn(BaseModel):
    pin: str


class PlatformIn(BaseModel):
    platform: str


class TemplatesIn(BaseModel):
    create: bool = False


def _fail(e: RelayError) -> HTTPException:
    """The relay's refusal, in its own words (it writes them for the creator).
    A refusal of this PC's signature (401) is the relay's problem, not the
    app's session: it is answered as a bad gateway."""
    return HTTPException(e.status if e.status in (400, 403, 404, 409, 413, 429) else 502, e.message)


def install(app, *, db, data_dir, session_secret: str, publish_worker=None) -> None:
    """Add the /remote-approval routes. `session_secret` is plugins_api.install's answer."""

    def require_session(x_clips_kitty_session: str | None = Header(default=None)) -> None:
        if not session.matches(session_secret, x_clips_kitty_session):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a script "
                                     f"finds it in {session.path(data_dir)}.")

    guarded = [Depends(require_session)]

    def changed(what: str) -> None:
        broadcaster.publish({"type": "remote_approval", "changed": what})
        if publish_worker is not None:
            publish_worker.notify_remote_approval()  # the next relay pass runs now, not in two minutes

    def relay_for(d):
        relay = service.client(d, data_dir)
        if relay is None:
            raise HTTPException(409, "Connect this PC to your relay first.")
        return relay

    def status(d) -> dict:
        view = ra_settings.public_view(d, data_dir)
        open_states = [states.NOTIFICATION_PENDING, *states.OPEN, states.APPROVED, states.PUBLISH_QUEUED,
                       states.PUBLISHING]
        return {
            "settings": view,
            "can_enable_remote_approval": service.can_enable_remote_approval(d),
            "pending": [service.review_view(d, r) for r in store.reviews_in(d, open_states)],
            "outbox_pending": store.pending_outbox(d),
            "usage": usage.summary(d),
        }

    @app.get("/remote-approval/status")
    def remote_status():
        d = db()
        try:
            return status(d)
        finally:
            d.close()

    @app.patch("/remote-approval/settings", dependencies=guarded)
    def remote_settings(body: SettingsPatch):
        patch = body.model_dump(exclude_none=True)
        d = db()
        try:
            wants_approvals = patch.pop("remote_approvals", None)
            try:
                ra_settings.save(d, patch)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            if wants_approvals:
                why = service.can_enable_remote_approval(d)
                if why:
                    raise HTTPException(409, why)
            if wants_approvals is not None:
                ra_settings.save(d, {"remote_approvals": bool(wants_approvals)})
            relay_error = ""
            relay = service.client(d, data_dir)
            if relay is not None:
                try:
                    service.push_preferences(d, relay)
                except RelayError as e:
                    relay_error = e.message
            out = {**status(d), "relay_error": relay_error}
        finally:
            d.close()
        changed("settings")
        return out

    @app.post("/remote-approval/register", dependencies=guarded)
    def remote_register(body: RegisterIn):
        d = db()
        try:
            if body.relay_url:
                try:
                    ra_settings.save(d, {"relay_url": body.relay_url})
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None
            try:
                service.register(d, data_dir, body.enroll_key.strip())
            except RelayError as e:
                raise _fail(e) from None
            out = status(d)
        finally:
            d.close()
        changed("registered")
        return out

    @app.post("/remote-approval/refresh", dependencies=guarded)
    def remote_refresh():
        d = db()
        try:
            try:
                service.refresh_status(d, relay_for(d))
            except RelayError as e:
                raise _fail(e) from None
            return status(d)
        finally:
            d.close()

    @app.post("/remote-approval/pair/start", dependencies=guarded)
    def remote_pair_start():
        """A six-character code, and a wa.me link that opens WhatsApp with it
        typed. The creator sends it from the phone that should get messages."""
        d = db()
        try:
            try:
                got = relay_for(d).pair_start()
            except RelayError as e:
                raise _fail(e) from None
        finally:
            d.close()
        return {k: got.get(k, "") for k in ("code", "text", "link", "number", "expires_at")}

    @app.post("/remote-approval/pair/confirm", dependencies=guarded)
    def remote_pair_confirm(body: ConfirmIn):
        """The PC confirms the number ending in these four digits is the
        creator's. A code alone is never enough: a number that sent it is
        only paired once confirmed here."""
        d = db()
        try:
            try:
                relay = relay_for(d)
                relay.pair_confirm(body.confirm)
                service.refresh_status(d, relay)
            except RelayError as e:
                raise _fail(e) from None
            if not body.confirm:
                ra_settings.save(d, {"remote_approvals": False})
            out = status(d)
        finally:
            d.close()
        changed("pairing")
        return out

    @app.post("/remote-approval/pin", dependencies=guarded)
    def remote_pin(body: PinIn):
        """The approval PIN the phone's page asks for. Sent to the relay,
        which keeps only a slow hash of it; never stored on this PC."""
        d = db()
        try:
            try:
                relay = relay_for(d)
                relay.set_pin(body.pin.strip())
                service.refresh_status(d, relay)
            except RelayError as e:
                raise _fail(e) from None
            out = status(d)
        finally:
            d.close()
        changed("pin")
        return out

    @app.post("/remote-approval/test", dependencies=guarded)
    def remote_test():
        d = db()
        try:
            if not ra_settings.load(d)["whatsapp_enabled"]:
                raise HTTPException(409, "Turn on WhatsApp notifications first.")
            service.send_test(d)
        finally:
            d.close()
        changed("test")
        return {"queued": True}

    @app.post("/remote-approval/disconnect", dependencies=guarded)
    def remote_disconnect():
        d = db()
        try:
            service.disconnect(d, data_dir)
            out = status(d)
        finally:
            d.close()
        changed("disconnected")
        return out

    @app.post("/remote-approval/accounts/connect", dependencies=guarded)
    def remote_accounts_connect(body: PlatformIn):
        """The Meta sign-in page for Facebook (with its Instagram) or Threads,
        on the relay, which keeps the token. The app opens it in the browser."""
        if body.platform not in ("facebook", "threads"):
            raise HTTPException(400, "Connect Facebook (it brings its Instagram account) or Threads.")
        d = db()
        try:
            try:
                got = relay_for(d).accounts_connect(body.platform)
            except RelayError as e:
                raise _fail(e) from None
        finally:
            d.close()
        return {"url": got.get("url", "")}

    @app.post("/remote-approval/accounts/disconnect", dependencies=guarded)
    def remote_accounts_disconnect(body: PlatformIn):
        if body.platform not in ra_settings.PLATFORMS:
            raise HTTPException(400, "Unknown platform.")
        d = db()
        try:
            try:
                relay = relay_for(d)
                relay.accounts_disconnect(body.platform)
                service.refresh_status(d, relay)
            except RelayError as e:
                raise _fail(e) from None
            config = ra_settings.load(d)
            ra_settings.save(d, {"preferred_destinations": [p for p in config["preferred_destinations"]
                                                            if (config["accounts"].get(p) or {}).get("connected")]})
            out = status(d)
        finally:
            d.close()
        changed("accounts")
        return out

    @app.post("/remote-approval/templates", dependencies=guarded)
    def remote_templates(body: TemplatesIn):
        """The WhatsApp message templates' review status, or submit them to
        Meta for review (create)."""
        d = db()
        try:
            try:
                relay = relay_for(d)
                got = relay.templates(create=body.create)
                service.refresh_status(d, relay)
            except RelayError as e:
                raise _fail(e) from None
        finally:
            d.close()
        return got

    @app.get("/remote-approval/reviews")
    def remote_reviews(limit: int = 20):
        d = db()
        try:
            return {"reviews": [service.review_view(d, r) for r in store.recent_reviews(d, max(1, min(limit, 100)))]}
        finally:
            d.close()

    @app.get("/remote-approval/reviews/{review_id}")
    def remote_review(review_id: int):
        d = db()
        try:
            review = store.get_review(d, review_id)
            if review is None:
                raise HTTPException(404, "No such offer.")
            return service.review_view(d, review, detail=True)
        finally:
            d.close()

    def act(review_id: int, what: str):
        d = db()
        try:
            review = store.get_review(d, review_id)
            if review is None:
                raise HTTPException(404, "No such offer.")
            try:
                if what == "cancel":
                    service.cancel_from_app(d, data_dir, review_id)
                    result = {}
                elif what == "retry":
                    result = {"queued": service.retry_failed(d, review_id)}
                    if not result["queued"]:
                        # A post that failed is not sent again once it is up,
                        # or on its way, from somewhere else (commands.queue_publishes).
                        failed = any(p.get("state") == "failed"
                                     for c in (review.get("results") or {}).get("clips") or []
                                     for p in c.get("posts") or [])
                        raise HTTPException(409, "What failed is already posted, on its way or uploading now, "
                                                 "so there is nothing to retry." if failed else
                                                 "Nothing in this offer failed, so there is nothing to retry.")
                elif what == "ask-again":
                    if review["state"] not in (states.EXPIRED, states.CANCELLED):
                        raise HTTPException(409, "Only an expired or cancelled offer can be sent again.")
                    new_id = service.ask_again(d, review_id)
                    if not new_id:
                        # Nothing of its run is left to offer (notifier.offer).
                        raise HTTPException(409, "None of this video's clips can be offered again: they were "
                                                 "removed or made again since, or are posted everywhere already.")
                    result = {"review_id": new_id}
                else:
                    if not service.resend(d, review_id):
                        raise HTTPException(409, "This offer's message is not waiting to be sent again.")
                    result = {}
            except states.IllegalMove:
                raise HTTPException(409, "An upload from this offer has already started, so it can't be cancelled "
                                         "now. Its result will be shown when it finishes.") from None
            out = {**result, "review": service.review_view(d, store.get_review(d, review_id))}
        finally:
            d.close()
        changed(what)
        return out

    @app.post("/remote-approval/reviews/{review_id}/cancel", dependencies=guarded)
    def remote_review_cancel(review_id: int):
        return act(review_id, "cancel")

    @app.post("/remote-approval/reviews/{review_id}/retry", dependencies=guarded)
    def remote_review_retry(review_id: int):
        return act(review_id, "retry")

    @app.post("/remote-approval/reviews/{review_id}/ask-again", dependencies=guarded)
    def remote_review_ask_again(review_id: int):
        return act(review_id, "ask-again")

    @app.post("/remote-approval/reviews/{review_id}/resend", dependencies=guarded)
    def remote_review_resend(review_id: int):
        return act(review_id, "resend")

    @app.get("/remote-approval/audit")
    def remote_audit(limit: int = 100):
        d = db()
        try:
            return {"audit": store.audit_log(d, None, max(1, min(limit, 500)))}
        finally:
            d.close()

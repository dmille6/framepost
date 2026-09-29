"""Platform secrets never reach the logs, the database or the UI.

The leak this exists for: httpx logs every request URL at INFO, the Instagram calls put
the long-lived token in the URL as `access_token=`, and the worker's docker log (no
rotation) kept every one of them. Three layers now stand in the way, and each is pinned
here without a network: the GETs send the token as a header; every log record is masked
(services/redact.py); and error text is masked before it is raised, stored or shown.
"""
import asyncio
import json
import logging
import types
import uuid
from urllib.parse import quote

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from config import settings
from models import PlatformCredential, Post, PostEvent, PostPlatform
from services import comments, http_client, redact, scheduler
from services.platforms import instagram as ig
from services.platforms.instagram import InstagramError

TOKEN = "IGQWRfake0token0SECRET0123456789abcdef"
NEW_TOKEN = "IGQWRrefreshed0SECRET0987654321fedcba"
MASK = redact.MASK


@pytest.fixture(autouse=True)
def _fresh_memory():
    """remember() is process-wide; keep this module's secrets out of other tests."""
    saved = dict(redact._known)
    redact._known.clear()
    yield
    redact._known.clear()
    redact._known.update(saved)


@pytest.fixture()
def logs(caplog):
    redact.install_logging()
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "framepost"):
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


def _everything_logged(caplog) -> str:
    """Rendered messages plus the formatted text (tracebacks included)."""
    return "\n".join([r.getMessage() for r in caplog.records] + [caplog.text])


# -----------------------------------------------------------------------------
# redact() — every pattern
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    f"https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token={TOKEN}",
    f"https://graph.instagram.com/v23.0/c1?access_token={TOKEN}&fields=status_code",
    f"grant_type=refresh_token&refresh_token={TOKEN}",
    f"client_id=cid&client_secret={TOKEN}&code=abc",
    f"/api/platforms/flickr/callback?oauth_token={TOKEN}&oauth_verifier={TOKEN}",
    f"oauth_token_secret={TOKEN}",
    f'OAuth oauth_consumer_key="ck", oauth_token="{TOKEN}", oauth_signature="{TOKEN}%3D"',
    f"app_password={TOKEN}",
    f'{{"identifier": "dmp.bsky.social", "password": "{TOKEN}"}}',
    f'{{"access_token":"{TOKEN}","token_type":"bearer","expires_in":5183944}}',
    f"{{'app_password': '{TOKEN}', 'did': 'did:plc:x'}}",
    f"{{'Authorization': 'Bearer {TOKEN}'}}",
    f"Authorization: Bearer {TOKEN}",
    f"Authorization: OAuth {TOKEN}",
    # A URL that is itself a parameter (a paging.next link, a redirect target).
    f"next={quote(f'https://graph.instagram.com/v23.0/ig1/media?access_token={TOKEN}&after=x', safe='')}",
    f"u=https%3A%2F%2Fx%2Fy%3Fa%3D1%26access_token%3D{TOKEN}",
    # JSON quoted inside JSON: a response body inside a post_events row.
    json.dumps({"error": f'HTTP 400: {{"access_token": "{TOKEN}"}}'}),
    f"X-Amz-Signature={TOKEN}",
])
def test_every_pattern_is_masked(text):
    out = redact.redact(text)
    assert TOKEN not in out
    assert MASK in out


def test_the_name_and_the_rest_of_the_url_survive():
    """The request line stays useful for debugging: only the value goes."""
    out = redact.redact(f"GET https://g/v23.0/c1?fields=status_code&access_token={TOKEN}&x=1")
    assert out == f"GET https://g/v23.0/c1?fields=status_code&access_token={MASK}&x=1"


def test_masked_json_stays_valid_json():
    raw = json.dumps({"error": f'for url "https://g/x?access_token={TOKEN}" {{"refresh_token": "{TOKEN}"}}',
                      "attempt": 2})
    parsed = json.loads(redact.redact(raw))
    assert TOKEN not in parsed["error"] and parsed["attempt"] == 2


@pytest.mark.parametrize("prose", [
    # The wording publish_errors and connection_check classify on — must survive intact.
    "Error validating access token: Session has been invalidated",
    "Invalid OAuth 2.0 access token",
    "OAuthException code 190",
    "Bluesky rejected the credentials (handle or app password is wrong).",
    "status_code=FINISHED",
    "Application request limit reached",
])
def test_prose_about_tokens_is_left_alone(prose):
    assert redact.redact(prose) == prose


def test_a_remembered_value_is_masked_with_no_label_next_to_it():
    redact.remember(TOKEN)
    assert redact.redact(f"session expired for {TOKEN}.") == f"session expired for {MASK}."
    # ...and in its URL-encoded form.
    tricky = TOKEN + "/+="
    redact.remember(tricky)
    assert tricky not in redact.redact(f"x={quote(tricky, safe='')}")
    assert quote(tricky, safe="") not in redact.redact(f"x={quote(tricky, safe='')}")


def test_short_values_are_never_remembered():
    """A short string would mask ordinary words wherever it happened to occur."""
    redact.remember("tok")
    assert redact.redact("token tok") == "token tok"


def test_empty_and_none_pass_through():
    assert redact.redact(None) is None
    assert redact.redact("") == ""


def test_crypto_remembers_what_it_decrypts(monkeypatch):
    import crypto

    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    ciphertext = crypto.encrypt_token(TOKEN)
    redact._known.clear()
    assert crypto.decrypt_token(ciphertext) == TOKEN      # callers still get plaintext
    assert redact.redact(f"echo {TOKEN}") == f"echo {MASK}"


# -----------------------------------------------------------------------------
# Logging — both processes install the same record factory
# -----------------------------------------------------------------------------

def test_placeholders_survive_and_a_split_secret_is_caught(logs):
    log = logging.getLogger("framepost.test")
    log.info("calling %s?access_token=%s", "https://g/me", TOKEN)
    log.warning("status %d for %s", 400, f"https://g/me?access_token={TOKEN}")
    text = _everything_logged(logs)
    assert TOKEN not in text
    assert "status 400 for https://g/me?access_token=" in text


def test_uvicorn_access_lines_keep_their_shape(logs):
    """uvicorn's access formatter unpacks record.args by position; the Flickr callback
    path carries oauth_token and oauth_verifier in its query string."""
    from uvicorn.logging import AccessFormatter

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.2:5000", "GET",
        f"/api/platforms/flickr/callback?oauth_token={TOKEN}&oauth_verifier={TOKEN}", "1.1", 303)
    record = logs.records[-1]
    line = AccessFormatter(use_colors=False).format(record)
    assert TOKEN not in line
    assert "GET /api/platforms/flickr/callback?oauth_token=" in line and "303" in line


def test_a_logged_httpx_traceback_is_masked(logs):
    """httpx exceptions quote the full request URL."""
    req = httpx.Request("GET", f"https://graph.instagram.com/refresh_access_token?access_token={TOKEN}")
    resp = httpx.Response(400, request=req)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        logging.getLogger("framepost.test").exception("refresh blew up")
    text = _everything_logged(logs)
    assert "refresh_access_token" in text        # the traceback is really there
    assert TOKEN not in text


# -----------------------------------------------------------------------------
# A mocked Instagram publish, status poll, refresh and insights sweep
# -----------------------------------------------------------------------------

@pytest.fixture()
def graph(monkeypatch):
    """Meta, answering from memory through real httpx clients, so httpx's own INFO
    request lines are produced exactly as in production."""
    seen: list[httpx.Request] = []
    refresh_fails = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path, q = request.url.path, request.url.params
        if path == "/refresh_access_token":
            if refresh_fails["on"]:
                # Meta echoing the rejected token back — the worst case for error text.
                return httpx.Response(400, json={"error": {
                    "message": f"Invalid token {q.get('access_token')}", "code": 190}})
            return httpx.Response(200, json={"access_token": NEW_TOKEN, "expires_in": 5184000})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "m1"})
        if path.endswith("/insights"):
            return httpx.Response(200, json={"data": [
                {"name": "reach", "values": [{"value": 42}]},
                {"name": "reach", "total_value": {"value": 42}}]})
        if q.get("fields") == "status_code":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if q.get("fields") == "permalink":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/m1/"})
        return httpx.Response(200, json={"followers_count": 7})

    transport = httpx.MockTransport(handler)
    real_client = http_client.client
    monkeypatch.setattr(http_client, "client",
                        lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(ig.time, "sleep", lambda s: None)
    return types.SimpleNamespace(seen=seen, refresh_fails=refresh_fails)


def test_publish_and_status_poll_never_log_the_token(logs, graph):
    ig._await_container("c1", TOKEN, describing="photo")
    media_id, permalink = ig._publish_container("ig1", "c1", TOKEN)
    assert (media_id, permalink) == ("m1", "https://www.instagram.com/p/m1/")

    text = _everything_logged(logs)
    assert "HTTP Request: GET" in text           # the request lines are kept...
    assert TOKEN not in text                     # ...without the token
    # And the GETs no longer carry it in the URL at all; it rides in the header.
    gets = [r for r in graph.seen if r.method == "GET"]
    assert gets and all("access_token" not in str(r.url) for r in gets)
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in gets)


def test_token_refresh_keeps_its_query_parameter_but_not_in_the_log(logs, graph, monkeypatch):
    monkeypatch.setattr(ig, "decrypt_token", lambda c: TOKEN)
    monkeypatch.setattr(ig, "encrypt_token", lambda p: "enc")
    row = types.SimpleNamespace(access_token="enc", token_expires=None,
                                auth_status=None, auth_error=None, auth_flagged_at=None)
    ig._refresh(types.SimpleNamespace(commit=lambda: None), row)

    assert f"access_token={TOKEN}" in str(graph.seen[-1].url)   # documented form, unchanged
    text = _everything_logged(logs)
    assert "refresh_access_token" in text
    assert TOKEN not in text and NEW_TOKEN not in text


def test_insights_sweep_never_logs_the_token(logs, graph, db, monkeypatch):
    db.add(PlatformCredential(id=uuid.uuid4().hex, platform="instagram", access_token="enc",
                              extra_json=json.dumps({"ig_user_id": "ig1"})))
    db.commit()
    monkeypatch.setattr(comments, "decrypt_token", lambda c: TOKEN)
    comments.sync_instagram_account_stats(db)
    with http_client.client() as c:
        assert comments._fetch_ig_media_insights(
            c, {"Authorization": f"Bearer {TOKEN}"}, "m1") == {"reach": 42}

    text = _everything_logged(logs)
    assert "insights" in text and TOKEN not in text


# -----------------------------------------------------------------------------
# Error paths — raised, stored, shown
# -----------------------------------------------------------------------------

def test_a_failed_refresh_raises_and_logs_without_the_token(logs, graph, monkeypatch):
    """Meta's error echoes the token in prose, with no `access_token=` beside it. Only
    the value itself can catch that, and real crypto is what teaches it the value."""
    import crypto

    graph.refresh_fails["on"] = True
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    row = types.SimpleNamespace(access_token=crypto.encrypt_token(TOKEN), token_expires=None)
    redact._known.clear()           # learnt only from the decrypt inside _refresh
    with pytest.raises(InstagramError) as exc:
        ig._refresh(types.SimpleNamespace(commit=lambda: None), row)
    assert "Token refresh failed" in str(exc.value)
    assert TOKEN not in str(exc.value)
    assert TOKEN not in _everything_logged(logs)


def test_a_stored_failure_holds_no_token(db, logs):
    """The worst case: a raw httpx error (not a platform error, so never masked at
    birth) reaches the fan-out failure recorder, which writes it to four places."""
    redact.remember(NEW_TOKEN)   # as crypto would have, decrypting it for the call
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="instagram",
                              access_token="enc", account_name="acct")
    post = Post(id=uuid.uuid4().hex, status="posted", title="t",
                original_filename="f.arw", original_path="/nope/f.arw")
    db.add_all([cred, post])
    db.commit()
    req = httpx.Request("GET", f"https://graph.instagram.com/v23.0/c1?access_token={TOKEN}")
    err = httpx.HTTPStatusError(f"Server error '503' for url '{req.url}' (session {NEW_TOKEN})",
                                request=req, response=httpx.Response(503, request=req))

    scheduler._record_platform_failure(db, post, cred, err)

    pp = db.get(PostPlatform, (post.id, cred.id))
    cred = db.get(PlatformCredential, cred.id)
    stored = [pp.error_message, cred.last_error, cred.auth_error or ""]
    stored += [e.details or "" for e in db.query(PostEvent).filter_by(post_id=post.id)]
    assert pp.error_message and "access_token=" in pp.error_message   # it did get stored
    for text in stored:
        assert TOKEN not in text and NEW_TOKEN not in text
    for ev in db.query(PostEvent).filter_by(post_id=post.id):
        json.loads(ev.details)                  # masking left the JSON parseable
    assert TOKEN not in _everything_logged(logs)


def test_every_masked_column_masks_on_assignment(db):
    reel_err = f"https://x/?access_token={TOKEN}"
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="pixelfed", last_error=reel_err)
    cred.auth_error = f"Authorization: Bearer {TOKEN}"
    db.add(cred)
    db.commit()
    db.refresh(cred)
    assert TOKEN not in cred.last_error and TOKEN not in cred.auth_error


@pytest.mark.parametrize("cls", ["instagram", "flickr", "bluesky", "pixelfed", "pinterest"])
def test_platform_errors_are_masked_at_birth(cls):
    from services.platforms import bluesky, flickr, instagram, pinterest, pixelfed

    err_cls = {"instagram": instagram.InstagramError, "flickr": flickr.FlickrError,
               "bluesky": bluesky.BlueskyError, "pixelfed": pixelfed.PixelfedError,
               "pinterest": pinterest.PinterestError}[cls]
    err = err_cls(f"transport failed: GET https://x/?access_token={TOKEN}")
    assert TOKEN not in str(err)


def test_http_error_details_are_masked():
    import main

    resp = asyncio.run(main._masked_http_exception(
        None, HTTPException(502, f"Flickr error: https://x/?oauth_token={TOKEN}")))
    assert TOKEN not in resp.body.decode()
    assert resp.status_code == 502


def test_oauth_redirect_reasons_are_masked():
    from routes import platforms

    resp = platforms._pixelfed_redirect_back(f"Pixelfed exchange failed: client_secret={TOKEN}")
    assert TOKEN not in resp.headers["location"]


# -----------------------------------------------------------------------------
# OAuth callback codes — nginx and uvicorn access logs
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    f"/api/platforms/pinterest/callback?code={TOKEN}&state=s1",
    f"/api/platforms/pixelfed/callback?state=s1&code={TOKEN}",
    "/cb?code=9f8e7d6c",
    f"next=https%3A%2F%2Fx%2Fcb%3Fcode%3D{TOKEN}%26state%3Dz",
])
def test_an_oauth_code_in_a_query_string_is_masked(text):
    out = redact.redact(text)
    assert TOKEN not in out and "9f8e7d6c" not in out and MASK in out


@pytest.mark.parametrize("text", [
    "Use promo code SAVE20 at checkout",                 # a caption
    "Scan the QR code=link in bio",
    "flickr error code=98: Invalid auth token",          # publish_errors classifies on it
    '{"error": {"message": "x", "code": 190}}',
    '{"code": "abcdef"}',
    "?code=190&fields=status_code",                        # numeric: an error code, not OAuth
    "status_code=FINISHED",
    "zipcode=abc12",
    "code=abc",                                            # not in a query string
])
def test_text_that_merely_says_code_is_untouched(text):
    assert redact.redact(text) == text


def test_uvicorn_masks_the_pinterest_callback_code(logs):
    from uvicorn.logging import AccessFormatter

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.2:5000", "GET",
        f"/api/platforms/pinterest/callback?code={TOKEN}&state=abc", "1.1", 303)
    line = AccessFormatter(use_colors=False).format(logs.records[-1])
    assert TOKEN not in line and "callback?code=" in line


def test_nginx_access_log_records_no_query_string_or_referer():
    """Tests run in the backend container, which has no nginx/; skip there."""
    import pathlib
    import re as _re

    conf_path = pathlib.Path(__file__).resolve().parents[2] / "nginx" / "nginx.conf"
    if not conf_path.exists():
        pytest.skip("nginx/nginx.conf is not mounted here")
    conf = conf_path.read_text()
    code = "\n".join(line.split("#", 1)[0] for line in conf.splitlines())   # drop comments

    fmt = _re.search(r"log_format\s+framepost\s+((?:'[^']*'\s*)+);", code)
    assert fmt, "framepost log_format missing"
    fields = fmt.group(1)
    for leaky in ("$request ", "$request\"", "$request_uri", "$args", "$query_string",
                  "$http_referer", "$arg_"):
        assert leaky not in fields, leaky
    assert "$uri" in fields
    assert _re.search(r"^\s*access_log\s+\S+\s+framepost\s*;", code, _re.M)
    # Every access_log either uses the safe format or is off.
    for m in _re.finditer(r"access_log\s+([^;]+);", code):
        assert m.group(1).strip() == "off" or m.group(1).split()[-1] == "framepost"
    # The proxied request is unchanged: the backend still gets the query string.
    assert "proxy_pass http://$backend_host:8000$request_uri;" in code


# -----------------------------------------------------------------------------
# First-use credentials: known to redact() before the first request goes out
# -----------------------------------------------------------------------------

def _serve(monkeypatch, handler):
    """Route every http_client.client through `handler` (real httpx, no network)."""
    transport = httpx.MockTransport(handler)
    real_client = http_client.client
    monkeypatch.setattr(http_client, "client",
                        lambda **kw: real_client(transport=transport, **kw))


def _echo_bearer(request: httpx.Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ")


def test_a_pasted_instagram_token_is_masked_when_me_rejects_it(monkeypatch, logs, db):
    """Meta's error text echoes the token in prose; crypto hasn't seen it yet."""
    _serve(monkeypatch, lambda req: httpx.Response(400, json={"error": {
        "message": f"Cannot parse access token {_echo_bearer(req)}", "code": 190}}))
    with pytest.raises(InstagramError) as exc:
        ig.connect(db, access_token=f"  {TOKEN}  ")
    assert TOKEN not in str(exc.value)
    assert TOKEN not in _everything_logged(logs)


def test_a_new_bluesky_app_password_is_masked_when_create_session_fails(monkeypatch, logs):
    from services.platforms import bluesky

    password = "abcd-efgh-ijkl-mnop"
    _serve(monkeypatch, lambda req: httpx.Response(500, text=(
        f"upstream said: bad login for {json.loads(req.content)['password']}")))
    with pytest.raises(bluesky.BlueskyError) as exc:
        bluesky._create_session("dmp.bsky.social", password)
    assert password not in str(exc.value)
    assert password not in _everything_logged(logs)


def test_fresh_bluesky_jwts_are_known_before_they_are_stored(monkeypatch):
    from services.platforms import bluesky

    jwt_a, jwt_r = "eyJhbGciOi.access." + "a" * 20, "eyJhbGciOi.refresh." + "b" * 20
    _serve(monkeypatch, lambda req: httpx.Response(200, json={
        "did": "did:plc:x", "handle": "dmp.bsky.social",
        "accessJwt": jwt_a, "refreshJwt": jwt_r}))
    bluesky._create_session("dmp.bsky.social", "abcd-efgh-ijkl-mnop")
    assert redact.redact(f"{jwt_a} {jwt_r}") == f"{MASK} {MASK}"


def _pending_row(db, platform, pending):
    from services.platforms import credentials

    row = credentials.upsert(db, platform)
    credentials.set_pending_oauth(row, pending)
    db.commit()
    return row


def test_a_fresh_pinterest_token_is_masked_when_user_account_fails(monkeypatch, logs, db):
    from services.platforms import pinterest

    monkeypatch.setattr(settings, "pinterest_app_id", "app-id")
    monkeypatch.setattr(settings, "pinterest_app_secret", "app-secret-value")
    _pending_row(db, "pinterest", {"state": "s1", "redirect_uri": "https://fp/cb"})

    def handler(req):
        if req.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": TOKEN, "refresh_token": NEW_TOKEN,
                                             "expires_in": 3600})
        return httpx.Response(500, text=f"no account for token {_echo_bearer(req)}")
    _serve(monkeypatch, handler)

    code = "pinterest-auth-code-123"
    with pytest.raises(pinterest.PinterestError) as exc:
        pinterest.complete_connect(db, code=code, state="s1")
    assert TOKEN not in str(exc.value)
    assert redact.redact(f"{NEW_TOKEN} {code}") == f"{MASK} {MASK}"
    assert TOKEN not in _everything_logged(logs)


def test_a_fresh_pixelfed_token_is_masked_when_verify_credentials_fails(monkeypatch, logs, db):
    import crypto
    from services.platforms import pixelfed

    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    _pending_row(db, "pixelfed", {
        "instance_url": "https://pixelfed.test", "client_id": "cid", "state": "s1",
        "client_secret": crypto.encrypt_token("pixelfed-client-secret"),
        "redirect_uri": "https://fp/cb"})

    def handler(req):
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": TOKEN})
        return httpx.Response(500, text=f"token {_echo_bearer(req)} is not valid here")
    _serve(monkeypatch, handler)

    with pytest.raises(pixelfed.PixelfedError) as exc:
        pixelfed.complete_connect(db, code="pixelfed-auth-code-1", state="s1")
    assert TOKEN not in str(exc.value)
    assert TOKEN not in _everything_logged(logs)


def test_a_new_pixelfed_client_secret_is_known_at_registration(monkeypatch, db):
    import crypto
    from services.platforms import pixelfed

    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    _serve(monkeypatch, lambda req: httpx.Response(200, json={
        "client_id": "cid", "client_secret": "brand-new-client-secret"}))
    pixelfed.begin_connect(db, instance_url="https://pixelfed.test",
                           redirect_uri="https://fp/cb")
    assert redact.redact("brand-new-client-secret") == MASK


# -----------------------------------------------------------------------------
# Mask, then truncate: a secret cut in half by a slice must not leave a fragment
# -----------------------------------------------------------------------------

FRAGMENT = TOKEN[:8]      # what a cut-then-mask would leave behind


def _straddling(limit: int) -> str:
    """Text whose `limit`-character cut keeps TOKEN's first 12 characters, unlabelled."""
    return "x" * (limit - 13) + " " + TOKEN + " tail"


def test_clip_masks_before_it_cuts():
    redact.remember(TOKEN)
    out = redact.clip(_straddling(200), 200)
    assert FRAGMENT not in out and len(out) <= 200


def test_clip_handles_none_and_short_text():
    assert redact.clip(None, 10) == ""
    assert redact.clip("short", 10) == "short"


def test_the_failure_recorder_masks_before_truncating(db):
    redact.remember(TOKEN)
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="instagram",
                              access_token="enc", account_name="acct")
    post = Post(id=uuid.uuid4().hex, status="posted", title="t",
                original_filename="f.arw", original_path="/nope/f.arw")
    db.add_all([cred, post])
    db.commit()

    # 400 is where the user-facing message cuts; 500 where last_error does.
    scheduler._record_platform_failure(db, post, cred, RuntimeError(_straddling(400)))
    pp = db.get(PostPlatform, (post.id, cred.id))
    cred = db.get(PlatformCredential, cred.id)
    stored = [pp.error_message, cred.last_error]
    stored += [e.details for e in db.query(PostEvent).filter_by(post_id=post.id)]
    for text in stored:
        assert FRAGMENT not in text

    scheduler._record_platform_failure(db, post, cred, RuntimeError(_straddling(500)))
    assert FRAGMENT not in db.get(PlatformCredential, cred.id).last_error


def test_instagram_error_text_masks_before_truncating():
    redact.remember(TOKEN)
    r = httpx.Response(500, text=_straddling(300))          # not JSON: the raw-body path
    with pytest.raises(InstagramError) as exc:
        ig._raise_api_error(r, "container status check")
    assert FRAGMENT not in str(exc.value)
    r = httpx.Response(400, json={"error": {"message": f"bad token {TOKEN}"}})
    assert TOKEN not in ig._error_text(r)


def test_a_platform_body_cut_at_200_leaves_no_fragment(monkeypatch):
    from services.platforms import bluesky

    redact.remember(TOKEN)
    _serve(monkeypatch, lambda req: httpx.Response(500, text=_straddling(200)))
    with pytest.raises(bluesky.BlueskyError) as exc:
        bluesky._refresh_session("refresh-jwt-value-xyz")
    assert FRAGMENT not in str(exc.value)


def test_the_connection_check_state_row_is_masked(db, monkeypatch):
    """connection_check keeps its last error in app_config, which has no listener."""
    import json as _json
    from datetime import datetime, timedelta

    from models import AppConfig
    from services import connection_check as cc

    now = datetime(2026, 10, 1, 12, 0)
    redact.remember(TOKEN)
    db.add(PlatformCredential(id=uuid.uuid4().hex, platform="instagram", access_token="tok"))
    db.add(Post(id=uuid.uuid4().hex, status="pending", scheduled_at=now + timedelta(minutes=30),
                target_platforms=_json.dumps(["instagram"])))
    db.commit()

    def boom(db, cred):
        raise RuntimeError(_straddling(300))
    monkeypatch.setattr(cc, "VERIFIERS", {"instagram": boom})
    cc.run(db, now=now)
    state = db.get(AppConfig, cc.STATE_KEY).value
    assert "error" in state and FRAGMENT not in state

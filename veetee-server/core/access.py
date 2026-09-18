"""Transport authentication and shared admission, independent of AI semantics."""
import asyncio
import hashlib
import hmac
import json
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit


class WebSocketAccess:
    """Authenticate dashboard managers and paired stock Xiaozhi devices."""

    def __init__(self, config, management_config=None, device_store=None):
        self.config = config
        self.management_config = management_config
        self.device_store = device_store
        self.active = 0

    def origin_allowed(self, headers):
        origin = headers.get("Origin")
        if origin is None:
            return True  # Stock firmware isn't a browser.
        if origin in self.config.ws_allowed_origins:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return (parsed.scheme in {"http", "https"} and not parsed.path
                and not parsed.query and not parsed.fragment
                and parsed.netloc == headers.get("Host"))

    def _management_token(self) -> str:
        config = self.management_config
        return str(getattr(config, "token", "") or "").strip()

    def manager_token_valid(self, supplied: str) -> bool:
        expected = self._management_token()
        supplied = str(supplied or "").strip()
        return bool(expected and supplied) and hmac.compare_digest(supplied, expected)

    def manager_cookie(self):
        token = self._management_token()
        if not token:
            raise RuntimeError("management token is not configured")
        expiry = str(int(time.time()) + 3600)
        signature = hmac.new(token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        return f"{expiry}.{signature}"

    def _manager_cookie_valid(self, headers) -> bool:
        token = self._management_token()
        if not token:
            return False
        try:
            cookie = SimpleCookie(headers.get("Cookie", ""))
            value = cookie["veetee_session"].value
            expiry, signature = value.split(".", 1)
            now = time.time()
            if not now < int(expiry) <= now + 3600:
                return False
            expected = hmac.new(token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (KeyError, ValueError, TypeError, CookieError):
            return False

    @staticmethod
    def _bearer(headers) -> str:
        authorization = str(headers.get("Authorization", "") or "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return ""

    def authenticate(self, headers):
        """Return auth context or None. Device identity is bound to its credential."""
        if self._manager_cookie_valid(headers):
            return {"kind": "manager", "assistant": None, "device": None}

        token = self._bearer(headers)
        device_id = str(headers.get("Device-Id", headers.get("device-id", "")) or "").strip()
        client_id = str(headers.get("Client-Id", headers.get("client-id", "")) or "").strip()
        if self.device_store is not None and token and device_id and client_id:
            auth = self.device_store.authenticate_device(device_id, client_id, token)
            if auth:
                return auth
        return None

    def authenticated(self, headers):
        return self.authenticate(headers) is not None

    def admit(self):
        # No await between checking and incrementing: shared by both listeners.
        if self.active >= self.config.ws_max_sessions:
            return False
        self.active += 1
        return True

    def release(self):
        self.active = max(0, self.active - 1)


def valid_hello(message):
    try:
        data = json.loads(message) if isinstance(message, str) else None
        return (isinstance(data, dict) and data.get("type") == "hello"
                and type(data.get("version", 1)) is int
                and data.get("version", 1) in (1, 2, 3)
                and isinstance(data.get("features", {}), dict)
                and isinstance(data.get("audio_params", {}), dict))
    except (ValueError, TypeError):
        return False


async def serve_session(transport, access, config, sessions, factory):
    """Reserve before awaits, require stock hello before allocating ASR."""
    if not access.admit():
        await transport.close(code=1013, reason="session_limit")
        return
    session = None
    try:
        iterator = transport.__aiter__()
        try:
            first = await asyncio.wait_for(anext(iterator), config.server.ws_hello_timeout_seconds)
        except asyncio.TimeoutError:
            await transport.close(code=1008, reason="hello_timeout")
            return
        if not valid_hello(first):
            await transport.close(code=1008, reason="hello_required")
            return
        session = factory()
        sessions[session.session_id] = session
        try:
            await asyncio.wait_for(session.initialize(), config.server.ws_hello_timeout_seconds)
        except asyncio.TimeoutError:
            await transport.close(code=1008, reason="hello_timeout")
            return
        await session.handle_message(first)
        async for message in iterator:
            await session.handle_message(message)
    except StopAsyncIteration:
        pass
    finally:
        try:
            if session is not None:
                await session.close()
        finally:
            if session is not None:
                sessions.pop(session.session_id, None)
            access.release()

"""Real Zalo bridge using zlapi (zalo-api package) with Supabase session persistence."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, get_settings
from app.core.rate_limiter import RateLimiter
from app.repositories.supabase_repo import SupabaseRepo
from app.services.message_pipeline import MessagePipeline
from app.services.zalo_bridge import ZaloBridge

from app.services import zlapi_patch  # noqa: F401 — patch zlapi before use

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
RECONNECT_BACKOFFS = (30, 60, 120)
PERSIST_RETRY_INTERVAL_SEC = 60
QR_PATH = Path("data/zalo_qr.png")


class RealZaloBridge(ZaloBridge):
    """Production-oriented Zalo bridge with QR login and Supabase session restore."""

    def __init__(
        self,
        pipeline: MessagePipeline | None = None,
        repo: SupabaseRepo | None = None,
        settings: Settings | None = None,
        rate_limiter: RateLimiter | None = None,
        *,
        zalo_client_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pipeline = pipeline or MessagePipeline(settings=self.settings)
        self.repo = repo or SupabaseRepo(self.settings)
        self._rate_limiter = rate_limiter or RateLimiter(
            max_per_min=self.settings.zalo_max_msg_per_min,
            min_delay_sec=self.settings.zalo_min_delay_sec,
            max_delay_sec=self.settings.zalo_max_delay_sec,
        )
        self._zalo_client_factory = zalo_client_factory or self._default_client_factory
        self._sleep = sleep or time.sleep

        self._client: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._login_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = "awaiting_qr"
        self._qr_image_b64: str | None = None
        self._reconnect_attempt = 0
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persist_retry_thread: threading.Thread | None = None
        self._persist_retry_stop = threading.Event()

    @property
    def status(self) -> str:
        return self._status

    @property
    def qr_image_b64(self) -> str | None:
        return self._qr_image_b64

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            self.repo.ensure_tables()
        except Exception:
            logger.warning("Could not ensure Supabase tables during startup", exc_info=True)
        threading.Thread(target=self._bootstrap, daemon=True, name="zalo-bootstrap").start()

    async def on_message(self, event: dict[str, Any]) -> None:
        await self._handle_inbound_event(event)

    async def send(self, group_id: str, text: str) -> None:
        await self._rate_limiter.wait_before_send()
        client = self._client
        if client is None or self._status != "connected":
            raise RuntimeError("Zalo client is not connected")

        from zlapi import Message
        from zlapi.models import ThreadType

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: client.sendMessage(Message(text=text), group_id, ThreadType.GROUP),
        )
        logger.info("RealZaloBridge sent group message to %s", group_id)

    def get_status(self) -> dict[str, str]:
        return {"status": self._status}

    def persist_session_now(self) -> bool:
        """Force-save the current Zalo session to Supabase."""
        return self._persist_session()

    def refresh_qr_login(self) -> bool:
        """Discard stale QR/login thread and start a fresh QR login flow."""
        with self._lock:
            if self._status == "connected":
                return False
            self._status = "awaiting_qr"
            self._qr_image_b64 = None
            self._client = None
            self._login_thread = None
        if QR_PATH.exists():
            QR_PATH.unlink(missing_ok=True)
        logger.info("Refreshing Zalo QR login")
        self._start_qr_login()
        return True

    def render_qr_html(self, poll_url: str) -> str:
        qr_src = (
            f"data:image/png;base64,{self._qr_image_b64}"
            if self._qr_image_b64
            else ""
        )
        return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <title>Zalo QR Login</title>
  <style>
    body {{ font-family: sans-serif; max-width: 520px; margin: 2rem auto; text-align: center; }}
    #status {{ margin-top: 1rem; font-size: 1.1rem; }}
    img {{ width: 280px; height: 280px; border: 1px solid #ddd; }}
  </style>
</head>
<body>
  <h1>Quét mã QR Zalo</h1>
  <p>Mở Zalo trên điện thoại và quét mã bên dưới.</p>
  {"<img id='qr' src='" + qr_src + "' alt='QR code' />" if qr_src else "<p id='qr'>Đang tạo mã QR...</p>"}
  <div id="status">Trạng thái: {self._status}</div>
  <script>
    async function poll() {{
      const res = await fetch("{poll_url}", {{
        headers: {{ "X-Admin-Token": new URLSearchParams(window.location.search).get("token") || "" }}
      }});
      const data = await res.json();
      const el = document.getElementById("status");
      if (data.status === "connected") {{
        el.textContent = "ĐÃ KẾT NỐI - anh có thể đóng trang";
        el.style.color = "green";
        el.style.fontWeight = "bold";
      }} else {{
        el.textContent = "Trạng thái: " + data.status;
        setTimeout(poll, 5000);
      }}
    }}
    poll();
  </script>
</body>
</html>"""

    def _default_client_factory(self, **kwargs: Any) -> Any:
        from app.services import zlapi_patch  # noqa: F401
        from zlapi import ZaloAPI

        return ZaloAPI(
            phone=kwargs.get("phone"),
            password=kwargs.get("password"),
            imei=kwargs.get("imei"),
            session_cookies=kwargs.get("session_cookies"),
            user_agent=kwargs.get("user_agent", DEFAULT_USER_AGENT),
            auto_login=kwargs.get("auto_login", False),
        )

    def _bootstrap(self) -> None:
        session = self.repo.load_session()
        if session and self._try_restore_session(session):
            self._start_listener()
            return
        self._status = "awaiting_qr"
        self._start_qr_login()

    def _try_restore_session(self, payload: dict[str, Any]) -> bool:
        try:
            cookies = payload.get("cookies") or {}
            imei = payload.get("imei")
            user_agent = payload.get("user_agent", DEFAULT_USER_AGENT)
            config = payload.get("config")
            if not cookies or not imei:
                return False

            client = self._zalo_client_factory(
                phone=None,
                password=None,
                imei=None,
                session_cookies=None,
                user_agent=user_agent,
                auto_login=False,
            )
            client.setSession(cookies)
            client._imei = imei
            client._state.user_imei = imei
            if user_agent:
                client._state._headers["User-Agent"] = user_agent

            if isinstance(config, dict) and config.get("secret_key"):
                client._state._config = config
                client._state._loggedin = True
                client._state.user_id = config.get("send2me_id")
                client.uid = config.get("send2me_id")
            else:
                client._state.login(None, None, imei, user_agent=user_agent)

            if not client.isLoggedIn():
                return False
            self._client = client
            self._status = "connected"
            self._bind_message_handler()
            logger.info("Restored Zalo session from Supabase")
            return True
        except Exception:
            logger.warning("Failed to restore Zalo session", exc_info=True)
            self._status = "awaiting_qr"
            return False

    def _start_qr_login(self) -> None:
        if self._login_thread and self._login_thread.is_alive():
            return

        def _run() -> None:
            try:
                client = self._zalo_client_factory(
                    phone=None,
                    password=None,
                    imei=None,
                    auto_login=False,
                )

                def on_qr_generated(path: str) -> None:
                    qr_file = Path(path)
                    if qr_file.exists():
                        self._qr_image_b64 = base64.b64encode(qr_file.read_bytes()).decode(
                            "ascii"
                        )

                client.loginWithQR(
                    user_agent=DEFAULT_USER_AGENT,
                    qr_path=str(QR_PATH),
                    on_qr_generated=on_qr_generated,
                )
                self._client = client
                self._status = "connected"
                self._reconnect_attempt = 0
                self._persist_session()
                self._bind_message_handler()
                self._start_listener()
            except Exception:
                logger.error("QR login failed", exc_info=True)
                with self._lock:
                    self._status = "awaiting_qr"
                    self._login_thread = None

        self._login_thread = threading.Thread(target=_run, daemon=True, name="zalo-qr-login")
        self._login_thread.start()

    def _persist_session(self) -> bool:
        if self._client is None:
            return False
        payload = {
            "cookies": self._client.getSession(),
            "imei": getattr(self._client, "_imei", None),
            "user_agent": DEFAULT_USER_AGENT,
            "config": getattr(self._client._state, "_config", None),
        }
        try:
            self.repo.save_session(payload)
            self._persist_retry_stop.set()
            logger.info("Zalo session persisted to Supabase")
            return True
        except Exception as exc:
            logger.warning("Session persist failed: %s", exc)
            self._ensure_persist_retry_running()
            return False

    def _ensure_persist_retry_running(self) -> None:
        if self._persist_retry_thread and self._persist_retry_thread.is_alive():
            return
        self._persist_retry_stop.clear()

        def _retry_loop() -> None:
            while not self._persist_retry_stop.wait(PERSIST_RETRY_INTERVAL_SEC):
                if self._client is None or self._status != "connected":
                    return
                if self._persist_session():
                    return
                logger.warning("Session persist retry failed, next in 60s")

        self._persist_retry_thread = threading.Thread(
            target=_retry_loop,
            daemon=True,
            name="zalo-persist-retry",
        )
        self._persist_retry_thread.start()

    def _bind_message_handler(self) -> None:
        if self._client is None:
            return

        bridge = self

        def _on_message(
            mid,
            author_id,
            message,
            message_object,
            thread_id,
            thread_type,
        ) -> None:
            from zlapi.models import ThreadType

            if thread_type == ThreadType.USER:
                return

            text = message if isinstance(message, str) else str(message or "")
            sender_name = getattr(message_object, "dName", "") or ""
            event = {
                "group_id": str(thread_id),
                "sender_id": str(author_id),
                "sender_name": str(sender_name),
                "sender_gender": "unknown",
                "text": text,
            }
            try:
                bridge.repo.log_message(
                    group_id=event["group_id"],
                    sender_id=event["sender_id"],
                    sender_name=event["sender_name"],
                    gender=event["sender_gender"],
                    text=event["text"],
                )
            except Exception:
                logger.warning("Failed to log Zalo group message", exc_info=True)

            loop = getattr(bridge, "_loop", None)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    bridge._handle_inbound_event(event),
                    loop,
                )
            else:
                asyncio.run(bridge._handle_inbound_event(event))

        self._client.onMessage = _on_message
        self._client.onDisconnected = lambda *args, **kwargs: bridge._handle_disconnect()

    def _start_listener(self) -> None:
        if self._client is None:
            return
        if self._listen_thread and self._listen_thread.is_alive():
            return

        def _run() -> None:
            try:
                self._client.startListening(type="websocket", thread=True, reconnect=5)
            except Exception:
                logger.warning("Zalo listener stopped", exc_info=True)
                self._handle_disconnect()

        self._listen_thread = threading.Thread(target=_run, daemon=True, name="zalo-listener")
        self._listen_thread.start()

    def _handle_disconnect(self) -> None:
        with self._lock:
            if self._status == "awaiting_qr":
                return
            self._status = "reconnecting"
            attempt = min(self._reconnect_attempt, len(RECONNECT_BACKOFFS) - 1)
            delay = RECONNECT_BACKOFFS[attempt]
            self._reconnect_attempt += 1

        self._sleep(delay)

        session = self.repo.load_session()
        if session and self._try_restore_session(session):
            with self._lock:
                self._status = "connected"
                self._reconnect_attempt = 0
            self._start_listener()
            return

        with self._lock:
            self._status = "awaiting_qr"
            self._client = None
        self._start_qr_login()

    async def _handle_inbound_event(self, event: dict[str, Any]) -> None:
        result = await self.pipeline.handle(event)
        if result is None:
            return
        await self.send(str(event["group_id"]), str(result["answer"]))

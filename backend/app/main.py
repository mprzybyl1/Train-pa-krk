"""FastAPI: /api/departures, /api/health, push (faza 2) i statyczny frontend PWA."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings, load_settings
from .push import Notifier, label_for, make_sender, platform_label
from .service import DIRECTIONS, TrainService, _iso
from .store import Store
from .worker import Worker

log = logging.getLogger("app")
MAX_SUBSCRIPTIONS = 20


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str
    keys: PushKeys


class WatchRequest(BaseModel):
    endpoint: str
    trainId: str
    direction: str


class EndpointRequest(BaseModel):
    endpoint: str


def _build_source(settings: Settings):
    if settings.demo:
        from .demo import DemoSource
        return DemoSource(settings.poznan_id, settings.krakow_id)
    if settings.plk_api_key:
        from .plk.client import PlkClient
        return PlkClient(settings.plk_api_key, settings.plk_base_url)
    return None


def create_app(settings: Settings | None = None, store: Store | None = None, source=None,
               start_worker: bool | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(settings.db_path)
    service = TrainService(settings, store)
    notifier = (Notifier(store, make_sender(settings.vapid_private_key, settings.vapid_subject),
                         settings.push_delay_threshold) if settings.push_enabled else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        src = source if source is not None else None
        run = settings.worker_enabled if start_worker is None else start_worker
        if run:
            src = src or _build_source(settings)
            if src is None:
                log.warning("Brak PLK_API_KEY — worker nie startuje, serwuję tylko cache.")
            else:
                worker = Worker(service, src, notifier.check if notifier else None)
                app.state.worker = worker
                task = asyncio.create_task(worker.run())
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if src is not None:
            await src.aclose()

    app = FastAPI(title="Pociągi PZ ⇄ KRK", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = service
    app.state.store = store

    def _direction(direction: str) -> str:
        if direction not in DIRECTIONS:
            raise HTTPException(400, f"direction musi być jednym z: {', '.join(DIRECTIONS)}")
        return direction

    @app.get("/api/departures")
    def departures(direction: str = Query("pz-krk")):
        data = service.departures_response(_direction(direction))
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        _, sched_at = service.trips("pz-krk")
        _, ops_at = service.live()
        return {
            "ok": True,
            "demo": settings.demo,
            "hasApiKey": bool(settings.plk_api_key),
            "pollSeconds": settings.poll_seconds,
            "quietHours": [t.isoformat("minutes") for t in settings.quiet_hours] if settings.quiet_hours else None,
            "inQuietHours": service.in_quiet_hours(),
            "scheduleFetchedAt": _iso(sched_at),
            "liveFetchedAt": _iso(ops_at),
            "usage": store.get_meta("usage"),
            "rateLimits": store.get_meta("rate_limits"),
            "lastError": store.get_meta("last_error"),
            "push": settings.push_enabled,
        }

    # --- faza 2: web push ---
    def _need_push():
        if not settings.push_enabled:
            raise HTTPException(503, "Powiadomienia wyłączone: brak VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY")

    def _sub_id(endpoint: str) -> int:
        sub = store.subscription_by_endpoint(endpoint)
        if sub is None:
            raise HTTPException(404, "Nieznana subskrypcja — włącz powiadomienia ponownie")
        return sub.id

    @app.get("/api/push/config")
    def push_config():
        return {"enabled": settings.push_enabled, "publicKey": settings.vapid_public_key or None,
                "delayThreshold": settings.push_delay_threshold}

    @app.post("/api/push/subscribe")
    def push_subscribe(sub: PushSubscription):
        _need_push()
        if not sub.endpoint.startswith("https://"):
            raise HTTPException(400, "Niepoprawny endpoint")
        if store.subscription_by_endpoint(sub.endpoint) is None and store.count_subscriptions() >= MAX_SUBSCRIPTIONS:
            raise HTTPException(429, "Za dużo subskrypcji")
        store.upsert_subscription(sub.endpoint, sub.keys.p256dh, sub.keys.auth)
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(req: EndpointRequest):
        sub = store.subscription_by_endpoint(req.endpoint)
        if sub:
            store.delete_subscription(sub.id)
        return {"ok": True}

    @app.post("/api/watches/list")
    def watches_list(req: EndpointRequest):
        sub = store.subscription_by_endpoint(req.endpoint)
        return {"trainIds": [w.train_id for w in store.watches(sub.id)] if sub else []}

    @app.post("/api/watches")
    def watch_add(req: WatchRequest):
        _need_push()
        sub_id = _sub_id(req.endpoint)
        dep = next((d for d in service.board(_direction(req.direction)) if d.id == req.trainId), None)
        if dep is None:
            raise HTTPException(404, "Tego pociągu nie ma już na tablicy")
        store.add_watch(sub_id, dep.id, req.direction, label_for(dep), dep.delay, platform_label(dep), dep.cancelled)
        return {"ok": True}

    @app.post("/api/watches/remove")
    def watch_remove(req: WatchRequest):
        sub = store.subscription_by_endpoint(req.endpoint)
        if sub:
            store.remove_watch(sub.id, req.trainId)
        return {"ok": True}

    # --- frontend ---
    static_dir = settings.static_dir
    if static_dir and os.path.isdir(static_dir):
        @app.get("/sw.js", include_in_schema=False)
        def service_worker():
            # SW nie może być cache'owany przez przeglądarkę/proxy, inaczej aktualizacje utkną
            return FileResponse(os.path.join(static_dir, "sw.js"), media_type="text/javascript",
                                headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

        @app.middleware("http")
        async def no_cache_html(request: Request, call_next):
            response = await call_next(request)
            if request.url.path in ("/", "/index.html", "/manifest.webmanifest"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


def _configure_logging() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def factory() -> FastAPI:
    """Punkt wejścia dla uvicorn: `uvicorn app.main:factory --factory`."""
    _configure_logging()
    return create_app()

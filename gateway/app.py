"""
API Gateway – SwiftTrack Platform Entry Point
===============================================
Single entry point for the Client Portal and Driver App.

Responsibilities:
  - JWT Authentication & Authorization
  - REST API routing to Orchestrator and Adapters
  - WebSocket endpoint for real-time tracking (Redis Pub/Sub)
  - Rate limiting headers
  - CORS for the web frontend
  - Serves the static Client Portal / Driver App UI
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import jwt
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gateway")

# ── Configuration ──────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "swifttrack-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "60"))

ORCHESTRATOR_HOST = os.getenv("ORCHESTRATOR_HOST", "orchestrator")
ORCHESTRATOR_PORT = os.getenv("ORCHESTRATOR_PORT", "8001")
ORCHESTRATOR_URL = f"http://{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}"

CMS_ADAPTER_HOST = os.getenv("CMS_ADAPTER_HOST", "cms-adapter")
CMS_ADAPTER_PORT = os.getenv("CMS_ADAPTER_PORT", "8002")
CMS_ADAPTER_URL = f"http://{CMS_ADAPTER_HOST}:{CMS_ADAPTER_PORT}"

ROS_HOST = os.getenv("ROS_HOST", "mock-ros")
ROS_PORT = os.getenv("ROS_PORT", "8003")
ROS_URL = f"http://{ROS_HOST}:{ROS_PORT}"

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# ── Global resources ───────────────────────────────────

http_client: httpx.AsyncClient | None = None
redis_client: aioredis.Redis | None = None
security = HTTPBearer(auto_error=False)

# ── Mock user store (in production: database) ──────────

USERS = {
    "megamart": {"user_id": "CLI001", "password": "pass123", "role": "client", "name": "MegaMart Online"},
    "shopeasy": {"user_id": "CLI002", "password": "pass123", "role": "client", "name": "ShopEasy"},
    "freshgrocer": {"user_id": "CLI003", "password": "pass123", "role": "client", "name": "FreshGrocer"},
    "driver1": {"user_id": "DRV-001", "password": "pass123", "role": "driver", "name": "Kamal Perera"},
    "driver2": {"user_id": "DRV-002", "password": "pass123", "role": "driver", "name": "Nimal Silva"},
    "admin": {"user_id": "ADM-001", "password": "admin123", "role": "admin", "name": "System Admin"},
}


# ── JWT Helpers ────────────────────────────────────────

def create_token(user_id: str, role: str, name: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "name": name,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Authentication required")
    return decode_token(credentials.credentials)


# ── WebSocket Connection Manager ───────────────────────

class ConnectionManager:
    """Manages WebSocket connections for real-time tracking."""

    def __init__(self):
        # Map: user_id → list of WebSocket connections
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"[WS] User {user_id} connected. Total: {sum(len(v) for v in self.active_connections.values())}")

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id] = [
                ws for ws in self.active_connections[user_id] if ws != websocket
            ]
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"[WS] User {user_id} disconnected")

    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            dead = []
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[user_id].remove(ws)

    async def broadcast(self, message: dict):
        for user_id in list(self.active_connections.keys()):
            await self.send_to_user(user_id, message)


manager = ConnectionManager()


# ── Redis Pub/Sub Listener ─────────────────────────────

async def redis_subscriber():
    """Listen for real-time tracking events from Redis Pub/Sub and push to WebSocket clients."""
    sub_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = sub_client.pubsub()
    await pubsub.subscribe("swifttrack:tracking")
    logger.info("[Gateway] Redis Pub/Sub subscriber started")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    sent_to: set[str] = set()

                    # 1. Send to the specific client who owns the order
                    client_id = data.get("client_id")  # Prefer from event
                    if not client_id:
                        order_id = data.get("order_id")
                        if order_id and redis_client:
                            order_data = await redis_client.hgetall(f"order:{order_id}")
                            client_id = order_data.get("client_id") if order_data else None
                    if client_id:
                        await manager.send_to_user(client_id, data)
                        sent_to.add(client_id)

                    # 2. Send to all connected drivers (they need all package/route events)
                    for uid in list(manager.active_connections.keys()):
                        if uid.startswith("DRV-") and uid not in sent_to:
                            await manager.send_to_user(uid, data)
                            sent_to.add(uid)

                    # 3. Send to admins
                    for uid in list(manager.active_connections.keys()):
                        if uid.startswith("ADM-") and uid not in sent_to:
                            await manager.send_to_user(uid, data)
                            sent_to.add(uid)

                except json.JSONDecodeError:
                    pass
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("swifttrack:tracking")
        await sub_client.close()


# ── App Lifecycle ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, redis_client

    http_client = httpx.AsyncClient(timeout=30)
    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Start Redis Pub/Sub listener for WebSocket broadcasting
    subscriber_task = asyncio.create_task(redis_subscriber())

    yield

    subscriber_task.cancel()
    await http_client.aclose()
    await redis_client.close()


app = FastAPI(
    title="SwiftTrack API Gateway",
    description="Entry point for SwiftTrack Client Portal and Driver App",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth Models & Endpoints ───────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    role: str
    name: str


@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    user = USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(401, "Invalid credentials")
    token = create_token(user["user_id"], user["role"], user["name"])
    return LoginResponse(
        access_token=token,
        user_id=user["user_id"],
        role=user["role"],
        name=user["name"],
    )


@app.get("/api/v1/auth/me")
async def get_me(user=Depends(get_current_user)):
    return user


# ── Order Endpoints (proxied to Orchestrator) ─────────

class SubmitOrderRequest(BaseModel):
    client_id: str
    recipient_name: str
    delivery_street: str
    delivery_city: str
    delivery_postal_code: str
    items: list[dict]
    priority: str = "normal"


@app.post("/api/v1/orders")
async def submit_order(req: SubmitOrderRequest, user=Depends(get_current_user)):
    """Submit a new order (synchronous processing)."""
    # Ensure client can only submit for themselves (or admin can for anyone)
    if user["role"] == "client" and req.client_id != user["sub"]:
        raise HTTPException(403, "Cannot submit orders for other clients")

    try:
        resp = await http_client.post(
            f"{ORCHESTRATOR_URL}/api/v1/orders/submit",
            json=req.model_dump(),
        )
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator order submit error: {exc}")
        raise HTTPException(502, "Order submission failed. Please try again.")


@app.post("/api/v1/orders/async")
async def submit_order_async(req: SubmitOrderRequest, user=Depends(get_current_user)):
    """Submit a new order (asynchronous/queued processing for high volume)."""
    if user["role"] == "client" and req.client_id != user["sub"]:
        raise HTTPException(403, "Cannot submit orders for other clients")

    try:
        resp = await http_client.post(
            f"{ORCHESTRATOR_URL}/api/v1/orders/submit-async",
            json=req.model_dump(),
        )
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator async order submit error: {exc}")
        raise HTTPException(502, "Async order submission failed. Please try again.")


@app.get("/api/v1/orders/client/{client_id}")
async def get_client_orders(client_id: str, user=Depends(get_current_user)):
    """Get all orders for a client."""
    if user["role"] == "client" and client_id != user["sub"]:
        raise HTTPException(403, "Cannot view other client's orders")
    try:
        resp = await http_client.get(f"{ORCHESTRATOR_URL}/api/v1/orders/client/{client_id}")
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator error fetching client orders: {exc}")
        raise HTTPException(502, "Failed to fetch client orders")


@app.get("/api/v1/orders/saga/{saga_id}")
async def get_saga(saga_id: str, user=Depends(get_current_user)):
    """Get saga execution state."""
    try:
        resp = await http_client.get(f"{ORCHESTRATOR_URL}/api/v1/orders/saga/{saga_id}")
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator error fetching saga: {exc}")
        raise HTTPException(502, "Failed to fetch saga status")


@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str, user=Depends(get_current_user)):
    """Get order status."""
    try:
        resp = await http_client.get(f"{ORCHESTRATOR_URL}/api/v1/orders/{order_id}")
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator error fetching order: {exc}")
        raise HTTPException(502, "Failed to fetch order status")


# ── Delivery Endpoints (for drivers) ──────────────────

class DeliveryUpdateRequest(BaseModel):
    order_id: str
    result: str
    notes: str | None = None


@app.post("/api/v1/delivery/update")
async def update_delivery(req: DeliveryUpdateRequest, user=Depends(get_current_user)):
    """Driver marks a delivery as delivered/failed."""
    if user["role"] not in ("driver", "admin"):
        raise HTTPException(403, "Only drivers can update deliveries")
    try:
        resp = await http_client.post(
            f"{ORCHESTRATOR_URL}/api/v1/delivery/update",
            json={
                "order_id": req.order_id,
                "driver_id": user["sub"],
                "result": req.result,
                "notes": req.notes,
            },
        )
        return resp.json()
    except Exception as exc:
        logger.error(f"Delivery update error: {exc}")
        raise HTTPException(502, "Delivery update failed. Please try again.")


# ── Client Info (proxied to CMS Adapter) ──────────────

@app.get("/api/v1/orders/all")
async def get_all_orders(user=Depends(get_current_user)):
    """Get all orders across all clients (admin only)."""
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    try:
        resp = await http_client.get(f"{ORCHESTRATOR_URL}/api/v1/orders/all")
        return resp.json()
    except Exception as exc:
        logger.error(f"Orchestrator error fetching all orders: {exc}")
        raise HTTPException(502, "Failed to fetch all orders")


@app.get("/api/v1/clients")
async def list_clients(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    try:
        resp = await http_client.get(f"{CMS_ADAPTER_URL}/api/v1/clients")
        return resp.json()
    except Exception as exc:
        logger.error(f"CMS adapter error listing clients: {exc}")
        raise HTTPException(502, "Failed to fetch clients")


@app.get("/api/v1/clients/{client_id}")
async def get_client(client_id: str, user=Depends(get_current_user)):
    try:
        resp = await http_client.get(f"{CMS_ADAPTER_URL}/api/v1/clients/{client_id}")
        return resp.json()
    except Exception as exc:
        logger.error(f"CMS adapter error fetching client {client_id}: {exc}")
        raise HTTPException(502, "Failed to fetch client info")


# ── Route Info (proxied to ROS) ───────────────────────

@app.get("/api/v1/routes")
async def list_routes(user=Depends(get_current_user)):
    try:
        resp = await http_client.get(f"{ROS_URL}/api/v1/routes")
        return resp.json()
    except Exception as exc:
        logger.error(f"ROS error listing routes: {exc}")
        raise HTTPException(502, "Failed to fetch routes")


@app.get("/api/v1/routes/{route_id}")
async def get_route(route_id: str, user=Depends(get_current_user)):
    try:
        resp = await http_client.get(f"{ROS_URL}/api/v1/routes/{route_id}")
        return resp.json()
    except Exception as exc:
        logger.error(f"ROS error fetching route {route_id}: {exc}")
        raise HTTPException(502, "Failed to fetch route")


# ── Driver Manifest ───────────────────────────────────

@app.get("/api/v1/driver/manifest")
async def get_driver_manifest(user=Depends(get_current_user)):
    """Get the driver's delivery manifest for today — flat stops list."""
    if user["role"] not in ("driver", "admin"):
        raise HTTPException(403, "Drivers only")

    driver_id = user["sub"]
    stops = []

    try:
        # Strategy 1: Get stops from ROS routes assigned to this driver
        try:
            resp = await http_client.get(f"{ROS_URL}/api/v1/routes")
            all_routes = resp.json().get("routes", [])
            driver_routes = [r for r in all_routes if r.get("driver_id") == driver_id]
            seen_orders = set()
            for route in driver_routes:
                for stop in route.get("stops", []):
                    oid = stop.get("order_id", "")
                    if oid in seen_orders:
                        continue
                    seen_orders.add(oid)
                    order_data = await redis_client.hgetall(f"order:{oid}")
                    if order_data:
                        stop["recipient_name"] = order_data.get("recipient_name", "")
                        stop["delivery_address"] = order_data.get("delivery_address", "")
                        stop["status"] = order_data.get("status", "PROCESSING")
                        stop["client_id"] = order_data.get("client_id", "")
                    stops.append(stop)
        except Exception:
            logger.warning("Could not fetch ROS routes, falling back to Redis")

        # Strategy 2: Also fetch from Redis driver:xxx:orders set
        redis_oids = await redis_client.smembers(f"driver:{driver_id}:orders")
        existing = {s.get("order_id") for s in stops}
        for oid in redis_oids:
            if oid in existing:
                continue
            order_data = await redis_client.hgetall(f"order:{oid}")
            if order_data:
                stops.append({
                    "order_id": oid,
                    "recipient_name": order_data.get("recipient_name", ""),
                    "delivery_address": order_data.get("delivery_address", ""),
                    "status": order_data.get("status", "PROCESSING"),
                    "client_id": order_data.get("client_id", ""),
                    "priority": order_data.get("priority", "normal"),
                })

        return {"driver_id": driver_id, "stops": stops, "count": len(stops)}
    except Exception as exc:
        logger.error(f"Error fetching driver manifest: {exc}")
        raise HTTPException(502, "Failed to fetch delivery manifest")


# ── WebSocket for Real-Time Tracking ──────────────────

@app.websocket("/ws/tracking")
async def websocket_tracking(websocket: WebSocket, token: str = Query(None)):
    """
    WebSocket endpoint for real-time order tracking.
    Clients connect with their JWT token as a query parameter.
    """
    if not token:
        await websocket.close(code=4001, reason="Token required")
        return

    try:
        user = decode_token(token)
    except HTTPException:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = user["sub"]
    await manager.connect(websocket, user_id)

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "event_type": "connected",
            "message": f"Connected as {user.get('name', user_id)}",
            "user_id": user_id,
            "role": user["role"],
        })

        # Keep the connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            # Handle ping/pong to keep connection alive
            if data == "ping":
                await websocket.send_text("pong")
            else:
                # Driver can send delivery updates via WebSocket too
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "delivery_update" and user["role"] == "driver":
                        await http_client.post(
                            f"{ORCHESTRATOR_URL}/api/v1/delivery/update",
                            json={
                                "order_id": msg["order_id"],
                                "driver_id": user_id,
                                "result": msg["result"],
                                "notes": msg.get("notes"),
                            },
                        )
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception as exc:
        logger.error(f"[WS] Error for {user_id}: {exc}")
        manager.disconnect(websocket, user_id)


# ── Health Check ──────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway"}


# ── Serve Static UI ──────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the Client Portal UI."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return HTMLResponse("<h1>SwiftTrack Gateway</h1><p>UI not deployed. Use API endpoints.</p>")


@app.get("/driver", response_class=HTMLResponse)
async def driver_page():
    """Serve the Driver App UI."""
    driver_path = static_dir / "driver.html"
    if driver_path.exists():
        return driver_path.read_text()
    return HTMLResponse("<h1>SwiftTrack Driver App</h1><p>UI not deployed.</p>")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """Serve the Admin Dashboard UI."""
    admin_path = static_dir / "admin.html"
    if admin_path.exists():
        return admin_path.read_text()
    return HTMLResponse("<h1>SwiftTrack Admin</h1><p>UI not deployed.</p>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

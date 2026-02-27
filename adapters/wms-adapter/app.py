"""
WMS Adapter – TCP/IP Proprietary Protocol to REST Translator
=============================================================
Bridges the WMS proprietary TCP protocol to internal REST/JSON middleware.

- Maintains a persistent TCP connection to the Mock WMS
- Exposes REST endpoints for the Orchestrator / Gateway
- Listens for async WMS events and publishes them to RabbitMQ + Redis Pub/Sub
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import aio_pika
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wms-adapter")

# ── Configuration ──────────────────────────────────────

WMS_HOST = os.getenv("MOCK_WMS_HOST", "mock-wms")
WMS_PORT = int(os.getenv("MOCK_WMS_PORT", "9000"))

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "swifttrack")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "swifttrack123")
RABBITMQ_URL = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# ── Global state ───────────────────────────────────────

tcp_reader: asyncio.StreamReader | None = None
tcp_writer: asyncio.StreamWriter | None = None
tcp_lock = asyncio.Lock()  # serialize TCP request-response pairs
rabbit_channel: aio_pika.abc.AbstractChannel | None = None
redis_client: aioredis.Redis | None = None
event_listener_task: asyncio.Task | None = None

# Pending responses keyed by a correlation approach
# Since TCP is sequential (one request → one response), we use a simple future
pending_response: asyncio.Future | None = None


# ── TCP Communication ─────────────────────────────────

async def tcp_connect(retries: int = 15):
    """Connect to the WMS TCP server with retries."""
    global tcp_reader, tcp_writer
    for attempt in range(1, retries + 1):
        try:
            tcp_reader, tcp_writer = await asyncio.open_connection(WMS_HOST, WMS_PORT)
            logger.info(f"[WMS-Adapter] Connected to WMS at {WMS_HOST}:{WMS_PORT}")
            return
        except Exception as exc:
            logger.warning(f"[WMS-Adapter] TCP connect attempt {attempt}/{retries}: {exc}")
            await asyncio.sleep(2)
    raise RuntimeError(f"Cannot connect to WMS at {WMS_HOST}:{WMS_PORT}")


async def tcp_send_recv(message: str) -> str:
    """Send a message over TCP and wait for the response line."""
    global tcp_reader, tcp_writer
    async with tcp_lock:
        try:
            tcp_writer.write((message + "\n").encode())
            await tcp_writer.drain()
            data = await asyncio.wait_for(tcp_reader.readline(), timeout=10)
            return data.decode().strip()
        except Exception as exc:
            logger.error(f"[WMS-Adapter] TCP error: {exc}, reconnecting...")
            await tcp_connect(retries=5)
            # Retry once
            tcp_writer.write((message + "\n").encode())
            await tcp_writer.drain()
            data = await asyncio.wait_for(tcp_reader.readline(), timeout=10)
            return data.decode().strip()


async def listen_for_events():
    """
    Background task: continuously read EVENT lines from the WMS TCP connection.
    Events are published to RabbitMQ and Redis for downstream consumers.
    
    NOTE: In a real scenario, we'd use a dedicated TCP connection for events.
    Here we handle it by checking if a line starts with EVENT.
    """
    # We use a second TCP connection dedicated to receiving events
    for attempt in range(15):
        try:
            event_reader, event_writer = await asyncio.open_connection(WMS_HOST, WMS_PORT)
            logger.info("[WMS-Adapter] Event listener connected to WMS")
            break
        except Exception:
            await asyncio.sleep(2)
    else:
        logger.error("[WMS-Adapter] Event listener failed to connect")
        return

    try:
        while True:
            data = await event_reader.readline()
            if not data:
                break
            line = data.decode().strip()
            if line.startswith("EVENT"):
                await handle_wms_event(line)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error(f"[WMS-Adapter] Event listener error: {exc}")
    finally:
        event_writer.close()


async def handle_wms_event(line: str):
    """Parse a WMS event and publish to RabbitMQ + Redis."""
    # EVENT|STATUS_CHANGE|<pkg_id>|<old>|<new>|<timestamp>
    parts = line.split("|")
    if len(parts) >= 6 and parts[1] == "STATUS_CHANGE":
        event_data = {
            "event_type": "wms.package.status_change",
            "package_id": parts[2],
            "old_status": parts[3],
            "new_status": parts[4],
            "timestamp": parts[5],
        }
        logger.info(f"[WMS-Adapter] Event: {event_data}")

        # Publish to RabbitMQ
        if rabbit_channel:
            try:
                exchange = await rabbit_channel.get_exchange("swifttrack.events")
                await exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(event_data).encode(),
                        content_type="application/json",
                    ),
                    routing_key="wms.status",
                )
            except Exception as exc:
                logger.error(f"[WMS-Adapter] RabbitMQ publish error: {exc}")

        # Publish to Redis Pub/Sub for real-time WebSocket updates
        if redis_client:
            try:
                await redis_client.publish(
                    "swifttrack:tracking",
                    json.dumps(event_data),
                )
            except Exception as exc:
                logger.error(f"[WMS-Adapter] Redis publish error: {exc}")


# ── RabbitMQ + Redis Setup ─────────────────────────────

async def setup_rabbitmq():
    global rabbit_channel
    for attempt in range(10):
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            rabbit_channel = await connection.channel()
            # Declare the exchange
            await rabbit_channel.declare_exchange(
                "swifttrack.events", aio_pika.ExchangeType.TOPIC, durable=True
            )
            logger.info("[WMS-Adapter] RabbitMQ connected")
            return
        except Exception as exc:
            logger.warning(f"[WMS-Adapter] RabbitMQ attempt {attempt+1}: {exc}")
            await asyncio.sleep(3)
    logger.error("[WMS-Adapter] Could not connect to RabbitMQ")


async def setup_redis():
    global redis_client
    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await redis_client.ping()
    logger.info("[WMS-Adapter] Redis connected")


# ── FastAPI App ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global event_listener_task
    await tcp_connect()
    await setup_rabbitmq()
    await setup_redis()
    event_listener_task = asyncio.create_task(listen_for_events())
    yield
    if event_listener_task:
        event_listener_task.cancel()
    if tcp_writer:
        tcp_writer.close()


app = FastAPI(
    title="WMS Adapter – TCP to REST",
    version="1.0.0",
    lifespan=lifespan,
)


# ── REST Models ────────────────────────────────────────

class ReceivePackageRequest(BaseModel):
    order_id: str
    item_count: int
    weight_kg: float


class PackageResponse(BaseModel):
    success: bool
    package_id: str
    message: str


# ── Endpoints ──────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "wms-adapter", "wms_target": f"{WMS_HOST}:{WMS_PORT}"}


@app.post("/api/v1/packages/receive", response_model=PackageResponse)
async def receive_package(req: ReceivePackageRequest):
    """REST → TCP: PKG_RECEIVE"""
    msg = f"PKG_RECEIVE|{req.order_id}|{req.item_count}|{req.weight_kg}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    # PKG_RECEIVE_ACK|OK|<pkg_id>|<order_id>
    if len(parts) >= 3 and parts[1] == "OK":
        return PackageResponse(success=True, package_id=parts[2], message="Package received in warehouse")
    raise HTTPException(500, f"WMS error: {resp}")


@app.get("/api/v1/packages/{package_id}/status")
async def get_package_status(package_id: str):
    """REST → TCP: PKG_STATUS"""
    msg = f"PKG_STATUS|{package_id}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    if len(parts) >= 4 and parts[1] == "OK":
        return {
            "package_id": parts[2],
            "status": parts[3],
            "order_id": parts[4] if len(parts) > 4 else None,
            "updated_at": parts[5] if len(parts) > 5 else None,
        }
    if parts[1] == "NOT_FOUND":
        raise HTTPException(404, f"Package {package_id} not found")
    raise HTTPException(500, f"WMS error: {resp}")


@app.post("/api/v1/packages/{package_id}/process")
async def process_package(package_id: str):
    """REST → TCP: PKG_PROCESS"""
    msg = f"PKG_PROCESS|{package_id}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    if len(parts) >= 3 and parts[1] == "OK":
        return {"package_id": parts[2], "status": parts[3]}
    raise HTTPException(500, f"WMS error: {resp}")


@app.post("/api/v1/packages/{package_id}/load")
async def load_package(package_id: str, vehicle_id: str = "VEH-001"):
    """REST → TCP: PKG_LOAD"""
    msg = f"PKG_LOAD|{package_id}|{vehicle_id}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    if len(parts) >= 3 and parts[1] == "OK":
        return {"package_id": parts[2], "status": parts[3], "vehicle_id": parts[4] if len(parts) > 4 else vehicle_id}
    raise HTTPException(500, f"WMS error: {resp}")


@app.post("/api/v1/packages/{package_id}/dispatch")
async def dispatch_package(package_id: str):
    """REST → TCP: PKG_DISPATCH"""
    msg = f"PKG_DISPATCH|{package_id}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    if len(parts) >= 3 and parts[1] == "OK":
        return {"package_id": parts[2], "status": parts[3]}
    raise HTTPException(500, f"WMS error: {resp}")


@app.delete("/api/v1/packages/{package_id}")
async def cancel_package(package_id: str):
    """REST → TCP: PKG_CANCEL (saga compensation)"""
    msg = f"PKG_CANCEL|{package_id}"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|")
    if len(parts) >= 3 and parts[1] == "OK":
        return {"package_id": parts[2], "status": parts[3], "message": "Package cancelled"}
    raise HTTPException(500, f"WMS error: {resp}")


@app.get("/api/v1/packages")
async def list_packages():
    """REST → TCP: PKG_LIST"""
    msg = "PKG_LIST"
    resp = await tcp_send_recv(msg)
    parts = resp.split("|", 2)
    if len(parts) >= 3 and parts[1] == "OK":
        try:
            packages = json.loads(parts[2])
        except json.JSONDecodeError:
            packages = []
        return {"packages": packages, "count": len(packages)}
    raise HTTPException(500, f"WMS error: {resp}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)

"""
Order Orchestrator Service – Saga Pattern Implementation
=========================================================
Coordinates the distributed "place order" transaction across:
  CMS (create order) → WMS (receive package) → ROS (optimise route)

Uses the Orchestrator Saga pattern:
  - Each step calls the next adapter via REST
  - On failure, compensating transactions roll back previous steps
  - Saga state is persisted in Redis for crash recovery
  - Events are published to RabbitMQ for async downstream consumers

Message flow:
  1. Client submits order via Gateway → Orchestrator
  2. Orchestrator calls CMS Adapter (create order in legacy CMS)
  3. Orchestrator calls WMS Adapter (register package in warehouse)
  4. Orchestrator calls ROS (optimise delivery route)
  5. On success: publishes ORDER_COMPLETED event
  6. On failure at any step: runs compensating transactions for completed steps
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import aio_pika
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

# ── Configuration ──────────────────────────────────────

CMS_ADAPTER_HOST = os.getenv("CMS_ADAPTER_HOST", "cms-adapter")
CMS_ADAPTER_PORT = os.getenv("CMS_ADAPTER_PORT", "8002")
WMS_ADAPTER_HOST = os.getenv("WMS_ADAPTER_HOST", "wms-adapter")
WMS_ADAPTER_PORT = os.getenv("WMS_ADAPTER_PORT", "8004")
ROS_HOST = os.getenv("ROS_HOST", "mock-ros")
ROS_PORT = os.getenv("ROS_PORT", "8003")

CMS_URL = f"http://{CMS_ADAPTER_HOST}:{CMS_ADAPTER_PORT}"
WMS_URL = f"http://{WMS_ADAPTER_HOST}:{WMS_ADAPTER_PORT}"
ROS_URL = f"http://{ROS_HOST}:{ROS_PORT}"

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "swifttrack")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "swifttrack123")
RABBITMQ_URL = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# ── Global resources ───────────────────────────────────

http_client: httpx.AsyncClient | None = None
rabbit_connection: aio_pika.abc.AbstractConnection | None = None
rabbit_channel: aio_pika.abc.AbstractChannel | None = None
redis_client: aioredis.Redis | None = None


# ── Saga State Machine ────────────────────────────────

class SagaState:
    STARTED = "STARTED"
    CMS_CREATED = "CMS_CREATED"
    WMS_RECEIVED = "WMS_RECEIVED"
    ROS_OPTIMISED = "ROS_OPTIMISED"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    FAILED = "FAILED"


async def save_saga(saga_id: str, saga: dict):
    """Persist saga state to Redis."""
    saga["updated_at"] = datetime.utcnow().isoformat()
    await redis_client.set(f"saga:{saga_id}", json.dumps(saga), ex=86400)  # 24h TTL


async def load_saga(saga_id: str) -> dict | None:
    data = await redis_client.get(f"saga:{saga_id}")
    return json.loads(data) if data else None


async def publish_event(routing_key: str, data: dict):
    """Publish event to RabbitMQ topic exchange."""
    if rabbit_channel:
        try:
            exchange = await rabbit_channel.get_exchange("swifttrack.events")
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(data).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=routing_key,
            )
        except Exception as exc:
            logger.error(f"RabbitMQ publish error: {exc}")


async def publish_tracking(data: dict):
    """Publish real-time tracking update to Redis Pub/Sub for WebSocket consumers."""
    if redis_client:
        await redis_client.publish("swifttrack:tracking", json.dumps(data))


# ── Order Submission Models ────────────────────────────

class OrderItem(BaseModel):
    description: str
    quantity: int = 1
    weight_kg: float = 0.5


class SubmitOrderRequest(BaseModel):
    client_id: str
    recipient_name: str
    delivery_street: str
    delivery_city: str
    delivery_postal_code: str
    items: list[OrderItem]
    priority: str = "normal"


class SubmitOrderResponse(BaseModel):
    saga_id: str
    order_id: str | None
    status: str
    message: str


class DeliveryUpdateRequest(BaseModel):
    order_id: str
    driver_id: str
    result: str  # DELIVERED, FAILED_NOT_HOME, etc.
    notes: str | None = None


# ── Saga Execution ─────────────────────────────────────

async def execute_order_saga(saga_id: str, req: SubmitOrderRequest) -> dict:
    """
    Execute the full order placement saga:
      Step 1: CMS → Create Order
      Step 2: WMS → Receive Package
      Step 3: ROS → Optimise Route
    On failure at any step, compensate all completed steps.
    """
    total_weight = sum(item.weight_kg * item.quantity for item in req.items)
    total_items = sum(item.quantity for item in req.items)

    saga = {
        "saga_id": saga_id,
        "state": SagaState.STARTED,
        "client_id": req.client_id,
        "cms_order_id": None,
        "wms_package_id": None,
        "ros_route_id": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
    }
    await save_saga(saga_id, saga)

    # ── Step 1: Create Order in CMS ────────────────────
    try:
        logger.info(f"[Saga {saga_id}] Step 1: Creating order in CMS...")
        resp = await http_client.post(
            f"{CMS_URL}/api/v1/orders",
            json={
                "client_id": req.client_id,
                "recipient_name": req.recipient_name,
                "delivery_street": req.delivery_street,
                "delivery_city": req.delivery_city,
                "delivery_postal_code": req.delivery_postal_code,
                "item_count": total_items,
                "total_weight_kg": total_weight,
                "priority": req.priority,
            },
            timeout=10,
        )
        resp.raise_for_status()
        cms_result = resp.json()
        saga["cms_order_id"] = cms_result["order_id"]
        saga["state"] = SagaState.CMS_CREATED
        await save_saga(saga_id, saga)
        logger.info(f"[Saga {saga_id}] CMS order created: {cms_result['order_id']}")

        await publish_tracking({
            "event_type": "order.status_change",
            "order_id": cms_result["order_id"],
            "status": "ACCEPTED",
            "message": "Order accepted by CMS",
            "timestamp": datetime.utcnow().isoformat(),
        })

    except Exception as exc:
        saga["state"] = SagaState.FAILED
        saga["error"] = f"CMS step failed: {exc}"
        await save_saga(saga_id, saga)
        logger.error(f"[Saga {saga_id}] CMS failed: {exc}")
        return saga

    # ── Step 2: Register Package in WMS ────────────────
    try:
        logger.info(f"[Saga {saga_id}] Step 2: Receiving package in WMS...")
        resp = await http_client.post(
            f"{WMS_URL}/api/v1/packages/receive",
            json={
                "order_id": saga["cms_order_id"],
                "item_count": total_items,
                "weight_kg": total_weight,
            },
            timeout=10,
        )
        resp.raise_for_status()
        wms_result = resp.json()
        saga["wms_package_id"] = wms_result["package_id"]
        saga["state"] = SagaState.WMS_RECEIVED
        await save_saga(saga_id, saga)
        logger.info(f"[Saga {saga_id}] WMS package received: {wms_result['package_id']}")

        await publish_tracking({
            "event_type": "order.status_change",
            "order_id": saga["cms_order_id"],
            "status": "IN_WAREHOUSE",
            "message": f"Package {wms_result['package_id']} received in warehouse",
            "timestamp": datetime.utcnow().isoformat(),
        })

    except Exception as exc:
        logger.error(f"[Saga {saga_id}] WMS failed: {exc}. Compensating CMS...")
        saga["state"] = SagaState.COMPENSATING
        await save_saga(saga_id, saga)
        # Compensate: Cancel CMS order
        await compensate_cms(saga)
        saga["state"] = SagaState.FAILED
        saga["error"] = f"WMS step failed: {exc}"
        await save_saga(saga_id, saga)
        return saga

    # ── Step 3: Optimise Route via ROS ─────────────────
    try:
        logger.info(f"[Saga {saga_id}] Step 3: Optimising route via ROS...")
        resp = await http_client.post(
            f"{ROS_URL}/api/v1/optimize",
            json={
                "delivery_points": [
                    {
                        "order_id": saga["cms_order_id"],
                        "street": req.delivery_street,
                        "city": req.delivery_city,
                        "postal_code": req.delivery_postal_code,
                        "priority": req.priority,
                    }
                ],
                "vehicles": [
                    {
                        "vehicle_id": "VEH-001",
                        "driver_id": "DRV-001",
                        "capacity_kg": 500.0,
                    }
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        ros_result = resp.json()
        route_id = ros_result["routes"][0]["route_id"] if ros_result.get("routes") else None
        saga["ros_route_id"] = route_id
        saga["state"] = SagaState.COMPLETED
        await save_saga(saga_id, saga)
        logger.info(f"[Saga {saga_id}] Route optimised: {route_id}")

        await publish_tracking({
            "event_type": "order.status_change",
            "order_id": saga["cms_order_id"],
            "status": "PROCESSING",
            "message": f"Route optimised. Route ID: {route_id}",
            "timestamp": datetime.utcnow().isoformat(),
        })

    except Exception as exc:
        logger.error(f"[Saga {saga_id}] ROS failed: {exc}. Compensating WMS + CMS...")
        saga["state"] = SagaState.COMPENSATING
        await save_saga(saga_id, saga)
        # Compensate: Cancel WMS package, then CMS order
        await compensate_wms(saga)
        await compensate_cms(saga)
        saga["state"] = SagaState.FAILED
        saga["error"] = f"ROS step failed: {exc}"
        await save_saga(saga_id, saga)
        return saga

    # ── Saga completed successfully ────────────────────
    await publish_event("order.completed", {
        "saga_id": saga_id,
        "order_id": saga["cms_order_id"],
        "package_id": saga["wms_package_id"],
        "route_id": saga["ros_route_id"],
        "client_id": req.client_id,
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Store order details in Redis for quick lookups
    driver_id = "DRV-001"  # Assigned by ROS optimizer
    await redis_client.hset(
        f"order:{saga['cms_order_id']}",
        mapping={
            "order_id": saga["cms_order_id"],
            "client_id": req.client_id,
            "recipient_name": req.recipient_name,
            "delivery_address": f"{req.delivery_street}, {req.delivery_city}",
            "package_id": saga["wms_package_id"],
            "route_id": saga["ros_route_id"] or "",
            "driver_id": driver_id,
            "status": "PROCESSING",
            "priority": req.priority,
            "created_at": saga["created_at"],
        },
    )

    # Add to client's order list and driver's order list
    await redis_client.sadd(f"client:{req.client_id}:orders", saga["cms_order_id"])
    await redis_client.sadd(f"driver:{driver_id}:orders", saga["cms_order_id"])

    return saga


# ── Compensating Transactions ──────────────────────────

async def compensate_cms(saga: dict):
    """Cancel the order in CMS."""
    if saga.get("cms_order_id"):
        try:
            resp = await http_client.delete(
                f"{CMS_URL}/api/v1/orders/{saga['cms_order_id']}", timeout=10
            )
            logger.info(f"[Saga {saga['saga_id']}] CMS compensation: {resp.status_code}")
        except Exception as exc:
            logger.error(f"[Saga {saga['saga_id']}] CMS compensation failed: {exc}")


async def compensate_wms(saga: dict):
    """Cancel the package in WMS."""
    if saga.get("wms_package_id"):
        try:
            resp = await http_client.delete(
                f"{WMS_URL}/api/v1/packages/{saga['wms_package_id']}", timeout=10
            )
            logger.info(f"[Saga {saga['saga_id']}] WMS compensation: {resp.status_code}")
        except Exception as exc:
            logger.error(f"[Saga {saga['saga_id']}] WMS compensation failed: {exc}")


# ── RabbitMQ Consumer for Async Order Processing ──────

async def consume_orders():
    """
    Listen for orders submitted via RabbitMQ (for high-volume async processing).
    This allows the Gateway to fire-and-forget during peak load.
    """
    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            "swifttrack.events", aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue("order.submissions", durable=True)
        await queue.bind(exchange, routing_key="order.submit")

        logger.info("[Orchestrator] Listening for async order submissions on RabbitMQ...")

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        data = json.loads(message.body.decode())
                        saga_id = data.get("saga_id", str(uuid.uuid4()))
                        req = SubmitOrderRequest(**data["order"])
                        logger.info(f"[Orchestrator] Processing async order {saga_id}")
                        await execute_order_saga(saga_id, req)
                    except Exception as exc:
                        logger.error(f"[Orchestrator] Async order processing error: {exc}")
    except Exception as exc:
        logger.error(f"[Orchestrator] RabbitMQ consumer error: {exc}")


# ── App Lifecycle ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, rabbit_connection, rabbit_channel, redis_client

    http_client = httpx.AsyncClient()
    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Connect to RabbitMQ
    for attempt in range(15):
        try:
            rabbit_connection = await aio_pika.connect_robust(RABBITMQ_URL)
            rabbit_channel = await rabbit_connection.channel()
            await rabbit_channel.declare_exchange(
                "swifttrack.events", aio_pika.ExchangeType.TOPIC, durable=True
            )
            logger.info("[Orchestrator] RabbitMQ connected")
            break
        except Exception as exc:
            logger.warning(f"[Orchestrator] RabbitMQ attempt {attempt+1}: {exc}")
            await asyncio.sleep(3)

    # Start async order consumer in background
    consumer_task = asyncio.create_task(consume_orders())

    yield

    consumer_task.cancel()
    await http_client.aclose()
    if rabbit_connection:
        await rabbit_connection.close()


app = FastAPI(
    title="SwiftTrack Order Orchestrator",
    description="Saga-based order orchestration across CMS, WMS, and ROS",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Endpoints ──────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}


@app.post("/api/v1/orders/submit", response_model=SubmitOrderResponse)
async def submit_order(req: SubmitOrderRequest):
    """
    Synchronous order submission – executes the full saga and returns the result.
    For high-volume scenarios, use the async RabbitMQ submission endpoint.
    """
    saga_id = str(uuid.uuid4())
    saga = await execute_order_saga(saga_id, req)

    if saga["state"] == SagaState.COMPLETED:
        return SubmitOrderResponse(
            saga_id=saga_id,
            order_id=saga["cms_order_id"],
            status="COMPLETED",
            message=f"Order {saga['cms_order_id']} placed successfully. "
                    f"Package: {saga['wms_package_id']}, Route: {saga['ros_route_id']}",
        )
    else:
        return SubmitOrderResponse(
            saga_id=saga_id,
            order_id=saga.get("cms_order_id"),
            status="FAILED",
            message=saga.get("error", "Order processing failed"),
        )


@app.post("/api/v1/orders/submit-async")
async def submit_order_async(req: SubmitOrderRequest):
    """
    Asynchronous order submission via RabbitMQ.
    Returns immediately with a saga_id for tracking.
    Ideal for high-volume scenarios (Black Friday, Avurudu sales).
    """
    saga_id = str(uuid.uuid4())

    if rabbit_channel:
        exchange = await rabbit_channel.get_exchange("swifttrack.events")
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps({
                    "saga_id": saga_id,
                    "order": req.model_dump(),
                }).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key="order.submit",
        )

    return {
        "saga_id": saga_id,
        "status": "QUEUED",
        "message": "Order queued for processing. Track status via saga_id.",
    }


@app.get("/api/v1/orders/{order_id}")
async def get_order_status(order_id: str):
    """Get current order status from Redis."""
    order_data = await redis_client.hgetall(f"order:{order_id}")
    if not order_data:
        raise HTTPException(404, f"Order {order_id} not found")
    return order_data


@app.get("/api/v1/orders/saga/{saga_id}")
async def get_saga_status(saga_id: str):
    """Get saga execution status."""
    saga = await load_saga(saga_id)
    if not saga:
        raise HTTPException(404, f"Saga {saga_id} not found")
    return saga


@app.get("/api/v1/orders/client/{client_id}")
async def get_client_orders(client_id: str):
    """Get all orders for a client."""
    order_ids = await redis_client.smembers(f"client:{client_id}:orders")
    orders = []
    for oid in order_ids:
        order_data = await redis_client.hgetall(f"order:{oid}")
        if order_data:
            orders.append(order_data)
    return {"client_id": client_id, "orders": orders, "count": len(orders)}


@app.post("/api/v1/delivery/update")
async def update_delivery(req: DeliveryUpdateRequest):
    """
    Driver marks a delivery as completed or failed.
    Updates order status and publishes real-time tracking event.
    """
    order_data = await redis_client.hgetall(f"order:{req.order_id}")
    if not order_data:
        raise HTTPException(404, f"Order {req.order_id} not found")

    new_status = "DELIVERED" if req.result.upper() == "DELIVERED" else "FAILED"
    await redis_client.hset(f"order:{req.order_id}", "status", new_status)

    # Publish real-time update
    await publish_tracking({
        "event_type": "order.status_change",
        "order_id": req.order_id,
        "driver_id": req.driver_id,
        "status": new_status,
        "result": req.result,
        "notes": req.notes,
        "message": f"Delivery {req.result.lower()} by driver {req.driver_id}",
        "timestamp": datetime.utcnow().isoformat(),
    })

    await publish_event("order.delivery_update", {
        "order_id": req.order_id,
        "driver_id": req.driver_id,
        "result": req.result,
        "status": new_status,
        "timestamp": datetime.utcnow().isoformat(),
    })

    return {"order_id": req.order_id, "status": new_status, "message": f"Delivery marked as {req.result}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

"""
CMS Adapter – SOAP-to-REST Protocol Translator
================================================
Bridges the legacy SOAP/XML CMS to the internal REST/JSON middleware.

- Receives REST/JSON requests from the Orchestrator / Gateway
- Translates them to SOAP/XML calls using the Zeep client
- Returns JSON responses
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from zeep import Client as ZeepClient
from zeep.transports import Transport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cms-adapter")

# ── Configuration ──────────────────────────────────────

CMS_HOST = os.getenv("MOCK_CMS_HOST", "mock-cms")
CMS_PORT = os.getenv("MOCK_CMS_PORT", "8005")
CMS_WSDL = f"http://{CMS_HOST}:{CMS_PORT}/?wsdl"

# ── Zeep SOAP client (initialised on startup) ─────────

soap_client: ZeepClient | None = None


def init_soap_client(retries: int = 10):
    """Initialise the Zeep SOAP client with retries."""
    import time
    global soap_client
    for attempt in range(1, retries + 1):
        try:
            transport = Transport(timeout=10)
            soap_client = ZeepClient(CMS_WSDL, transport=transport)
            logger.info(f"[CMS-Adapter] Connected to CMS WSDL at {CMS_WSDL}")
            return
        except Exception as exc:
            logger.warning(f"[CMS-Adapter] WSDL connect attempt {attempt}/{retries} failed: {exc}")
            time.sleep(3)
    raise RuntimeError(f"Cannot connect to CMS at {CMS_WSDL}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_soap_client()
    yield

app = FastAPI(
    title="CMS Adapter – SOAP to REST",
    version="1.0.0",
    lifespan=lifespan,
)


# ── REST Models ────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    client_id: str
    recipient_name: str
    delivery_street: str
    delivery_city: str
    delivery_postal_code: str
    item_count: int
    total_weight_kg: float
    priority: str = "normal"


class OrderResult(BaseModel):
    success: bool
    order_id: str
    message: str


class ClientInfo(BaseModel):
    client_id: str
    company_name: str
    contact_email: str
    contract_type: str
    billing_rate: float


# ── Endpoints ──────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "cms-adapter", "cms_wsdl": CMS_WSDL}


@app.get("/api/v1/clients/{client_id}", response_model=ClientInfo)
def get_client(client_id: str):
    """Translate REST GET → SOAP GetClient."""
    try:
        result = soap_client.service.GetClient(client_id)
        if result.company_name == "NOT_FOUND":
            raise HTTPException(404, f"Client {client_id} not found")
        return ClientInfo(
            client_id=result.client_id,
            company_name=result.company_name,
            contact_email=result.contact_email,
            contract_type=result.contract_type,
            billing_rate=result.billing_rate,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"SOAP GetClient error: {exc}")
        raise HTTPException(502, "CMS communication error")


@app.get("/api/v1/clients")
def list_clients():
    """Translate REST GET → SOAP ListClients."""
    try:
        results = soap_client.service.ListClients()
        clients = []
        if results:
            for r in results:
                clients.append({
                    "client_id": r.client_id,
                    "company_name": r.company_name,
                    "contact_email": r.contact_email,
                    "contract_type": r.contract_type,
                    "billing_rate": r.billing_rate,
                })
        return {"clients": clients, "count": len(clients)}
    except Exception as exc:
        logger.error(f"SOAP ListClients error: {exc}")
        raise HTTPException(502, "CMS communication error")


@app.post("/api/v1/orders", response_model=OrderResult)
def create_order(req: CreateOrderRequest):
    """Translate REST POST → SOAP CreateOrder."""
    try:
        result = soap_client.service.CreateOrder(
            req.client_id,
            req.recipient_name,
            req.delivery_street,
            req.delivery_city,
            req.delivery_postal_code,
            req.item_count,
            req.total_weight_kg,
            req.priority,
        )
        success = result.success == "true"
        if not success:
            raise HTTPException(400, result.message)
        return OrderResult(
            success=success,
            order_id=result.order_id,
            message=result.message,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"SOAP CreateOrder error: {exc}")
        raise HTTPException(502, "CMS communication error")


@app.get("/api/v1/orders/{order_id}")
def get_order(order_id: str):
    """Translate REST GET → SOAP GetOrder."""
    try:
        result = soap_client.service.GetOrder(order_id)
        if result.order_id == "NOT_FOUND":
            raise HTTPException(404, f"Order {order_id} not found")
        return {
            "order_id": result.order_id,
            "client_id": result.client_id,
            "recipient_name": result.recipient_name,
            "status": result.status,
            "created_at": str(result.created_at) if result.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"SOAP GetOrder error: {exc}")
        raise HTTPException(502, "CMS communication error")


@app.delete("/api/v1/orders/{order_id}")
def cancel_order(order_id: str):
    """Translate REST DELETE → SOAP CancelOrder (saga compensation)."""
    try:
        result = soap_client.service.CancelOrder(order_id)
        success = result.success == "true"
        return {"success": success, "order_id": result.order_id, "message": result.message}
    except Exception as exc:
        logger.error(f"SOAP CancelOrder error: {exc}")
        raise HTTPException(502, "CMS communication error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

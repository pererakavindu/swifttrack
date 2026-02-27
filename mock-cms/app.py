"""
Mock Client Management System (CMS)
====================================
A legacy, on-premise system that exposes a SOAP/XML API for:
  - Order intake
  - Client lookup
  - Billing

Uses Spyne to serve a real WSDL/SOAP endpoint.
"""

import uuid
from datetime import datetime

from spyne import (
    Application,
    Unicode,
    Integer,
    Float,
    DateTime,
    Array,
    ComplexModel,
    Iterable,
    ServiceBase,
    rpc,
)
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication


# ── SOAP Data Types ────────────────────────────────────

class ClientInfo(ComplexModel):
    client_id = Unicode
    company_name = Unicode
    contact_email = Unicode
    contract_type = Unicode  # standard | premium
    billing_rate = Float


class OrderRecord(ComplexModel):
    order_id = Unicode
    client_id = Unicode
    recipient_name = Unicode
    delivery_street = Unicode
    delivery_city = Unicode
    delivery_postal_code = Unicode
    item_count = Integer
    total_weight_kg = Float
    priority = Unicode
    status = Unicode
    created_at = DateTime


class OrderResult(ComplexModel):
    success = Unicode  # "true" / "false"
    order_id = Unicode
    message = Unicode


class CancelResult(ComplexModel):
    success = Unicode
    order_id = Unicode
    message = Unicode


# ── In-memory data store ───────────────────────────────

CLIENTS: dict[str, dict] = {
    "CLI001": {
        "client_id": "CLI001",
        "company_name": "MegaMart Online",
        "contact_email": "ops@megamart.lk",
        "contract_type": "premium",
        "billing_rate": 150.0,
    },
    "CLI002": {
        "client_id": "CLI002",
        "company_name": "ShopEasy",
        "contact_email": "logistics@shopeasy.lk",
        "contract_type": "standard",
        "billing_rate": 200.0,
    },
    "CLI003": {
        "client_id": "CLI003",
        "company_name": "FreshGrocer",
        "contact_email": "delivery@freshgrocer.lk",
        "contract_type": "premium",
        "billing_rate": 180.0,
    },
}

ORDERS: dict[str, dict] = {}


# ── SOAP Service ───────────────────────────────────────

class ClientManagementService(ServiceBase):
    """SOAP service simulating the legacy CMS."""

    @rpc(Unicode, _returns=ClientInfo)
    def GetClient(ctx, client_id):
        """Look up a client by ID."""
        client = CLIENTS.get(client_id)
        if not client:
            return ClientInfo(
                client_id="",
                company_name="NOT_FOUND",
                contact_email="",
                contract_type="",
                billing_rate=0.0,
            )
        return ClientInfo(**client)

    @rpc(_returns=Array(ClientInfo))
    def ListClients(ctx):
        """Return all registered clients."""
        return [ClientInfo(**c) for c in CLIENTS.values()]

    @rpc(
        Unicode,  # client_id
        Unicode,  # recipient_name
        Unicode,  # delivery_street
        Unicode,  # delivery_city
        Unicode,  # delivery_postal_code
        Integer,  # item_count
        Float,    # total_weight_kg
        Unicode,  # priority
        _returns=OrderResult,
    )
    def CreateOrder(
        ctx,
        client_id,
        recipient_name,
        delivery_street,
        delivery_city,
        delivery_postal_code,
        item_count,
        total_weight_kg,
        priority,
    ):
        """Accept a new delivery order from a client."""
        if client_id not in CLIENTS:
            return OrderResult(
                success="false",
                order_id="",
                message=f"Client {client_id} not found",
            )

        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        order = {
            "order_id": order_id,
            "client_id": client_id,
            "recipient_name": recipient_name,
            "delivery_street": delivery_street,
            "delivery_city": delivery_city,
            "delivery_postal_code": delivery_postal_code,
            "item_count": item_count,
            "total_weight_kg": total_weight_kg,
            "priority": priority,
            "status": "ACCEPTED",
            "created_at": datetime.utcnow(),
        }
        ORDERS[order_id] = order
        print(f"[CMS] Created order {order_id} for client {client_id}")
        return OrderResult(
            success="true",
            order_id=order_id,
            message="Order created successfully",
        )

    @rpc(Unicode, _returns=OrderRecord)
    def GetOrder(ctx, order_id):
        """Retrieve a single order by ID."""
        order = ORDERS.get(order_id)
        if not order:
            return OrderRecord(order_id="NOT_FOUND")
        return OrderRecord(**order)

    @rpc(Unicode, _returns=Iterable(OrderRecord))
    def GetOrdersByClient(ctx, client_id):
        """List all orders for a given client."""
        for o in ORDERS.values():
            if o["client_id"] == client_id:
                yield OrderRecord(**o)

    @rpc(Unicode, _returns=CancelResult)
    def CancelOrder(ctx, order_id):
        """Cancel (compensate) a previously created order."""
        if order_id not in ORDERS:
            return CancelResult(
                success="false",
                order_id=order_id,
                message="Order not found",
            )
        ORDERS[order_id]["status"] = "CANCELLED"
        print(f"[CMS] Cancelled order {order_id}")
        return CancelResult(
            success="true",
            order_id=order_id,
            message="Order cancelled (compensation)",
        )


# ── Spyne Application Setup ───────────────────────────

soap_app = Application(
    [ClientManagementService],
    tns="http://swiftlogistics.lk/cms",
    in_protocol=Soap11(validator="lxml"),
    out_protocol=Soap11(),
)

wsgi_app = WsgiApplication(soap_app)


if __name__ == "__main__":
    import logging
    from wsgiref.simple_server import make_server

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("spyne.protocol.xml").setLevel(logging.INFO)

    host, port = "0.0.0.0", 8005
    server = make_server(host, port, wsgi_app)
    print(f"[CMS] SOAP service running on http://{host}:{port}")
    print(f"[CMS] WSDL available at http://{host}:{port}/?wsdl")
    server.serve_forever()

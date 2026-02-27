"""
Mock Warehouse Management System (WMS)
========================================
A system that uses a PROPRIETARY MESSAGING PROTOCOL over TCP/IP
for real-time package status updates.

Protocol Specification
──────────────────────
All messages are newline-delimited text with pipe-separated fields:

  REQUEST:   <MSG_TYPE>|<FIELD1>|<FIELD2>|...\n
  RESPONSE:  <MSG_TYPE>_ACK|<STATUS>|<FIELD1>|...\n
  EVENT:     EVENT|<EVENT_TYPE>|<FIELD1>|...\n

Message Types:
  PKG_RECEIVE   – Register a new package in the warehouse
  PKG_STATUS    – Query current status of a package
  PKG_PROCESS   – Start processing (picking/packing) a package
  PKG_LOAD      – Mark a package as loaded onto a vehicle
  PKG_DISPATCH  – Mark a package as dispatched
  PKG_CANCEL    – Cancel a package in the warehouse
  PING          – Health check

Events (pushed asynchronously to connected clients):
  EVENT|STATUS_CHANGE|<package_id>|<old_status>|<new_status>|<timestamp>
"""

import asyncio
import json
import uuid
from datetime import datetime
from enum import Enum

# ── Package status lifecycle ───────────────────────────

class PkgStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    LOADED = "LOADED"
    DISPATCHED = "DISPATCHED"
    CANCELLED = "CANCELLED"


# ── In-memory store ────────────────────────────────────

packages: dict[str, dict] = {}
connected_clients: list[asyncio.StreamWriter] = []


# ── Protocol Handling ──────────────────────────────────

def build_response(*fields: str) -> bytes:
    return ("|".join(fields) + "\n").encode()


def build_event(event_type: str, *fields: str) -> bytes:
    return ("|".join(["EVENT", event_type, *fields]) + "\n").encode()


async def broadcast_event(event: bytes, exclude: asyncio.StreamWriter | None = None):
    """Send an event to all connected adapter clients."""
    dead = []
    for writer in connected_clients:
        if writer is exclude:
            continue
        try:
            writer.write(event)
            await writer.drain()
        except Exception:
            dead.append(writer)
    for w in dead:
        connected_clients.remove(w)


def sanitize_field(value: str) -> str:
    """Strip pipe characters and newlines to prevent protocol injection."""
    return value.replace("|", "").replace("\n", "").replace("\r", "").strip()


async def handle_message(line: str, writer: asyncio.StreamWriter) -> bytes | None:
    """Parse and execute a single protocol message."""
    parts = [sanitize_field(p) for p in line.strip().split("|")]
    msg_type = parts[0].upper() if parts else ""
    now = datetime.utcnow().isoformat()

    if msg_type == "PING":
        return build_response("PING_ACK", "OK", now)

    elif msg_type == "PKG_RECEIVE":
        # PKG_RECEIVE|<order_id>|<item_count>|<weight_kg>
        if len(parts) < 4:
            return build_response("PKG_RECEIVE_ACK", "ERROR", "MISSING_FIELDS")
        order_id, item_count, weight = parts[1], parts[2], parts[3]
        pkg_id = f"PKG-{uuid.uuid4().hex[:8].upper()}"
        packages[pkg_id] = {
            "package_id": pkg_id,
            "order_id": order_id,
            "item_count": int(item_count),
            "weight_kg": float(weight),
            "status": PkgStatus.RECEIVED,
            "created_at": now,
            "updated_at": now,
        }
        print(f"[WMS] Package {pkg_id} received for order {order_id}")
        await broadcast_event(
            build_event("STATUS_CHANGE", pkg_id, "NONE", PkgStatus.RECEIVED, now),
            exclude=writer,
        )
        return build_response("PKG_RECEIVE_ACK", "OK", pkg_id, order_id)

    elif msg_type == "PKG_STATUS":
        # PKG_STATUS|<package_id>
        if len(parts) < 2:
            return build_response("PKG_STATUS_ACK", "ERROR", "MISSING_FIELDS")
        pkg_id = parts[1]
        pkg = packages.get(pkg_id)
        if not pkg:
            return build_response("PKG_STATUS_ACK", "NOT_FOUND", pkg_id)
        return build_response(
            "PKG_STATUS_ACK", "OK", pkg_id, pkg["status"],
            pkg["order_id"], pkg["updated_at"],
        )

    elif msg_type == "PKG_PROCESS":
        # PKG_PROCESS|<package_id>
        if len(parts) < 2:
            return build_response("PKG_PROCESS_ACK", "ERROR", "MISSING_FIELDS")
        pkg_id = parts[1]
        pkg = packages.get(pkg_id)
        if not pkg:
            return build_response("PKG_PROCESS_ACK", "NOT_FOUND", pkg_id)
        old_status = pkg["status"]
        pkg["status"] = PkgStatus.PROCESSING
        pkg["updated_at"] = now
        print(f"[WMS] Package {pkg_id} → PROCESSING")
        await broadcast_event(
            build_event("STATUS_CHANGE", pkg_id, old_status, PkgStatus.PROCESSING, now)
        )
        return build_response("PKG_PROCESS_ACK", "OK", pkg_id, PkgStatus.PROCESSING)

    elif msg_type == "PKG_LOAD":
        # PKG_LOAD|<package_id>|<vehicle_id>
        if len(parts) < 3:
            return build_response("PKG_LOAD_ACK", "ERROR", "MISSING_FIELDS")
        pkg_id, vehicle_id = parts[1], parts[2]
        pkg = packages.get(pkg_id)
        if not pkg:
            return build_response("PKG_LOAD_ACK", "NOT_FOUND", pkg_id)
        old_status = pkg["status"]
        pkg["status"] = PkgStatus.LOADED
        pkg["vehicle_id"] = vehicle_id
        pkg["updated_at"] = now
        print(f"[WMS] Package {pkg_id} loaded onto {vehicle_id}")
        await broadcast_event(
            build_event("STATUS_CHANGE", pkg_id, old_status, PkgStatus.LOADED, now)
        )
        return build_response("PKG_LOAD_ACK", "OK", pkg_id, PkgStatus.LOADED, vehicle_id)

    elif msg_type == "PKG_DISPATCH":
        # PKG_DISPATCH|<package_id>
        if len(parts) < 2:
            return build_response("PKG_DISPATCH_ACK", "ERROR", "MISSING_FIELDS")
        pkg_id = parts[1]
        pkg = packages.get(pkg_id)
        if not pkg:
            return build_response("PKG_DISPATCH_ACK", "NOT_FOUND", pkg_id)
        old_status = pkg["status"]
        pkg["status"] = PkgStatus.DISPATCHED
        pkg["updated_at"] = now
        print(f"[WMS] Package {pkg_id} dispatched")
        await broadcast_event(
            build_event("STATUS_CHANGE", pkg_id, old_status, PkgStatus.DISPATCHED, now)
        )
        return build_response("PKG_DISPATCH_ACK", "OK", pkg_id, PkgStatus.DISPATCHED)

    elif msg_type == "PKG_CANCEL":
        # PKG_CANCEL|<package_id>
        if len(parts) < 2:
            return build_response("PKG_CANCEL_ACK", "ERROR", "MISSING_FIELDS")
        pkg_id = parts[1]
        pkg = packages.get(pkg_id)
        if not pkg:
            return build_response("PKG_CANCEL_ACK", "NOT_FOUND", pkg_id)
        old_status = pkg["status"]
        pkg["status"] = PkgStatus.CANCELLED
        pkg["updated_at"] = now
        print(f"[WMS] Package {pkg_id} cancelled")
        await broadcast_event(
            build_event("STATUS_CHANGE", pkg_id, old_status, PkgStatus.CANCELLED, now)
        )
        return build_response("PKG_CANCEL_ACK", "OK", pkg_id, PkgStatus.CANCELLED)

    elif msg_type == "PKG_LIST":
        # PKG_LIST  (no args – returns JSON blob for simplicity)
        data = json.dumps(list(packages.values()))
        return build_response("PKG_LIST_ACK", "OK", data)

    else:
        return build_response("ERROR", "UNKNOWN_MSG_TYPE", msg_type)


# ── TCP Server ─────────────────────────────────────────

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"[WMS] Adapter connected from {addr}")
    connected_clients.append(writer)

    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            line = data.decode().strip()
            if not line:
                continue
            print(f"[WMS] ← {line}")
            response = await handle_message(line, writer)
            if response:
                writer.write(response)
                await writer.drain()
                print(f"[WMS] → {response.decode().strip()}")
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[WMS] Client error: {exc}")
    finally:
        connected_clients.remove(writer)
        writer.close()
        await writer.wait_closed()
        print(f"[WMS] Adapter disconnected from {addr}")


async def main():
    host, port = "0.0.0.0", 9000
    server = await asyncio.start_server(handle_client, host, port)
    print(f"[WMS] TCP server listening on {host}:{port}")
    print(f"[WMS] Proprietary protocol ready – awaiting adapter connections")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

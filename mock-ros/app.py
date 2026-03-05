"""
Mock Route Optimisation System (ROS)
=====================================
A modern, cloud-based service that exposes a RESTful API for:
  - Accepting delivery addresses + vehicle availability
  - Generating optimised delivery routes
  - Returning route details with ETAs

Built with FastAPI.
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="SwiftLogistics – Route Optimisation System",
    description="Third-party cloud REST API for route planning",
    version="1.0.0",
)


# ── Models ─────────────────────────────────────────────

class DeliveryPoint(BaseModel):
    order_id: str
    street: str
    city: str
    postal_code: str
    lat: float | None = None
    lng: float | None = None
    priority: str = "normal"


class Vehicle(BaseModel):
    vehicle_id: str
    driver_id: str
    capacity_kg: float
    current_lat: float = 6.9271
    current_lng: float = 79.8612  # Colombo default


class OptimizeRequest(BaseModel):
    delivery_points: list[DeliveryPoint]
    vehicles: list[Vehicle]


class AddStopRequest(BaseModel):
    """Request to add a new stop to an existing route (dynamic route change)."""
    order_id: str
    street: str
    city: str
    postal_code: str
    priority: str = "normal"


class ReOptimizeRequest(BaseModel):
    """Request to re-optimise all routes for a specific driver."""
    driver_id: str
    new_delivery_point: DeliveryPoint | None = None


class RouteStop(BaseModel):
    sequence: int
    order_id: str
    street: str
    city: str
    estimated_arrival: str
    distance_from_prev_km: float


class DriverRoute(BaseModel):
    driver_id: str
    vehicle_id: str
    route_id: str
    stops: list[RouteStop]
    total_distance_km: float
    estimated_duration_min: float


class OptimizeResponse(BaseModel):
    optimization_id: str
    routes: list[DriverRoute]
    unassigned_orders: list[str]
    computed_at: datetime


class RouteChangeResponse(BaseModel):
    route_id: str
    driver_id: str
    change_type: str  # "STOP_ADDED", "RE_OPTIMISED"
    new_stop: RouteStop | None = None
    updated_stops: list[RouteStop]
    total_distance_km: float
    estimated_duration_min: float
    changed_at: datetime


# ── In-memory store ────────────────────────────────────

ROUTES: dict[str, dict] = {}


# ── Endpoints ──────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "route-optimisation-system"}


@app.post("/api/v1/optimize", response_model=OptimizeResponse)
async def optimize_route(req: OptimizeRequest):
    """
    Simulate route optimisation.  Introduces a small random delay
    to mimic real computation time.
    """
    # Simulate processing time (0.5 – 2 seconds)
    await asyncio.sleep(random.uniform(0.5, 2.0))

    if not req.delivery_points:
        raise HTTPException(400, "No delivery points provided")
    if not req.vehicles:
        raise HTTPException(400, "No vehicles available")

    optimization_id = f"OPT-{uuid.uuid4().hex[:8].upper()}"
    driver_routes: list[DriverRoute] = []
    unassigned: list[str] = []

    # Simple round-robin assignment across vehicles
    for idx, vehicle in enumerate(req.vehicles):
        assigned = [
            dp for j, dp in enumerate(req.delivery_points)
            if j % len(req.vehicles) == idx
        ]
        if not assigned:
            continue

        # Shuffle to simulate "optimisation"
        random.shuffle(assigned)

        stops: list[RouteStop] = []
        base_time = datetime.utcnow() + timedelta(hours=1)
        total_dist = 0.0

        for seq, dp in enumerate(assigned, start=1):
            dist = round(random.uniform(1.5, 12.0), 1)
            total_dist += dist
            eta = base_time + timedelta(minutes=seq * random.randint(8, 20))
            stops.append(
                RouteStop(
                    sequence=seq,
                    order_id=dp.order_id,
                    street=dp.street,
                    city=dp.city,
                    estimated_arrival=eta.strftime("%Y-%m-%d %H:%M"),
                    distance_from_prev_km=dist,
                )
            )

        route_id = f"RTE-{uuid.uuid4().hex[:6].upper()}"
        dr = DriverRoute(
            driver_id=vehicle.driver_id,
            vehicle_id=vehicle.vehicle_id,
            route_id=route_id,
            stops=stops,
            total_distance_km=round(total_dist, 1),
            estimated_duration_min=round(len(stops) * random.uniform(10, 18), 0),
        )
        driver_routes.append(dr)
        ROUTES[route_id] = dr.model_dump()

    resp = OptimizeResponse(
        optimization_id=optimization_id,
        routes=driver_routes,
        unassigned_orders=unassigned,
        computed_at=datetime.utcnow(),
    )
    print(f"[ROS] Optimised {len(req.delivery_points)} points → {len(driver_routes)} routes")
    return resp


@app.get("/api/v1/routes/{route_id}")
async def get_route(route_id: str):
    """Retrieve a previously computed route."""
    if route_id not in ROUTES:
        raise HTTPException(404, "Route not found")
    return ROUTES[route_id]


@app.get("/api/v1/routes")
async def list_routes():
    """List all computed routes."""
    return {"routes": list(ROUTES.values()), "count": len(ROUTES)}


# ── Route Change / Re-Optimisation Endpoints ──────────

@app.post("/api/v1/routes/{route_id}/add-stop", response_model=RouteChangeResponse)
async def add_stop_to_route(route_id: str, req: AddStopRequest):
    """
    Dynamically add a new stop to an existing route.
    This simulates what happens when a new high-priority order arrives
    and needs to be inserted into a driver's current route.
    """
    if route_id not in ROUTES:
        raise HTTPException(404, "Route not found")

    route = ROUTES[route_id]
    existing_stops = route["stops"]

    # Simulate processing delay
    await asyncio.sleep(random.uniform(0.3, 1.0))

    # Determine insertion position: high-priority goes near the front
    if req.priority == "high":
        insert_pos = min(1, len(existing_stops))  # After first stop
    else:
        insert_pos = len(existing_stops)  # Append at end

    # Create the new stop
    base_time = datetime.utcnow() + timedelta(minutes=random.randint(15, 45))
    dist = round(random.uniform(1.5, 10.0), 1)
    new_stop = RouteStop(
        sequence=insert_pos + 1,
        order_id=req.order_id,
        street=req.street,
        city=req.city,
        estimated_arrival=base_time.strftime("%Y-%m-%d %H:%M"),
        distance_from_prev_km=dist,
    )

    # Insert and re-sequence all stops
    stop_dicts = list(existing_stops)
    new_stop_dict = new_stop.model_dump()
    stop_dicts.insert(insert_pos, new_stop_dict)

    # Re-number sequences and recalculate ETAs
    total_dist = 0.0
    recalc_base = datetime.utcnow() + timedelta(hours=1)
    for i, s in enumerate(stop_dicts):
        s["sequence"] = i + 1
        dist_leg = s.get("distance_from_prev_km", round(random.uniform(2.0, 8.0), 1))
        total_dist += dist_leg
        eta = recalc_base + timedelta(minutes=(i + 1) * random.randint(10, 18))
        s["estimated_arrival"] = eta.strftime("%Y-%m-%d %H:%M")

    # Update the stored route
    route["stops"] = stop_dicts
    route["total_distance_km"] = round(total_dist, 1)
    route["estimated_duration_min"] = round(len(stop_dicts) * random.uniform(10, 18), 0)
    ROUTES[route_id] = route

    print(f"[ROS] Route {route_id}: added stop for order {req.order_id} (priority={req.priority})")

    return RouteChangeResponse(
        route_id=route_id,
        driver_id=route["driver_id"],
        change_type="STOP_ADDED",
        new_stop=new_stop,
        updated_stops=[RouteStop(**s) for s in stop_dicts],
        total_distance_km=route["total_distance_km"],
        estimated_duration_min=route["estimated_duration_min"],
        changed_at=datetime.utcnow(),
    )


@app.post("/api/v1/routes/re-optimize")
async def re_optimize_driver_routes(req: ReOptimizeRequest):
    """
    Re-optimise all routes for a specific driver.
    Called when a significant change requires full route recalculation.
    """
    await asyncio.sleep(random.uniform(0.5, 1.5))

    driver_routes = {rid: r for rid, r in ROUTES.items() if r.get("driver_id") == req.driver_id}

    if not driver_routes:
        raise HTTPException(404, f"No routes found for driver {req.driver_id}")

    changes = []
    for route_id, route in driver_routes.items():
        stops = route["stops"]
        # Shuffle stops to simulate re-optimisation
        random.shuffle(stops)

        total_dist = 0.0
        recalc_base = datetime.utcnow() + timedelta(hours=1)
        for i, s in enumerate(stops):
            s["sequence"] = i + 1
            dist_leg = round(random.uniform(1.5, 10.0), 1)
            s["distance_from_prev_km"] = dist_leg
            total_dist += dist_leg
            eta = recalc_base + timedelta(minutes=(i + 1) * random.randint(10, 18))
            s["estimated_arrival"] = eta.strftime("%Y-%m-%d %H:%M")

        route["stops"] = stops
        route["total_distance_km"] = round(total_dist, 1)
        route["estimated_duration_min"] = round(len(stops) * random.uniform(10, 18), 0)
        ROUTES[route_id] = route

        changes.append({
            "route_id": route_id,
            "driver_id": req.driver_id,
            "change_type": "RE_OPTIMISED",
            "updated_stops": stops,
            "total_distance_km": route["total_distance_km"],
            "estimated_duration_min": route["estimated_duration_min"],
            "changed_at": datetime.utcnow().isoformat(),
        })

    print(f"[ROS] Re-optimised {len(changes)} route(s) for driver {req.driver_id}")
    return {"driver_id": req.driver_id, "changes": changes, "count": len(changes)}


@app.get("/api/v1/routes/driver/{driver_id}")
async def get_driver_routes(driver_id: str):
    """Get all routes assigned to a specific driver."""
    driver_routes = [r for r in ROUTES.values() if r.get("driver_id") == driver_id]
    return {"driver_id": driver_id, "routes": driver_routes, "count": len(driver_routes)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)

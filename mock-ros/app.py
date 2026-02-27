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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)

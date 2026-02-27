"""Shared Pydantic models used across SwiftTrack services."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    IN_WAREHOUSE = "IN_WAREHOUSE"
    PROCESSING = "PROCESSING"
    LOADED = "LOADED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PackageStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    LOADED = "LOADED"
    DISPATCHED = "DISPATCHED"


class DeliveryResult(str, Enum):
    DELIVERED = "DELIVERED"
    FAILED_NOT_HOME = "FAILED_NOT_HOME"
    FAILED_WRONG_ADDRESS = "FAILED_WRONG_ADDRESS"
    FAILED_REFUSED = "FAILED_REFUSED"
    FAILED_OTHER = "FAILED_OTHER"


# ── Request / Response Models ──────────────────────────

class Address(BaseModel):
    street: str
    city: str
    postal_code: str
    lat: Optional[float] = None
    lng: Optional[float] = None


class OrderItem(BaseModel):
    item_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str
    quantity: int = 1
    weight_kg: float = 0.5


class OrderRequest(BaseModel):
    client_id: str
    recipient_name: str
    delivery_address: Address
    items: list[OrderItem]
    priority: str = "normal"  # normal | express


class OrderResponse(BaseModel):
    order_id: str
    status: OrderStatus
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RoutePoint(BaseModel):
    order_id: str
    address: Address
    sequence: int
    estimated_arrival: Optional[str] = None


class RouteResponse(BaseModel):
    route_id: str
    driver_id: str
    points: list[RoutePoint]
    total_distance_km: float
    estimated_duration_min: float


class DeliveryUpdate(BaseModel):
    order_id: str
    driver_id: str
    result: DeliveryResult
    notes: Optional[str] = None
    proof_photo_url: Optional[str] = None
    signature_data: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TrackingEvent(BaseModel):
    order_id: str
    status: OrderStatus
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    location: Optional[Address] = None


# ── Saga Models ────────────────────────────────────────

class SagaState(str, Enum):
    STARTED = "STARTED"
    CMS_CREATED = "CMS_CREATED"
    WMS_RECEIVED = "WMS_RECEIVED"
    ROS_OPTIMISED = "ROS_OPTIMISED"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    FAILED = "FAILED"


class SagaRecord(BaseModel):
    saga_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    state: SagaState = SagaState.STARTED
    cms_ref: Optional[str] = None
    wms_ref: Optional[str] = None
    ros_ref: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Auth Models ────────────────────────────────────────

class UserRole(str, Enum):
    CLIENT = "client"
    DRIVER = "driver"
    ADMIN = "admin"


class TokenPayload(BaseModel):
    sub: str  # user_id
    role: UserRole
    exp: int


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    user_id: str

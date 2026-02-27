"""Shared configuration helpers loaded from environment variables."""

from __future__ import annotations

import os


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# RabbitMQ
RABBITMQ_HOST = get_env("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(get_env("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = get_env("RABBITMQ_USER", "swifttrack")
RABBITMQ_PASS = get_env("RABBITMQ_PASS", "swifttrack123")

RABBITMQ_URL = (
    f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
)

# Redis
REDIS_HOST = get_env("REDIS_HOST", "localhost")
REDIS_PORT = int(get_env("REDIS_PORT", "6379"))

# JWT
JWT_SECRET = get_env("JWT_SECRET", "swifttrack-secret-key-change-in-production")
JWT_ALGORITHM = get_env("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_MINUTES = int(get_env("JWT_EXPIRATION_MINUTES", "60"))

# Service URLs (inside Docker network)
GATEWAY_PORT = int(get_env("GATEWAY_PORT", "8000"))
ORCHESTRATOR_HOST = get_env("ORCHESTRATOR_HOST", "localhost")
ORCHESTRATOR_PORT = int(get_env("ORCHESTRATOR_PORT", "8001"))
CMS_ADAPTER_HOST = get_env("CMS_ADAPTER_HOST", "localhost")
CMS_ADAPTER_PORT = int(get_env("CMS_ADAPTER_PORT", "8002"))
ROS_HOST = get_env("ROS_HOST", "localhost")
ROS_PORT = int(get_env("ROS_PORT", "8003"))
WMS_ADAPTER_HOST = get_env("WMS_ADAPTER_HOST", "localhost")
WMS_ADAPTER_PORT = int(get_env("WMS_ADAPTER_PORT", "8004"))
MOCK_CMS_HOST = get_env("MOCK_CMS_HOST", "localhost")
MOCK_CMS_PORT = int(get_env("MOCK_CMS_PORT", "8005"))
MOCK_WMS_HOST = get_env("MOCK_WMS_HOST", "localhost")
MOCK_WMS_PORT = int(get_env("MOCK_WMS_PORT", "9000"))


def orchestrator_url() -> str:
    return f"http://{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}"


def cms_adapter_url() -> str:
    return f"http://{CMS_ADAPTER_HOST}:{CMS_ADAPTER_PORT}"


def ros_url() -> str:
    return f"http://{ROS_HOST}:{ROS_PORT}"


def wms_adapter_url() -> str:
    return f"http://{WMS_ADAPTER_HOST}:{WMS_ADAPTER_PORT}"


def mock_cms_url() -> str:
    return f"http://{MOCK_CMS_HOST}:{MOCK_CMS_PORT}"

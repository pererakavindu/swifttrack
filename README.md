# SwiftTrack – Middleware Architecture for SwiftLogistics

A microservices-based middleware platform for **Swift Logistics (Pvt) Ltd.**, integrating three heterogeneous back-end systems through a modern event-driven architecture.

## 🏗️ Architecture Overview

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Client Portal  │     │   Driver App     │     │   Admin Panel    │
│   (HTML/JS SPA)  │     │   (Mobile Web)   │     │                  │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │  REST + WebSocket      │                         │
         ▼                        ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     API GATEWAY (FastAPI)                            │
│  • JWT Authentication  • Rate Limiting  • WebSocket Hub              │
│  • REST Routing        • CORS           • Redis Pub/Sub Listener     │
└────────┬──────────────────────┬───────────────────────┬─────────────┘
         │                      │                       │
         ▼                      ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  ORDER ORCHESTRATOR (Saga Pattern)                    │
│  • Saga State Machine   • Compensating Transactions                  │
│  • RabbitMQ Producer    • Redis State Store                          │
└────────┬──────────────────────┬───────────────────────┬─────────────┘
         │                      │                       │
         ▼                      ▼                       ▼
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐
│  CMS Adapter    │  │  WMS Adapter     │  │  Route Optimisation      │
│  SOAP→REST      │  │  TCP/IP→REST     │  │  System (REST)           │
│  (Zeep client)  │  │  (asyncio TCP)   │  │  (FastAPI)               │
└────────┬────────┘  └────────┬─────────┘  └──────────────────────────┘
         │                    │
         ▼                    ▼
┌─────────────────┐  ┌──────────────────┐
│  Mock CMS       │  │  Mock WMS        │
│  SOAP/XML API   │  │  TCP/IP Protocol │
│  (Spyne)        │  │  (asyncio)       │
└─────────────────┘  └──────────────────┘

Cross-cutting:
  ┌──────────────┐  ┌──────────────┐
  │  RabbitMQ    │  │  Redis       │
  │  (Messaging) │  │  (Cache/PubSub)│
  └──────────────┘  └──────────────┘
```

## 🧩 Services

| Service | Port | Protocol | Description |
|---------|------|----------|-------------|
| **Gateway** | 8000 | REST + WS | API entry point, JWT auth, WebSocket hub |
| **Orchestrator** | 8001 | REST | Saga-based order orchestration |
| **CMS Adapter** | 8002 | REST | SOAP-to-REST protocol translator |
| **Mock ROS** | 8003 | REST | Route optimisation (third-party mock) |
| **WMS Adapter** | 8004 | REST | TCP/IP-to-REST protocol translator |
| **Mock CMS** | 8005 | SOAP/XML | Legacy client management (Spyne) |
| **Mock WMS** | 9000 | TCP/IP | Warehouse management (proprietary protocol) |
| **RabbitMQ** | 5672/15672 | AMQP | Message broker (async processing) |
| **Redis** | 6379 | RESP | Cache, state store, Pub/Sub |

## 🚀 Quick Start

```bash
# Clone and start all services
cd SwiftTrack
docker compose up --build

# Access the platform
# Client Portal:  http://localhost:8000
# Driver App:     http://localhost:8000/driver
# API Docs:       http://localhost:8000/docs
# RabbitMQ Admin:  http://localhost:15672 (swifttrack/swifttrack123)
```

## 🔑 Demo Credentials

| Username | Password | Role | ID |
|----------|----------|------|----|
| megamart | pass123 | Client | CLI001 |
| shopeasy | pass123 | Client | CLI002 |
| freshgrocer | pass123 | Client | CLI003 |
| driver1 | pass123 | Driver | DRV-001 |
| driver2 | pass123 | Driver | DRV-002 |
| admin | admin123 | Admin | ADM-001 |

## 📐 Architectural Patterns

1. **API Gateway Pattern** – Single entry point with JWT auth, routing, rate limiting
2. **Saga Pattern (Orchestrator)** – Distributed transaction management with compensating actions
3. **Adapter Pattern** – Protocol translation (SOAP→REST, TCP→REST)
4. **Pub/Sub Pattern** – RabbitMQ for async event-driven communication
5. **Service Registry** – Docker Compose DNS-based service discovery
6. **CQRS (lightweight)** – Redis for fast reads, adapters for writes

## 🔄 Order Flow (Saga)

```
1. Client submits order → Gateway
2. Gateway → Orchestrator (start saga)
3. Saga Step 1: Orchestrator → CMS Adapter → Mock CMS (SOAP CreateOrder)
4. Saga Step 2: Orchestrator → WMS Adapter → Mock WMS (TCP PKG_RECEIVE)
5. Saga Step 3: Orchestrator → Mock ROS (REST POST /optimize)
6. Success: Publish ORDER_COMPLETED to RabbitMQ + Redis
7. WebSocket pushes real-time update to Client Portal

On failure at any step:
  - Compensating transactions roll back completed steps
  - CMS: CancelOrder | WMS: PKG_CANCEL
  - Saga state persisted in Redis for recovery
```

## 🛡️ Security Considerations

- **JWT Authentication** – All API endpoints require valid Bearer tokens
- **Role-Based Access Control** – Clients can only see their own orders; drivers can only update deliveries
- **CORS** – Configured on the gateway
- **Input Validation** – Pydantic models enforce strict input schemas
- **Transport Security** – TLS recommended for production (configured at load balancer level)
- **Message Broker Security** – RabbitMQ with username/password authentication
- **No Secrets in Code** – All credentials loaded from environment variables

## 📁 Project Structure

```
SwiftTrack/
├── docker-compose.yml          # Service orchestration
├── .env                        # Environment configuration
├── shared/                     # Shared models and config
│   ├── models.py
│   └── config.py
├── gateway/                    # API Gateway (FastAPI)
│   ├── app.py
│   ├── static/
│   │   ├── index.html          # Client Portal UI
│   │   └── driver.html         # Driver App UI
│   ├── requirements.txt
│   └── Dockerfile
├── orchestrator/               # Order Orchestrator (Saga)
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── adapters/
│   ├── cms-adapter/            # SOAP → REST adapter
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── wms-adapter/            # TCP/IP → REST adapter
│       ├── app.py
│       ├── requirements.txt
│       └── Dockerfile
├── mock-cms/                   # Mock CMS (SOAP/XML)
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── mock-ros/                   # Mock ROS (REST/JSON)
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
└── mock-wms/                   # Mock WMS (TCP/IP)
    ├── app.py
    └── Dockerfile
```

## 🔧 Technology Stack

- **Language**: Python 3.11
- **API Framework**: FastAPI + Uvicorn
- **SOAP**: Spyne (server) + Zeep (client)
- **TCP/IP**: Python asyncio streams
- **Message Broker**: RabbitMQ (aio-pika)
- **Cache/PubSub**: Redis
- **Auth**: PyJWT
- **HTTP Client**: httpx (async)
- **Containers**: Docker + Docker Compose
- **Frontend**: Vanilla HTML/CSS/JavaScript

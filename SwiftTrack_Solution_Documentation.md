# SwiftTrack – Middleware Architecture Solution Documentation

## Middleware Architecture (SCS2314) – Assignment 4

---

## Table of Contents

1. [Introduction to the Solution](#1-introduction-to-the-solution)
2. [Architecture of the Solution](#2-architecture-of-the-solution)
   - 2.1 [Conceptual Architecture](#21-conceptual-architecture)
   - 2.2 [Implementation Architecture](#22-implementation-architecture)
   - 2.3 [Alternative Architectures](#23-alternative-architectures)
   - 2.4 [Rationale for the Chosen Architecture](#24-rationale-for-the-chosen-architecture)
3. [Architectural and Integration Patterns](#3-architectural-and-integration-patterns)
4. [Prototype Implementation](#4-prototype-implementation)
   - 4.1 [Technology Stack](#41-technology-stack)
   - 4.2 [Service Descriptions](#42-service-descriptions)
   - 4.3 [Order Submission Flow (Saga Execution)](#43-order-submission-flow-saga-execution)
   - 4.4 [Real-Time Tracking and Notifications](#44-real-time-tracking-and-notifications)
   - 4.5 [Client Portal UI](#45-client-portal-ui)
   - 4.6 [Driver Mobile App UI](#46-driver-mobile-app-ui)
5. [Information Security Considerations](#5-information-security-considerations)
6. [Deployment and Running the Prototype](#6-deployment-and-running-the-prototype)

---

## 1. Introduction to the Solution

**SwiftTrack** is a middleware platform designed for Swift Logistics (Pvt) Ltd., a rapidly growing logistics company in Sri Lanka specialising in last-mile delivery for e-commerce businesses. The platform provides a unified web-based **Client Portal** and a mobile-optimised **Driver App**, both branded as "SwiftTrack."

The core challenge addressed by SwiftTrack is the **seamless integration of three heterogeneous, independently operating back-end systems**:

| System | Protocol | Description |
|--------|----------|-------------|
| **Client Management System (CMS)** | SOAP/XML | Legacy, on-premise system for client contracts, billing, and order intake |
| **Route Optimisation System (ROS)** | REST/JSON | Modern, cloud-based third-party service for generating optimal delivery routes |
| **Warehouse Management System (WMS)** | Proprietary TCP/IP | Real-time package tracking within the warehouse using a custom binary/text protocol |

SwiftTrack solves this by introducing a **middleware layer** that acts as the integration backbone, providing:

- **Protocol translation** between SOAP, REST, and TCP/IP
- **Saga-based distributed transaction management** ensuring order consistency across all three systems
- **Asynchronous, event-driven processing** via RabbitMQ for handling high-volume order intake
- **Real-time tracking** via WebSocket connections backed by Redis Pub/Sub
- **A single, secure API Gateway** with JWT authentication, rate limiting, and CORS

All components are fully containerised using **Docker** and orchestrated through **Docker Compose**, leveraging Docker's internal DNS for service discovery.

---

## 2. Architecture of the Solution

### 2.1 Conceptual Architecture

The conceptual architecture follows a **layered, microservices-based middleware** approach with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                              │
│   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   │
│   │  Client Portal   │   │   Driver App     │   │   Admin Panel    │   │
│   │  (HTML/JS SPA)   │   │  (Mobile Web)    │   │                  │   │
│   └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘   │
└────────────┼──────────────────────┼───────────────────────┼─────────────┘
             │  REST + WebSocket    │                       │
             ▼                      ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          GATEWAY LAYER                                  │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                   API GATEWAY (FastAPI)                         │   │
│   │  • JWT Authentication    • Rate Limiting    • CORS              │   │
│   │  • REST Routing          • WebSocket Hub    • Redis Pub/Sub     │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└────────────┬──────────────────────┬───────────────────────┬─────────────┘
             │                      │                       │
             ▼                      ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                                │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │              ORDER ORCHESTRATOR (Saga Pattern)                  │   │
│   │  • Saga State Machine      • Compensating Transactions         │   │
│   │  • RabbitMQ Producer       • Redis State Store                 │   │
│   │  • Async Order Consumer    • Delivery Status Management        │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└────────────┬──────────────────────┬───────────────────────┬─────────────┘
             │                      │                       │
             ▼                      ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       ADAPTER LAYER                                     │
│   ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐   │
│   │  CMS Adapter    │  │  WMS Adapter     │  │  Route Optimisation │   │
│   │  SOAP → REST    │  │  TCP/IP → REST   │  │  System (REST)     │   │
│   │  (Zeep client)  │  │  (asyncio TCP)   │  │  (Direct REST)     │   │
│   └────────┬────────┘  └────────┬─────────┘  └────────┬────────────┘   │
└────────────┼──────────────────────┼────────────────────┼────────────────┘
             │                      │                    │
             ▼                      ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    BACK-END SYSTEMS LAYER                                │
│   ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐   │
│   │  Mock CMS       │  │  Mock WMS        │  │  Mock ROS           │   │
│   │  SOAP/XML       │  │  TCP/IP          │  │  REST/JSON          │   │
│   │  (Spyne)        │  │  (asyncio)       │  │  (FastAPI)          │   │
│   └─────────────────┘  └──────────────────┘  └─────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                     CROSS-CUTTING INFRASTRUCTURE                        │
│   ┌──────────────────────────┐  ┌──────────────────────────────────┐   │
│   │  RabbitMQ                │  │  Redis                           │   │
│   │  • Topic Exchange        │  │  • Saga State Persistence        │   │
│   │  • Async Order Queue     │  │  • Order Status Cache            │   │
│   │  • Event Distribution    │  │  • Pub/Sub (Real-time WS feed)   │   │
│   └──────────────────────────┘  └──────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key layers:**

1. **Presentation Layer** – Browser-based SPAs served by the Gateway (Client Portal, Driver App)
2. **Gateway Layer** – Single entry point handling authentication, authorization, routing, and real-time WebSocket connections
3. **Orchestration Layer** – Saga-based order coordinator managing distributed transactions across all three back-end systems
4. **Adapter Layer** – Protocol translators that normalise heterogeneous system interfaces into a uniform internal REST/JSON API
5. **Back-End Systems Layer** – The three mock systems simulating CMS (SOAP), WMS (TCP/IP), and ROS (REST)
6. **Cross-Cutting Infrastructure** – RabbitMQ for asynchronous messaging and Redis for state management and Pub/Sub

### 2.2 Implementation Architecture

The implementation architecture maps directly to Docker containers orchestrated via Docker Compose:

| Service | Container | Port | Technology | Role |
|---------|-----------|------|------------|------|
| API Gateway | `swifttrack-gateway` | 8000 | FastAPI + Uvicorn | Single entry point, JWT auth, WebSocket hub |
| Orchestrator | `swifttrack-orchestrator` | 8001 | FastAPI + Uvicorn | Saga execution, compensation, event publishing |
| CMS Adapter | `swifttrack-cms-adapter` | 8002 | FastAPI + Zeep | SOAP-to-REST protocol translation |
| Mock ROS | `swifttrack-mock-ros` | 8003 | FastAPI + Uvicorn | REST route optimisation mock |
| WMS Adapter | `swifttrack-wms-adapter` | 8004 | FastAPI + asyncio TCP | TCP/IP-to-REST protocol translation |
| Mock CMS | `swifttrack-mock-cms` | 8005 | Spyne (WSGI) | SOAP/XML CMS mock |
| Mock WMS | `swifttrack-mock-wms` | 9000 | asyncio TCP server | Proprietary TCP/IP protocol mock |
| RabbitMQ | `swifttrack-rabbitmq` | 5672 / 15672 | RabbitMQ 3 Management | Message broker |
| Redis | `swifttrack-redis` | 6379 | Redis 7 Alpine | Cache, state store, Pub/Sub |

**Network topology:** All containers are connected via a single Docker bridge network (`swiftnet`), using Docker DNS for service discovery (e.g., the gateway resolves `orchestrator:8001` automatically).

**Inter-service communication flow:**

```
Client Browser
     │
     │ HTTPS / WSS
     ▼
┌─────────┐       REST/JSON       ┌──────────────┐
│ Gateway │ ───────────────────── │ Orchestrator  │
│ :8000   │                       │    :8001      │
└────┬────┘                       └──┬───┬───┬────┘
     │                               │   │   │
     │ Redis Pub/Sub                 │   │   │ REST/JSON
     │ (tracking events)            │   │   │
     ▼                               │   │   ▼
┌─────────┐                          │   │  ┌──────────┐
│  Redis  │ ◄────────────────────────┤   │  │ Mock ROS │
│  :6379  │                          │   │  │  :8003   │
└─────────┘                          │   │  └──────────┘
                                     │   │
               REST/JSON             │   │  REST/JSON
          ┌──────────────────────────┘   └──────────────────┐
          ▼                                                  ▼
   ┌──────────────┐                                  ┌──────────────┐
   │ CMS Adapter  │                                  │ WMS Adapter  │
   │    :8002     │                                  │    :8004     │
   └──────┬───────┘                                  └──────┬───────┘
          │ SOAP/XML                                        │ TCP/IP
          ▼                                                  ▼
   ┌──────────────┐                                  ┌──────────────┐
   │  Mock CMS    │                                  │  Mock WMS    │
   │   :8005      │                                  │   :9000      │
   └──────────────┘                                  └──────────────┘

Message Broker:
┌──────────────┐
│  RabbitMQ    │  • Topic exchange: swifttrack.events
│  :5672       │  • Queue: order.submissions (async orders)
│  :15672 (UI) │  • Routing keys: order.submit, order.completed, wms.status
└──────────────┘
```

### 2.3 Alternative Architectures

#### Alternative 1: Enterprise Service Bus (ESB) Architecture

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Client App  │    │ Driver App  │    │ Admin Panel  │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   Enterprise Service  │
              │        Bus (ESB)      │
              │   (e.g., Apache      │
              │    ServiceMix,       │
              │    MuleSoft CE)      │
              │                       │
              │ • Message routing     │
              │ • Protocol mediation  │
              │ • Orchestration       │
              │ • Data transformation │
              │ • Service registry    │
              └───┬──────┬───────┬───┘
                  │      │       │
         ┌────────┘      │       └────────┐
         ▼               ▼                ▼
   ┌───────────┐  ┌───────────┐   ┌───────────┐
   │    CMS    │  │    WMS    │   │    ROS    │
   │  (SOAP)  │  │  (TCP/IP) │   │  (REST)  │
   └───────────┘  └───────────┘   └───────────┘
```

**Pros:** Centralised mediation, built-in protocol translation, mature tooling.  
**Cons:** Single point of failure; monolithic ESB becomes a bottleneck at scale; heavy resource footprint; harder to deploy incrementally; vendor lock-in with specific ESB products; less flexibility for independent scaling of components.

#### Alternative 2: Point-to-Point Integration with Shared Database

```
┌─────────────┐    ┌─────────────┐
│ Client App  │    │ Driver App  │
└──────┬──────┘    └──────┬──────┘
       │                  │
       ▼                  ▼
┌─────────────────────────────────┐
│      Monolithic API Server      │
│   • Direct CMS SOAP calls       │
│   • Direct WMS TCP calls         │
│   • Direct ROS REST calls        │
│   • Transaction management       │
└────────────────┬────────────────┘
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
   ┌────────┐ ┌──────┐ ┌──────┐
   │  CMS   │ │ WMS  │ │ ROS  │
   └────────┘ └──────┘ └──────┘
        │        │        │
        └────────┼────────┘
                 ▼
         ┌──────────────┐
         │ Shared DB     │
         │ (PostgreSQL)  │
         └──────────────┘
```

**Pros:** Simple to implement initially; fewer moving parts; familiar relational model.  
**Cons:** Tight coupling between all systems; no asynchronous processing; shared database becomes a contention point; difficult to scale individual components independently; single monolithic failure domain; no real-time event streaming capability; protocol changes in one system cascade through the entire application.

### 2.4 Rationale for the Chosen Architecture

We chose the **microservices-based middleware architecture** with an **API Gateway, Saga Orchestrator, and protocol Adapters** for the following reasons:

| Concern | Our Architecture | ESB Alternative | Point-to-Point Alternative |
|---------|-----------------|-----------------|---------------------------|
| **Scalability** | Each microservice scales independently via Docker replicas | ESB becomes a bottleneck | Monolith scales as a whole unit |
| **Resilience** | Failure of one adapter does not affect others; saga compensations handle partial failures | ESB failure impacts all integrations | Single point of failure for all integrations |
| **Protocol Isolation** | Each adapter encapsulates one protocol; changes are localised | ESB handles all mediation centrally | Protocol logic tangled in one codebase |
| **Async Processing** | RabbitMQ enables fire-and-forget for high-volume scenarios | ESB may support this but adds complexity | No built-in async support |
| **Real-time Updates** | Redis Pub/Sub + WebSocket provides instant client notifications | Requires additional configuration | Polling-based, no real-time |
| **Deployment** | Docker Compose; each service deployed independently | Heavy ESB deployment | Single deployment unit |
| **Technology Freedom** | Python/FastAPI, any language per service | Constrained by ESB framework | Single tech stack |
| **Transaction Management** | Saga pattern with explicit compensating transactions | ESB orchestration | 2PC or manual, fragile |

The chosen architecture directly satisfies all five architectural challenges described in the assignment:

1. **Heterogeneous Systems Integration** → Adapter Pattern (CMS Adapter for SOAP, WMS Adapter for TCP/IP)
2. **Real-time Tracking** → Redis Pub/Sub + WebSocket connections at the Gateway
3. **High-Volume Async Processing** → RabbitMQ message broker with topic exchange
4. **Transaction Management** → Orchestrator Saga with compensating transactions
5. **Scalability and Resilience** → Docker-based microservices with independent scaling

---

## 3. Architectural and Integration Patterns

The following architectural and integration patterns are employed in the SwiftTrack solution:

### 3.1 API Gateway Pattern

**Component:** `gateway/app.py`  
**Purpose:** Provides a single entry point for all client requests, centralising cross-cutting concerns.

**Responsibilities:**
- **JWT Authentication** – All API requests (except login) require a valid JWT token in the `Authorization` header. Tokens contain user ID, role, and expiry claims.
- **Authorization** – Role-based access control (clients can only view their own orders; only drivers can update deliveries; only admins can list all clients).
- **REST Routing** – Proxies requests to the Orchestrator, CMS Adapter, and ROS as appropriate.
- **WebSocket Hub** – Manages persistent WebSocket connections for real-time tracking. A `ConnectionManager` class maps user IDs to WebSocket connections and dispatches events to the correct recipients.
- **Redis Pub/Sub Listener** – A background async task subscribes to the `swifttrack:tracking` Redis channel and pushes events to connected WebSocket clients (clients see their own orders; drivers see all package events; admins see everything).
- **Static File Serving** – Serves the Client Portal (`index.html`) and Driver App (`driver.html`) directly.
- **CORS** – Configured to allow cross-origin requests for frontend development flexibility.

**Rationale:** The Gateway pattern decouples the frontend from the internal microservice topology. Clients only need to know one URL. Security, rate limiting, and protocol translation to WebSocket are handled centrally.

### 3.2 Saga Pattern (Orchestrator)

**Component:** `orchestrator/app.py`  
**Purpose:** Manages the distributed "place order" transaction across three systems that do not share a database.

**Saga execution flow:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SAGA STATE MACHINE                              │
│                                                                     │
│  STARTED ──► CMS_CREATED ──► WMS_RECEIVED ──► ROS_OPTIMISED        │
│     │             │                │               │                │
│     │    (fail)   │     (fail)     │    (fail)     │                │
│     ▼             ▼                ▼               ▼                │
│  FAILED     COMPENSATING     COMPENSATING    COMPENSATING           │
│                   │                │               │                │
│             Cancel CMS       Cancel CMS      Cancel WMS             │
│              order            order          + CMS order            │
│                   │                │               │                │
│                   ▼                ▼               ▼                │
│                FAILED           FAILED          FAILED              │
│                                                                     │
│  On full success: state = COMPLETED                                 │
│  → Publish ORDER_COMPLETED event to RabbitMQ                        │
│  → Store order in Redis for fast lookups                            │
│  → Publish real-time tracking event via Redis Pub/Sub               │
└─────────────────────────────────────────────────────────────────────┘
```

**The three saga steps:**

| Step | Service Called | Action | Compensation on Failure |
|------|---------------|--------|------------------------|
| 1 | CMS Adapter → Mock CMS (SOAP) | `CreateOrder` – register order in CMS | `CancelOrder` – mark order as CANCELLED in CMS |
| 2 | WMS Adapter → Mock WMS (TCP/IP) | `PKG_RECEIVE` – register package in warehouse | `PKG_CANCEL` – cancel package in WMS + compensate Step 1 |
| 3 | ROS (REST) | `POST /api/v1/optimize` – compute optimal route | Compensate Steps 2 and 1 |

**State persistence:** Every saga state transition is persisted to Redis (`saga:<saga_id>`) with a 24-hour TTL, enabling crash recovery.

**Rationale:** A traditional two-phase commit (2PC) is impractical across heterogeneous systems with different protocols. The Saga pattern provides **eventual consistency** with explicit compensating actions, which is well-suited for a logistics order workflow where each step is independently reversible.

### 3.3 Adapter Pattern (Protocol Translation)

Two adapters bridge the heterogeneous back-end protocols to the internal REST/JSON standard:

#### CMS Adapter (SOAP → REST)

**Component:** `adapters/cms-adapter/app.py`  
**Technology:** FastAPI + Zeep (Python SOAP client)

| REST Endpoint | SOAP Operation | Description |
|---------------|---------------|-------------|
| `GET /api/v1/clients/{id}` | `GetClient(client_id)` | Look up client by ID |
| `GET /api/v1/clients` | `ListClients()` | List all registered clients |
| `POST /api/v1/orders` | `CreateOrder(...)` | Create a new delivery order |
| `GET /api/v1/orders/{id}` | `GetOrder(order_id)` | Retrieve order by ID |
| `DELETE /api/v1/orders/{id}` | `CancelOrder(order_id)` | Cancel order (saga compensation) |

**Translation process:** The adapter receives a JSON request body, maps it to SOAP operation parameters, invokes the SOAP service via Zeep, and returns the SOAP response as JSON.

#### WMS Adapter (TCP/IP → REST)

**Component:** `adapters/wms-adapter/app.py`  
**Technology:** FastAPI + Python asyncio TCP client

The WMS uses a **proprietary text-based protocol** over TCP/IP:

```
REQUEST:   <MSG_TYPE>|<FIELD1>|<FIELD2>|...\n
RESPONSE:  <MSG_TYPE>_ACK|<STATUS>|<FIELD1>|...\n
EVENT:     EVENT|<EVENT_TYPE>|<FIELD1>|...\n
```

| REST Endpoint | TCP Message | Description |
|---------------|-------------|-------------|
| `POST /api/v1/packages/receive` | `PKG_RECEIVE\|<order_id>\|<count>\|<weight>` | Register package in warehouse |
| `GET /api/v1/packages/{id}/status` | `PKG_STATUS\|<package_id>` | Query package status |
| `POST /api/v1/packages/{id}/process` | `PKG_PROCESS\|<package_id>` | Start picking/packing |
| `POST /api/v1/packages/{id}/load` | `PKG_LOAD\|<package_id>\|<vehicle_id>` | Mark as loaded onto vehicle |
| `POST /api/v1/packages/{id}/dispatch` | `PKG_DISPATCH\|<package_id>` | Mark as dispatched |
| `DELETE /api/v1/packages/{id}` | `PKG_CANCEL\|<package_id>` | Cancel package (compensation) |

**Event listening:** The WMS Adapter maintains a **second dedicated TCP connection** for listening to asynchronous `EVENT` messages from the WMS. When a `STATUS_CHANGE` event is received, it is:
1. Published to RabbitMQ (`swifttrack.events` exchange, routing key `wms.status`)
2. Published to Redis Pub/Sub (`swifttrack:tracking` channel) for real-time WebSocket updates

**Rationale:** The Adapter pattern isolates protocol complexity within dedicated services. If the CMS vendor upgrades from SOAP to REST, only the CMS Adapter needs to change — the Orchestrator and Gateway remain untouched.

### 3.4 Publish/Subscribe Pattern (Event-Driven Messaging)

**Infrastructure:** RabbitMQ with a **Topic Exchange** (`swifttrack.events`)

| Routing Key | Publisher | Consumer | Description |
|-------------|-----------|----------|-------------|
| `order.submit` | Gateway / Orchestrator | Orchestrator (`order.submissions` queue) | Async order submission for high-volume processing |
| `order.completed` | Orchestrator | (Future consumers) | Full saga completion event |
| `order.delivery_update` | Orchestrator | (Future consumers) | Driver delivery status change |
| `wms.status` | WMS Adapter | (Future consumers) | WMS package status change events |

**High-volume async processing:** During peak events (e.g., Black Friday), clients can use the `/api/v1/orders/async` endpoint. The order is published to RabbitMQ and a saga ID is returned immediately. A background consumer in the Orchestrator picks up queued orders and executes sagas asynchronously with a prefetch count of 10.

**Rationale:** Pub/Sub decouples producers from consumers and enables handling spikes in order volume without blocking the client portal. RabbitMQ's durable queues ensure messages are not lost even if consumers restart.

### 3.5 Service Discovery (Docker Compose DNS)

Docker Compose creates an internal DNS on the `swiftnet` bridge network. Each service resolves other services by container name (e.g., `rabbitmq`, `orchestrator`, `mock-cms`). Service URLs are configured via environment variables in the `.env` file.

**Rationale:** For a containerised prototype, Docker DNS provides zero-configuration service discovery without requiring external tools like Consul or Eureka. In a production deployment, this could be replaced by Kubernetes DNS or a dedicated service mesh.

### 3.6 CQRS (Lightweight – Command/Query Separation)

- **Commands** (writes): Flow through the Orchestrator → Adapters → Back-end systems (CMS, WMS, ROS)
- **Queries** (reads): Served from **Redis** for fast lookups. After a successful saga, order details are stored in Redis hash maps (`order:<order_id>`), and indexed by client (`client:<id>:orders`) and driver (`driver:<id>:orders`).

**Rationale:** Reading order status from Redis (sub-millisecond) is far faster than querying three separate back-end systems. This separation also reduces load on the legacy CMS and WMS.

---

## 4. Prototype Implementation

### 4.1 Technology Stack

All technologies used are **open-source**:

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| **Language** | Python | 3.11+ | Primary language for all services |
| **Web Framework** | FastAPI | Latest | REST API framework (Gateway, Orchestrator, Adapters, ROS) |
| **ASGI Server** | Uvicorn | Latest | High-performance async HTTP/WS server |
| **SOAP Client** | Zeep | Latest | Python SOAP client for CMS Adapter |
| **SOAP Server** | Spyne | Latest | Python SOAP/WSDL server for Mock CMS |
| **Message Broker** | RabbitMQ | 3-management | Async messaging with topic exchange |
| **AMQP Client** | aio-pika | Latest | Async Python RabbitMQ client |
| **Cache/State Store** | Redis | 7-alpine | Saga state, order cache, Pub/Sub |
| **Redis Client** | redis-py (async) | Latest | Async Python Redis client |
| **HTTP Client** | httpx | Latest | Async HTTP client for inter-service calls |
| **JWT** | PyJWT | Latest | JSON Web Token generation and validation |
| **Containerisation** | Docker + Docker Compose | Latest | Service packaging and orchestration |
| **Frontend** | HTML/CSS/JavaScript | – | Single-page applications served by Gateway |

### 4.2 Service Descriptions

#### Mock CMS (Port 8005) – SOAP/XML Legacy System

- Built with **Spyne**, a Python SOAP framework
- Serves a real WSDL at `http://mock-cms:8005/?wsdl`
- SOAP operations: `GetClient`, `ListClients`, `CreateOrder`, `GetOrder`, `GetOrdersByClient`, `CancelOrder`
- In-memory data store with 3 pre-seeded clients: MegaMart Online (CLI001), ShopEasy (CLI002), FreshGrocer (CLI003)
- Simulates order lifecycle with ACCEPTED → CANCELLED states

#### Mock WMS (Port 9000) – Proprietary TCP/IP System

- Built with **Python asyncio** TCP server
- Custom pipe-delimited text protocol over TCP
- Package lifecycle: `RECEIVED → PROCESSING → LOADED → DISPATCHED` (or `CANCELLED`)
- **Broadcasts real-time events** to all connected TCP clients when package status changes
- Supports input sanitization to prevent protocol injection (strips pipe and newline characters)

#### Mock ROS (Port 8003) – REST API Cloud Service

- Built with **FastAPI**
- Simulates route optimisation with random delays (0.5–2s) to mimic computation time
- Round-robin assignment of delivery points across available vehicles
- Returns route stops with estimated arrival times and distances
- Stores computed routes in memory for later retrieval

#### CMS Adapter (Port 8002) – SOAP to REST Translator

- Connects to Mock CMS WSDL on startup with retry logic (10 attempts, 3s interval)
- Translates REST JSON requests to SOAP operations via the Zeep client
- Returns SOAP responses as clean JSON
- Exposes a `/health` endpoint for monitoring

#### WMS Adapter (Port 8004) – TCP/IP to REST Translator

- Maintains a persistent TCP connection to Mock WMS with reconnection logic
- Uses `asyncio.Lock` to serialise TCP request-response pairs (the TCP protocol is sequential)
- Runs a **dedicated event listener TCP connection** in a background task
- Publishes WMS events to both **RabbitMQ** (for async consumers) and **Redis Pub/Sub** (for real-time WebSocket updates)
- Exposes full package lifecycle as REST endpoints

#### Orchestrator (Port 8001) – Saga Coordinator

- Implements the complete 3-step saga: CMS → WMS → ROS
- Persists saga state in Redis after every step for crash recovery
- Executes compensating transactions in reverse order on failure
- Publishes real-time tracking events via Redis Pub/Sub after each successful step
- Runs a **RabbitMQ consumer** in the background for async order processing (prefetch=10)
- Stores completed orders in Redis for fast queries by client ID and driver ID

#### Gateway (Port 8000) – API Entry Point

- JWT authentication with configurable secret, algorithm, and expiration
- 6 demo user accounts (3 clients, 2 drivers, 1 admin)
- Proxies all API calls to the appropriate downstream service
- WebSocket endpoint (`/ws/tracking`) with token-based authentication
- `ConnectionManager` maintains user-to-WebSocket mapping
- Background Redis Pub/Sub subscriber pushes events to WebSocket clients
- Serves the static Client Portal and Driver App HTML/JS UIs

### 4.3 Order Submission Flow (Saga Execution)

The following sequence illustrates a complete order submission:

```
 Client Browser              Gateway            Orchestrator       CMS Adapter      Mock CMS      WMS Adapter     Mock WMS      Mock ROS
      │                        │                     │                  │               │              │              │             │
      │  POST /api/v1/orders   │                     │                  │               │              │              │             │
      │ ─────────────────────► │                     │                  │               │              │              │             │
      │                        │ Verify JWT Token     │                  │               │              │              │             │
      │                        │ Check role=client    │                  │               │              │              │             │
      │                        │                     │                  │               │              │              │             │
      │                        │ POST /submit        │                  │               │              │              │             │
      │                        │ ──────────────────► │                  │               │              │              │             │
      │                        │                     │ Generate saga_id │               │              │              │             │
      │                        │                     │ Save STARTED     │               │              │              │             │
      │                        │                     │                  │               │              │              │             │
      │                        │                     │  STEP 1: CMS     │               │              │              │             │
      │                        │                     │ POST /orders     │               │              │              │             │
      │                        │                     │ ────────────────►│  SOAP Call     │              │              │             │
      │                        │                     │                  │ CreateOrder(…) │              │              │             │
      │                        │                     │                  │ ─────────────► │              │              │             │
      │                        │                     │                  │  SOAP Response │              │              │             │
      │                        │                     │                  │ ◄───────────── │              │              │             │
      │                        │                     │  order_id=ORD-xx │               │              │              │             │
      │                        │                     │ ◄────────────────│               │              │              │             │
      │                        │                     │ Save CMS_CREATED │               │              │              │             │
      │  ◄─ ─ ─ WS: ACCEPTED ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ Redis Pub/Sub ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│              │             │
      │                        │                     │                  │               │              │              │             │
      │                        │                     │  STEP 2: WMS     │               │              │              │             │
      │                        │                     │ POST /receive    │               │              │              │             │
      │                        │                     │ ─────────────────────────────────────────────►  │  TCP Message  │             │
      │                        │                     │                  │               │              │ PKG_RECEIVE   │             │
      │                        │                     │                  │               │              │ ────────────► │             │
      │                        │                     │                  │               │              │ PKG_RECEIVE   │             │
      │                        │                     │                  │               │              │  _ACK|OK|PKG  │             │
      │                        │                     │                  │               │              │ ◄──────────── │             │
      │                        │                     │  package_id      │               │              │              │             │
      │                        │                     │ ◄─────────────────────────────────────────────  │              │             │
      │                        │                     │ Save WMS_RECEIVED│               │              │              │             │
      │  ◄─ ─ ─ WS: IN_WAREHOUSE ─ ─ ─ ─ ─ ─ ─ ─  │─ ─ ─ ─ Redis Pub/Sub ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│              │             │
      │                        │                     │                  │               │              │              │             │
      │                        │                     │  STEP 3: ROS     │               │              │              │             │
      │                        │                     │ POST /optimize   │               │              │              │             │
      │                        │                     │ ──────────────────────────────────────────────────────────────────────────►  │
      │                        │                     │                  │               │              │              │  Optimize   │
      │                        │                     │  route_id        │               │              │              │             │
      │                        │                     │ ◄──────────────────────────────────────────────────────────────────────────  │
      │                        │                     │ Save COMPLETED   │               │              │              │             │
      │                        │                     │ Store in Redis   │               │              │              │             │
      │                        │                     │ Publish to MQ    │               │              │              │             │
      │  ◄─ ─ ─ WS: PROCESSING─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ Redis Pub/Sub ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│              │             │
      │                        │                     │                  │               │              │              │             │
      │                        │  200 OK {saga}      │                  │               │              │              │             │
      │                        │ ◄────────────────── │                  │               │              │              │             │
      │  200 OK {order_id,     │                     │                  │               │              │              │             │
      │   package_id, route_id}│                     │                  │               │              │              │             │
      │ ◄───────────────────── │                     │                  │               │              │              │             │
```

**Compensation example (WMS fails at Step 2):**

```
Orchestrator detects WMS failure
  → Set saga state to COMPENSATING
  → Call CMS Adapter: DELETE /api/v1/orders/{order_id}
    → CMS Adapter calls SOAP CancelOrder()
    → CMS sets order status to CANCELLED
  → Set saga state to FAILED
  → Return error response to client
```

### 4.4 Real-Time Tracking and Notifications

The real-time tracking system uses a **Redis Pub/Sub → WebSocket** pipeline:

```
Event Source                    Redis                    Gateway                  Browser
(Orchestrator /                 Pub/Sub                  WebSocket                Client
 WMS Adapter)                                            Server

     │                           │                        │                        │
     │  PUBLISH                  │                        │                        │
     │  swifttrack:tracking      │                        │                        │
     │  {order_id, status, msg}  │                        │                        │
     │ ─────────────────────────►│                        │                        │
     │                           │  SUBSCRIBE             │                        │
     │                           │  swifttrack:tracking    │                        │
     │                           │ ──────────────────────►│                        │
     │                           │                        │  Route to correct user │
     │                           │                        │  (by client_id / DRV-) │
     │                           │                        │                        │
     │                           │                        │  ws.send_json(event)   │
     │                           │                        │ ──────────────────────►│
     │                           │                        │                        │ Update UI
     │                           │                        │                        │ Show toast
     │                           │                        │                        │ Flash row
```

**Event routing logic in the Gateway:**
1. Look up the `order_id` in Redis to find the `client_id`
2. Send the event to the client who owns the order
3. Send to all connected drivers (prefix `DRV-`)
4. Send to all connected admins (prefix `ADM-`)

**WebSocket connection lifecycle:**
1. Client connects to `ws://host:8000/ws/tracking?token=<JWT>`
2. Gateway validates the JWT token
3. Connection is registered in `ConnectionManager` under user ID
4. Gateway sends a `connected` event as confirmation
5. Client sends `ping` messages every 25 seconds to keep the connection alive
6. On disconnect, the connection is removed from the manager
7. Auto-reconnect from the frontend after 3 seconds

### 4.5 Client Portal UI

The Client Portal is a **single-page application** (SPA) served from `gateway/static/index.html`:

**Features:**
- **Login screen** with pre-filled demo credentials
- **Dashboard** with real-time statistics (Total Orders, Processing, Delivered, Failed) that pulse-animate on changes
- **Order History tab** – Table of all orders with status badges, row-flash animation on real-time updates
- **New Order tab** – Form to submit orders (synchronous or async high-volume mode)
- **Live Tracking tab** – Real-time event feed with colour-coded entries
- **WebSocket status indicator** (green dot when connected)
- **Toast notifications** for order status changes (accepted, in warehouse, delivered, failed)

### 4.6 Driver Mobile App UI

The Driver App is served from `gateway/static/driver.html`, designed as a **mobile-optimised web app** (max-width 480px):

**Features:**
- **Login** restricted to driver-role users only
- **Summary cards** showing total stops, delivered, and pending counts with pulse animations
- **Delivery Manifest** – List of assigned delivery stops with recipient name, address, and status
- **Delivery actions** – "Mark Delivered" and "Mark Failed" buttons on each stop card
- **Real-time notifications panel** – Live event feed from WebSocket
- **WebSocket integration** for instant manifest updates when new orders are assigned or status changes

---

## 5. Information Security Considerations

The following security measures have been designed and implemented in the SwiftTrack platform:

### 5.1 Authentication – JWT (JSON Web Tokens)

- All API endpoints (except `/api/v1/auth/login` and health checks) require a valid JWT Bearer token.
- Tokens contain claims: `sub` (user ID), `role` (client/driver/admin), `name`, `exp` (expiry), `iat` (issued at).
- Token expiration is configurable (default: 60 minutes).
- JWT secret is stored as an environment variable (`JWT_SECRET`), not hardcoded in production.

### 5.2 Authorization – Role-Based Access Control (RBAC)

| Endpoint | Client | Driver | Admin |
|----------|--------|--------|-------|
| Submit orders | Own orders only | ✗ | Any client |
| View orders | Own orders only | ✗ | All orders |
| Update delivery | ✗ | ✓ | ✓ |
| View clients list | ✗ | ✗ | ✓ |
| View manifest | ✗ | Own manifest | ✓ |

### 5.3 WebSocket Security

- WebSocket connections require a JWT token as a query parameter (`?token=<JWT>`)
- Token is validated before the WebSocket handshake is accepted
- Invalid or expired tokens result in connection closure with error code 4001

### 5.4 Input Validation and Sanitisation

- All REST endpoints use **Pydantic models** for automatic request body validation and type coercion
- The WMS TCP protocol includes a `sanitize_field()` function that strips pipe (`|`) and newline characters to prevent **protocol injection attacks**
- All SOAP calls are mediated through the Zeep library which handles XML parsing safely

### 5.5 Cross-Origin Resource Sharing (CORS)

- CORS middleware is configured on the Gateway to control which origins can make API requests
- In production, `allow_origins` should be restricted to the specific frontend domain

### 5.6 Credential Management

- RabbitMQ credentials, JWT secrets, and service configurations are managed via environment variables in a `.env` file
- The `.env` file is excluded from version control in production
- Docker environment variable injection prevents credentials from being embedded in source code

### 5.7 Network Isolation

- All services communicate over a private Docker bridge network (`swiftnet`)
- Only the Gateway (port 8000) and RabbitMQ management UI (port 15672) need to be exposed externally in production
- Inter-service communication is internal and not accessible from outside the Docker network

### 5.8 Error Handling and Information Leakage Prevention

- External-facing error messages are generic (e.g., "CMS communication error")
- Detailed error information is logged server-side only
- HTTP 502 (Bad Gateway) is returned for downstream failures rather than exposing internal stack traces

### 5.9 Additional Production Recommendations

- **HTTPS/TLS** – All external communication should be encrypted via TLS (an NGINX reverse proxy or cloud load balancer in front of the Gateway)
- **Rate Limiting** – Implement token-bucket rate limiting at the Gateway to prevent brute-force attacks and DDoS
- **Secret Rotation** – JWT secrets and RabbitMQ credentials should be rotated periodically using a secrets manager (e.g., HashiCorp Vault)
- **Audit Logging** – Log all authentication attempts and order submissions for compliance and forensic analysis
- **Container Scanning** – Docker images should be scanned for known vulnerabilities before deployment

---

## 6. Deployment and Running the Prototype

### Prerequisites

- **Docker Desktop** installed and running
- No other services occupying ports 5672, 6379, 8000–8005, 9000, 15672

### Quick Start

```bash
cd swifttrack
docker compose up --build
```

This builds and starts all 9 services. Startup order is managed by health checks and `depends_on` conditions.

### Access Points

| URL | Description |
|-----|-------------|
| http://localhost:8000 | Client Portal (Web UI) |
| http://localhost:8000/driver | Driver App (Mobile Web UI) |
| http://localhost:8000/docs | API Documentation (Swagger/OpenAPI) |
| http://localhost:15672 | RabbitMQ Management Console |

### Demo Credentials

| Username | Password | Role | User ID |
|----------|----------|------|---------|
| megamart | pass123 | Client | CLI001 |
| shopeasy | pass123 | Client | CLI002 |
| freshgrocer | pass123 | Client | CLI003 |
| driver1 | pass123 | Driver | DRV-001 |
| driver2 | pass123 | Driver | DRV-002 |
| admin | admin123 | Admin | ADM-001 |

### Testing the Order Flow

1. Open http://localhost:8000 and log in as `megamart` / `pass123`
2. Navigate to **New Order** tab
3. Fill in recipient details and click **Submit Order**
4. Observe real-time updates in the **Live Tracking** tab and toast notifications
5. Open http://localhost:8000/driver in another tab, log in as `driver1` / `pass123`
6. See the order appear in the driver's delivery manifest
7. Click **Delivered** on the stop card
8. Return to the client portal and see the order status update to DELIVERED in real-time

### Stopping the Platform

```bash
docker compose down
```

---

*This document describes the SwiftTrack middleware architecture solution for Assignment 4 – Middleware Architecture for "SwiftLogistics" (SCS2314).*

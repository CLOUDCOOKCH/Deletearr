# ADR 0001 — A single-process modular monolith

Status: accepted.

We compared Go, Python/FastAPI and Node for a low-volume, self-hosted retention service. Go offers the smallest baseline runtime and a single binary; Python provides mature HTTP clients, Argon2, typed validation and straightforward SQLite/descriptor-relative filesystem APIs. Node offers shared frontend/backend types but less direct access to Python's established Linux file-descriptor operations. Runtime memory still needs measurement on target NAS hardware; no numerical performance claim is made.

Choose Python 3.12, FastAPI/Pydantic, httpx, SQLite and React/TypeScript with Vite. React's build is static; there is no Node process in the runtime. Use Manrope served locally, Lucide icons and owned CSS tokens rather than adding a broad component framework. Requirements and npm lockfiles pin dependencies. The contributor environment includes tests, linting and audits; the runtime image includes only the runtime closure.

One Uvicorn worker owns a durable SQLite job worker. Short transactions use BEGIN IMMEDIATE, a shared process lock, WAL, FULL synchronous writes, foreign keys and a busy timeout. A configuration-directory flock prevents multiple job workers. The scheduler and executor do not depend on a broker.

Consequences: avoid multiple Uvicorn workers/replicas; a long sync queues background work behind it. API reads and settings remain available while asynchronous HTTP awaits. CPU-heavy evaluation runs in a worker thread when dispatched by the scheduler. Very large libraries need further throughput and memory profiling. SQLite `/config` belongs on reliable local storage, not SMB/NFS.

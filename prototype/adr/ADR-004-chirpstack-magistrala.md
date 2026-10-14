# ADR-004 — ChirpStack + Magistrala instead of The Things Stack + FastAPI

| | |
|---|---|
| **Status** | Proposed, and **split**: adopt one half, defer the other. Design only, no code. |
| **Date** | 2026-10-14 |
| **Branch** | `design/chirpstack-magistrala` |
| **Unlocks** | No new buyer route. **Same radio, same device.** |
| **Tests** | Whether the server side is portable, mirroring what [ADR-003](ADR-003-wifi-esp-idf.md) tests on the device side |

## Context

[ADR-003](ADR-003-wifi-esp-idf.md) tests whether the firmware survives an RTOS change. This branch
asks the same question of the server: is the backend coupled to The Things Stack and to FastAPI, or
only to the 8 bytes?

There is a cheap reason to care. `main` self-hosts TTS on an ~\$18/month VPS with Postgres and
Redis. ChirpStack v4 is a single Rust binary and, with `chirpstack-pg-to-sqlite`, runs the whole
network server on SQLite — **~\$6/month**, no Postgres to back up. At pre-seed, removing a stateful
service you must not lose is worth more than the \$12.

There is also a security reason. A carrier security review asks for mutual TLS, role-based access
control, audit logs and multi-tenancy. `main`'s FastAPI backend has none of those; Magistrala
(Apache-2.0) has all of them.

## Decision — split

**Adopt the ChirpStack swap. Defer the Magistrala swap.** The reasoning for the split is the
substance of this ADR.

### Part A — TTS → ChirpStack: adopt, and it is nearly free

The migration is thin, and the reason is a rule already in the contract: §4 requires the backend to
**decode `frm_payload` server-side and never trust a caller-supplied `decoded_payload`**, because a
webhook is untrusted input. That rule was written for security. Its side effect is portability — the
backend consumes raw bytes plus a thin metadata wrapper, so changing LNS changes one adapter.

| Path | Change | Effort **[E]** |
|---|---|---|
| `contracts/INTERFACES.md` | Add **§4d**, the ChirpStack webhook envelope, beside §4's TTS shape. Different JSON, identical 8 bytes. | 1 day |
| `backend/app/ingest.py` | One envelope adapter, selected by a header or route. `(dev_eui, f_cnt)` idempotency is unchanged — ChirpStack supplies `fCnt` too. | **2–3 days** |
| `backend/app/codec.py` | **Unchanged** | — |
| `firmware/`, `codec/`, `detection/`, `simulator/` | **All unchanged.** The device cannot tell which network server is listening. | — |
| `docker-compose.yml` | Swap the commented TTS/Postgres/Redis block for `chirpstack` + `chirpstack-gateway-bridge` + Mosquitto | 1 day |

**Total: under a week.** And it comes with tooling `main` lacks:

- **`chirpstack-fuota-server` (MIT)** — multicast firmware update over LoRaWAN. `main` currently
  calls FUOTA a trap, correctly, because *unicast* updates at DR0 blow the airtime budget. Multicast
  is the answer when you must update 10,000 nodes, and this is the only open implementation.
- **`chirpstack-packet-multiplexer` (MIT)** — forwards the same gateway uplinks to two network
  servers at once. That is the zero-downtime migration path, and it also lets a pilot run against
  TTN's free community network *and* your own LNS simultaneously.

### Part B — FastAPI → Magistrala: defer, and here is why

Magistrala is genuinely the right answer eventually. It is Apache-2.0 and ships **mTLS, ABAC + RBAC,
audit logs, multi-tenant workspaces, OpenTelemetry, and MQTT/HTTP/WS/CoAP with unified auth** —
close to a line-by-line match for a carrier security questionnaire.

But it is **not a swap, it is an addition**, and conflating the two is the mistake this ADR exists
to prevent. Magistrala replaces the *plumbing*: device registry, auth, transport, tenancy. It does
not replace the *product*:

| `main` component | Magistrala equivalent |
|---|---|
| device registry, auth, transport, tenancy | **Yes — replaced, and better** |
| `app/ingest.py` payload decode | No. Still yours. |
| `app/detector_bridge.py` | No. Still yours. |
| `app/alerts.py` lifecycle + shadow mode | No. Shadow mode is a product rule, not a platform feature. |
| `app/valve.py` arming and refusal | No. **And this is the one that matters** — fail-as-is, the 409 on an unarmed device, and "unconfirmed never silently success" are safety invariants that must stay in code you own and test. |
| `app/insurer.py` portfolio report | No. Still yours. |

So adopting Magistrala now buys authentication features **nobody has asked for yet** at the cost of
a platform to operate, while leaving every line of the actual product exactly where it is. The
roadmap puts the carrier security review and SOC 2 in P1–P3; this belongs there, triggered by a
real questionnaire, not now.

**Defer, with a named trigger:** adopt Magistrala when a buyer's security review first demands mTLS
or RBAC — not before.

## What breaks

**1. ChirpStack is MIT, not Apache-2.0.** Verified in `../../data/repos.csv`. MIT is permissive and
entirely safe, but it is *silent* on patents where Apache-2.0 §3 grants them explicitly. If the
stated reason for preferring Apache-2.0 was the patent grant, this is a small step down. Ranking:
Apache-2.0 > MIT/BSD-3 > BSD-3-Clear.

**2. Key-person risk, and it is the real argument against Part A.** ChirpStack's forum `/about`
renders **all zeros** — no disclosed member or post counts — and the project is essentially
single-maintainer. TTS is backed by The Things Industries with commercial plans (Discovery free,
**Standard €190/month [V]**) and a quarterly release cadence. Against that: ChirpStack is embedded
in shipping third-party products (Dragino's LPS8v2 ships it on-gateway), which is its own kind of
durability. Mitigation is cheap and worth taking: keep the §4 TTS adapter alive alongside §4d, so
reverting is an hour rather than a project.

**3. You give up the in-tree Join Server.** TTS bundles Network, Application **and Join** servers,
so you hold your own AppKeys and NwkKeys rather than renting them — a clean answer to "where does
your root key material live?" in a security review. ChirpStack can be paired with a separate join
server (`brocaar/simple-join-server`, Rust, by ChirpStack's own author) and arguably splitting key
custody from the network server is the *better* architecture. But it is now your component to run,
and losing that key material is an extinction event.

## Cost

| Line | Delta vs `main` |
|---|---|
| Hosting | **−\$12/month** (SQLite single binary replaces TTS + Postgres + Redis) |
| Engineering, Part A | **+under 1 person-week [E]** |
| Engineering, Part B | **+3–5 person-weeks [E]** — **deferred** |
| Gains | multicast FUOTA, dual-forward migration, one fewer stateful service |
| Loses | in-tree Join Server, a commercial support option, the Apache-2.0 patent grant |
| Hardware, certification, recurring | **\$0 — nothing on the device changes** |

## When to pick this

**Part A:** when FUOTA becomes necessary — i.e. the first time a fleet over a few hundred nodes
needs a firmware update — or opportunistically, since it is under a week and reduces the ops
surface. It has no device impact and no certification impact, so it carries almost no risk.

**Part B:** when a buyer's security review demands mTLS, RBAC or audit logs. Not before.

## The result worth recording

The server side is portable, and the device side is portable
([ADR-003](ADR-003-wifi-esp-idf.md)). In all four branches on this repo, **`detection/` is
unchanged** — 130 tests and a measured 0.3383 nuisance alerts per device-year carry across every
radio and every stack.

That is the one architectural property worth defending. It holds because the detector is stdlib-only
pure functions over a `Sample` struct and has no idea what delivered the bytes. **If a future
migration plan ever requires touching `detection/`, that is the signal something has been coupled
that should not have been** — and it should be treated as a defect in the new design, not an
unavoidable cost of the migration.

## Rejected within this branch

- **ThingsBoard.** Disqualified outright: **BUSL-1.1**, free commercial production use capped at
  **100 devices and one server**. Last Apache-2.0 release was v3.9.1. It is still the top search
  result for "open source IoT platform", which is exactly why it is named here.
- **EMQX** as the broker. Also **BUSL-1.1** since v5.9.0, and clustering requires a paid licence
  file, so the high-availability story dies with a single node. `main` already uses VerneMQ
  (Apache-2.0) for this reason.
- **OpenRemote.** **AGPL-3.0** — §13 network copyleft reaches a carrier-facing portal.
- **Eclipse Hono / Ditto.** EPL-2.0 is survivable server-side, but AMQP/Qpid and five
  microservices plus MongoDB is far too much operations for a three-person team.
- **Keeping TTS and adopting only the ChirpStack components.** Actually viable and worth noting:
  `chirpstack-fuota-server` and `chirpstack-packet-multiplexer` are MIT and work against a gateway
  regardless of which LNS owns the session. If Part A's key-person risk is judged unacceptable,
  take the tooling without the server.

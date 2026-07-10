"""
Cross-component envelope (v2) — Python mirror.

Mirrors `matrx-frontend/lib/types/bridge-envelope.ts` CrossComponentEnvelope
byte-for-byte in field names, types, and defaults. Other components mirror
the same shape in their own runtimes (matrx-extend TS, aidream Python).

v2 extends the existing v1 BridgeEnvelope (used today between the Chrome
extension and the Next.js frontend) with:
  - `kind` discriminator: "rpc" | "wake" | "presence"
  - `fromInstance` / `toInstance` instance refs for disambiguation

Back-compat: v1 publishers don't set these fields. Defaults below keep
them working unchanged.

`kind:"task"` is intentionally absent. Cross-component tasks live in the
`sch_task` table (matrx-scheduler), not in bus envelopes. Wake envelopes
carry `payload: {"taskId": "..."}` pointing at the durable row.

`parse_envelope` is wired into the `_on_broadcast` handler in
`extension_broadcast.py`; `kind:"rpc"` envelopes are dispatched into the
extension command registry by `cross_component_router.py` and answered
with a reply envelope (`action: "<action>.result"`, same `requestId`).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class InstanceRef(BaseModel):
    """Identifies a specific running instance of a component."""

    component: str
    instanceId: str


class PartialInstanceRef(BaseModel):
    """Like `InstanceRef`, but with `instanceId` optional.

    Used for `toInstance` so a publisher can target a component class
    ("any matrx-local instance") without naming a specific instance —
    matches the TS schema's `InstanceRefSchema.partial({ instanceId: true })`.
    """

    component: str
    instanceId: Optional[str] = None


class CrossComponentEnvelope(BaseModel):
    """The v2 cross-component envelope.

    Carried over Supabase Broadcast on the per-user buses
    (`matrx-extension-bridge:<userId>`, `matrx-local-bridge:<userId>`,
    `matrx-server-bus:<userId>`). v1 publishers parse cleanly: missing
    v2 fields default appropriately.
    """

    # Envelope schema version. v1 publishers omit it (and every v2 field);
    # v2 publishers stamp it explicitly. Bump ONLY on a breaking wire change
    # and keep parse back-compat for at least one version.
    v: int = 2
    kind: Literal["rpc", "wake", "presence"] = "rpc"
    direction: str = Field(..., min_length=1)
    action: str
    requestId: str
    payload: Optional[Any] = None
    timestamp: int
    fromInstance: InstanceRef = Field(
        default_factory=lambda: InstanceRef(component="unknown", instanceId="unknown")
    )
    toInstance: Optional[PartialInstanceRef] = None


def parse_envelope(raw: dict) -> CrossComponentEnvelope:
    """Parse a v1 or v2 envelope dict into a typed CrossComponentEnvelope.

    Raises pydantic.ValidationError on missing required fields
    (`direction`, `action`, `requestId`, `timestamp`).
    """
    return CrossComponentEnvelope.model_validate(raw)

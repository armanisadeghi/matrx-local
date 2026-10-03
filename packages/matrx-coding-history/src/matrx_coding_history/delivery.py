"""Deliver import envelopes straight to the server, proving every acknowledgement.

For an app with no durable outbox (Matrx 2's explicit "Import missing history"): each envelope
is POSTed in order and its answer must pass the same receipt check Matrx Local's outbox applies
before it forgets a row. A 2xx that does not account for every entry is an error, never a success.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from matrx_coding_history.bridge import BridgeRequest
from matrx_coding_history.envelopes import BRIDGE_PATH, validate_acknowledgement
from matrx_coding_history.ports import CloudError, CloudHttp


class DirectBridgeDelivery:
    """A ``BridgeSink`` whose "enqueue" is the delivery itself."""

    def __init__(self, http: CloudHttp, *, timeout: float = 30.0) -> None:
        self._http = http
        self._timeout = timeout

    async def enqueue_many(
        self,
        requests: Sequence[BridgeRequest],
        *,
        enqueue_origin: str,
    ) -> dict[str, Any]:
        duplicates: list[bool] = []
        for request in requests:
            # The exact body Matrx Local's outbox persists and sends (``_canonical_envelope``).
            payload = request.model_dump(mode="json", exclude_none=True)
            response = await self._http.post_json(BRIDGE_PATH, payload, timeout=self._timeout)

            def _error(status: int, message: str, answer: Any = response) -> Exception:
                return CloudError(status, message, body=answer)

            validate_acknowledgement(response, request, error=_error)
            accepted = response.get("accepted", 0) if isinstance(response, dict) else 0
            repeated = response.get("duplicates", 0) if isinstance(response, dict) else 0
            duplicates.append(accepted == 0 and isinstance(repeated, int) and repeated > 0)
        return {
            "duplicates_by_index": duplicates,
            "pending": 0,
            "enqueue_origin": enqueue_origin,
        }


__all__ = ["DirectBridgeDelivery"]

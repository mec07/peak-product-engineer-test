from fastapi import APIRouter, Depends

from ..auth import current_user
from ..models import (
    BiomarkerHistory,
    BiomarkerResult,
    Envelope,
    HistoryPayload,
    HistoryPoint,
    Member,
    RangePeriod,
    ResultsDocument,
)
from ..store import Store, get_store

router = APIRouter(prefix="/api")

HISTORY_LIMIT = 30


def biomarker_history(
    store: Store, user_id: str, limit: int = HISTORY_LIMIT
) -> tuple[list[BiomarkerHistory], int, bool]:
    """Every marker's history across the member's latest `limit` draws.

    Each draw document holds all the markers measured that day, so one marker's
    history means reading every draw. We read them once and split by marker.
    `keys()` finds the window without decoding anything; the range query then
    decodes at most `limit` documents however long the history gets.

    Returns (histories, draws read, whether older draws were left out).
    """
    partition = f"results:{user_id}"
    sort_keys = list(store.keys(partition))
    if not sort_keys:
        return [], 0, False

    window = sort_keys[-limit:]
    by_marker: dict[str, list[tuple[str, BiomarkerResult]]] = {}
    for _sk, draw in store.query(partition, ResultsDocument, start=window[0]):
        for result in draw.results:
            by_marker.setdefault(result.biomarker_id, []).append(
                (draw.tested_at, result)
            )

    histories = [
        marker_history(marker_id, entries)
        for marker_id, entries in sorted(by_marker.items())
    ]
    return histories, len(window), len(sort_keys) > limit


def marker_history(
    marker_id: str, entries: list[tuple[str, BiomarkerResult]]
) -> BiomarkerHistory:
    """Points oldest first, in the latest unit, with ranges as change periods."""
    unit = entries[-1][1].unit
    points: list[HistoryPoint] = []
    periods: list[RangePeriod] = []
    for tested_at, result in entries:
        # No conversion: a point in another unit would sit on the wrong scale.
        if result.unit != unit:
            continue
        points.append(
            HistoryPoint(tested_at=tested_at, value=result.value, status=result.status)
        )
        if not periods or periods[-1].ranges != result.ranges:
            periods.append(RangePeriod(from_tested_at=tested_at, ranges=result.ranges))
    return BiomarkerHistory(
        biomarker_id=marker_id, unit=unit, points=points, range_periods=periods
    )


@router.get("/biomarkers/history", response_model=Envelope[HistoryPayload])
def history(member: Member = Depends(current_user), store: Store = Depends(get_store)):
    """Biomarker history for the signed-in member. Not part of /api/home, so it
    does not count against the home budget; the client fetches it on demand."""
    histories, draws, truncated = biomarker_history(store, member.id)
    return Envelope(
        data=HistoryPayload(biomarkers=histories),
        meta={"draws": draws, "truncated": truncated},
    )

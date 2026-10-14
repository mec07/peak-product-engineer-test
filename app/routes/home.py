from datetime import date, timedelta

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..models import (
    BiomarkerDefinition,
    BiomarkerListPayload,
    BiomarkerView,
    DailyPoint,
    Envelope,
    HomePayload,
    Member,
    PreviousResult,
    ResultsDocument,
    Trend,
    WearableDayDocument,
    WearableHistory,
    WearableSeries,
)
from ..store import Store, get_store
from ..wearables import LABELS, METRICS, SUPPORTED
from .history import HISTORY_LIMIT

router = APIRouter(prefix="/api")


DEFINITIONS = "biomarker"


def definitions(store: Store) -> dict[str, BiomarkerDefinition]:
    return {
        marker_id: definition
        for marker_id, definition in store.query(DEFINITIONS, BiomarkerDefinition)
    }


RANK = {"improve": 0, "good": 1, "optimal": 2}

# The latest draw plus the five before it: enough to find every previous result
# in the fixtures, where one is at most 5 draws back.
RECENT_DRAWS = 6


def trend(previous: str, latest: str) -> Trend | None:
    """Band movement between two statuses; None if either is unknown."""
    if previous not in RANK or latest not in RANK:
        return None
    change = RANK[latest] - RANK[previous]
    return "up" if change > 0 else "down" if change < 0 else "same"


def previous_results(
    earlier: list[ResultsDocument],
) -> dict[tuple[str, str], PreviousResult]:
    """Each (marker, unit)'s most recent result among the earlier draws.

    Keyed by unit as well as marker, the same guard the History chart uses, so a
    marker's previous result is always the chart's second-to-last dot. Draws are
    partial, so that dot can be several draws back.
    """
    found: dict[tuple[str, str], PreviousResult] = {}
    for draw in reversed(earlier):
        for result in draw.results:
            key = (result.biomarker_id, result.unit)
            if key not in found:
                found[key] = PreviousResult(
                    status=result.status, tested_at=draw.tested_at
                )
    return found


def latest_results(
    store: Store, user_id: str
) -> tuple[list[BiomarkerView], str | None]:
    """Every biomarker with this member's most recent value and its trend.

    One partition per member, one document per draw, sorted by ISO timestamp. So
    the last sort key in the partition is the latest draw. Previous results come
    from the same window the History chart plots (the latest HISTORY_LIMIT
    draws). Decoding the whole window costs ~10 ms at 30 draws, so the recent
    draws are read first and the rest of the window only when some marker's
    previous result is still missing: one query usually, two at most.
    """
    partition = f"results:{user_id}"
    sort_keys = list(store.keys(partition))
    if not sort_keys:
        return [], None

    window = sort_keys[-HISTORY_LIMIT:]
    recent, older = window[-RECENT_DRAWS:], window[:-RECENT_DRAWS]
    draws = [
        draw for _sk, draw in store.query(partition, ResultsDocument, start=recent[0])
    ]
    if not draws:
        return [], None
    draw = draws[-1]
    earlier = previous_results(draws[:-1])

    wanted = {(result.biomarker_id, result.unit) for result in draw.results}
    if older and not wanted <= earlier.keys():
        rest = [
            d
            for _sk, d in store.query(
                partition, ResultsDocument, start=older[0], end=recent[0]
            )
        ]
        for key, before in previous_results(rest).items():
            earlier.setdefault(key, before)

    defs = definitions(store)

    views = []
    for result in draw.results:
        definition = defs.get(result.biomarker_id)
        before = earlier.get((result.biomarker_id, result.unit))
        change = trend(before.status, result.status) if before else None
        views.append(
            BiomarkerView(
                biomarker_id=result.biomarker_id,
                name=definition.name if definition else result.biomarker_id,
                category=definition.category if definition else "other",
                description=definition.description if definition else "",
                direction=definition.direction if definition else "in_range",
                value=result.value,
                unit=result.unit,
                tested_at=draw.tested_at,
                status=result.status,
                ranges=result.ranges,
                trend=change,
                previous=before if change else None,
            )
        )

    views.sort(key=lambda v: (v.category, v.name))
    return views, draw.tested_at


WEARABLE_DAYS = 30


def wearable_history(store: Store, user_id: str) -> WearableHistory | None:
    """The member's latest 30 calendar days of daily metrics, gaps as None.

    The window ends on the member's latest stored day, not today. keys() finds
    that day without decoding anything, then one range query reads at most 30
    small summaries. Raw samples were never stored, so nothing here is large.
    """
    partition = f"wearables:{user_id}"
    days = list(store.keys(partition))
    if not days:
        return None

    end = date.fromisoformat(days[-1])
    start = end - timedelta(days=WEARABLE_DAYS - 1)
    stored = dict(store.query(partition, WearableDayDocument, start=start.isoformat()))
    calendar = [(start + timedelta(days=i)).isoformat() for i in range(WEARABLE_DAYS)]
    provider = stored[days[-1]].provider

    series = []
    for metric in METRICS:
        label, unit = LABELS[metric]
        series.append(
            WearableSeries(
                metric=metric,
                label=label,
                unit=unit,
                supported=metric in SUPPORTED.get(provider, set()),
                points=[
                    DailyPoint(
                        date=day,
                        value=getattr(stored[day], metric) if day in stored else None,
                    )
                    for day in calendar
                ],
            )
        )
    return WearableHistory(
        provider=provider, start=calendar[0], end=calendar[-1], series=series
    )


@router.get("/biomarkers", response_model=Envelope[BiomarkerListPayload])
def list_biomarkers(
    member: Member = Depends(current_user), store: Store = Depends(get_store)
):
    results, _tested_at = latest_results(store, member.id)
    return Envelope(
        data=BiomarkerListPayload(results=results),
        meta={
            "count": len(results),
            "categories": sorted({r.category for r in results}),
        },
    )


@router.get("/home", response_model=Envelope[HomePayload])
def home(member: Member = Depends(current_user), store: Store = Depends(get_store)):
    """Everything the member's home screen needs, in one request.

    Budget: defined and measured by scripts/bench.py.
    Whatever you add to this screen, it has to still pass when you are done.
    """
    results, tested_at = latest_results(store, member.id)

    return Envelope(
        data=HomePayload(results=results, wearables=wearable_history(store, member.id)),
        meta={
            "count": len(results),
            "categories": sorted({r.category for r in results}),
            "tested_at": tested_at,
        },
    )

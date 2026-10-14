from datetime import date
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- envelope


T = TypeVar("T")


class Envelope[T](BaseModel):
    """Every endpoint returns {"data": ..., "meta": ...}."""

    data: T
    meta: dict = Field(default_factory=dict)


# ------------------------------------------------------- stored documents


class Member(BaseModel):
    """pk="user", sk=id"""

    id: str
    email: str
    name: str
    sex: str
    birth_date: str
    created_at: str


class EmailPointer(BaseModel):
    """pk="email", sk=email: a pointer partition."""

    user_id: str


class Band(BaseModel):
    min: float
    max: float


class Ranges(BaseModel):
    optimal: Band
    good: Band
    improve: Band


class BiomarkerDefinition(BaseModel):
    """pk="biomarker", sk=biomarker_id"""

    id: str
    name: str
    category: str
    unit: str
    description: str
    direction: str
    ranges: Ranges


class BiomarkerResult(BaseModel):
    biomarker_id: str
    value: float
    unit: str
    status: str
    ranges: Ranges
    source: str | None = None


class ResultsDocument(BaseModel):
    """pk=f"results:{id}", sk=tested_at"""

    user_id: str
    tested_at: str
    results: list[BiomarkerResult]


class WearableDayDocument(BaseModel):
    """pk=f"wearables:{user_id}", sk=date (YYYY-MM-DD)

    The normalised summary of one provider day. Raw samples are not kept.
    """

    user_id: str
    date: str
    provider: str
    upload_id: str
    received_at: str
    resting_hr_bpm: float | None = None
    steps: int | None = None
    sleep_efficiency_pct: float | None = None
    superseded_upload_ids: list[str] = Field(default_factory=list)


# -------------------------------------------------------- response & resquest models


class MemberView(BaseModel):
    id: str
    email: str
    name: str
    sex: str
    birth_date: str


Trend = Literal["up", "down", "same"]


class PreviousResult(BaseModel):
    """The marker's previous result: the History chart's second-to-last dot."""

    status: str
    tested_at: str


class BiomarkerView(BaseModel):
    biomarker_id: str
    name: str
    category: str
    description: str
    direction: str
    value: float
    unit: str
    tested_at: str
    status: str
    ranges: Ranges
    trend: Trend | None = None
    previous: PreviousResult | None = None


class MemberPayload(BaseModel):
    user: MemberView


class BiomarkerListPayload(BaseModel):
    results: list[BiomarkerView]


class OkPayload(BaseModel):
    ok: bool


class DailyPoint(BaseModel):
    date: str
    value: float | None


class WearableSeries(BaseModel):
    metric: str
    label: str
    unit: str
    supported: bool
    points: list[DailyPoint]


class WearableHistory(BaseModel):
    provider: str
    start: str
    end: str
    series: list[WearableSeries]


class HomePayload(BaseModel):
    results: list[BiomarkerView]
    wearables: WearableHistory | None = None


class HistoryPoint(BaseModel):
    tested_at: str
    value: float
    status: str


class RangePeriod(BaseModel):
    """Ranges that apply from `from_tested_at` until the next period starts."""

    from_tested_at: str
    ranges: Ranges


class BiomarkerHistory(BaseModel):
    biomarker_id: str
    unit: str
    points: list[HistoryPoint]
    range_periods: list[RangePeriod]


class HistoryPayload(BaseModel):
    biomarkers: list[BiomarkerHistory]


class ReadingBody(BaseModel):
    biomarker_id: str
    value: float
    unit: str
    tested_at: str


class ReadingPayload(BaseModel):
    biomarker_id: str
    tested_at: str
    status: str
    created_draw: bool


class DrawReading(BaseModel):
    biomarker_id: str
    value: float
    unit: str


class DrawBody(BaseModel):
    tested_at: str
    results: list[DrawReading]


class DrawResultPayload(BaseModel):
    biomarker_id: str
    status: str


class DrawPayload(BaseModel):
    tested_at: str
    created_draw: bool
    accepted: list[DrawResultPayload]
    results_in_draw: int


class WearableMember(BaseModel):
    reference_id: str
    email: str


class WearableDay(BaseModel):
    model_config = {"extra": "allow"}

    calendar_date: date
    upload_id: str


class WearableBody(BaseModel):
    provider: str
    member: WearableMember
    data: WearableDay


class WearablePayload(BaseModel):
    stored: bool
    date: str
    replaced_upload_id: str | None = None

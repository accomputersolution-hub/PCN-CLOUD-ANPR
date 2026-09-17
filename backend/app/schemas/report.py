from datetime import date

from pydantic import BaseModel


class ReportRow(BaseModel):
    label: str
    entries: int
    exits: int
    detections: int


class ReportResponse(BaseModel):
    kind: str
    from_date: date
    to_date: date
    rows: list[ReportRow]

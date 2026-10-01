"""Persist whether a provider delivered data, an empty report, or failed."""

from datetime import datetime, timezone

from app import db
from app.models import DataFetchReceipt


def record_fetch(feed: str, status: str, row_count: int = 0, *,
                 data_through=None, source_version=None, detail=None) -> DataFetchReceipt:
    receipt = DataFetchReceipt(
        feed=feed,
        status=status,
        fetched_at=datetime.now(timezone.utc),
        data_through=data_through,
        source_version=source_version,
        row_count=row_count,
        detail=(detail or '')[:300],
    )
    db.session.add(receipt)
    return receipt


def latest_fetch(feed: str) -> DataFetchReceipt | None:
    return (DataFetchReceipt.query.filter_by(feed=feed)
            .order_by(DataFetchReceipt.fetched_at.desc(), DataFetchReceipt.id.desc())
            .first())

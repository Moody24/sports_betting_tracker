"""Atomic, user-scoped retry receipts for local bet records."""

import hashlib
import json

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import BetRecordingRequest


class RecordingConflict(ValueError):
    code = 'request_conflict'


def begin_recording(user_id, request_id, payload):
    """Reserve a key in the current transaction or return its committed result."""
    if not request_id:
        return None, None
    encoded = json.dumps({k: v for k, v in payload.items() if k != 'request_id'},
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    identity = (user_id, request_id)
    receipt = db.session.get(BetRecordingRequest, identity)
    if receipt is None:
        try:
            receipt = BetRecordingRequest(user_id=user_id, request_id=request_id,
                                          payload_hash=digest, request_json=encoded)
            db.session.add(receipt)
            db.session.flush()
            return receipt, None
        except IntegrityError:
            db.session.rollback()
            # A concurrent winner commits both the bet rows and response before
            # its unique-key reservation becomes visible to this transaction.
            receipt = db.session.get(BetRecordingRequest, identity, populate_existing=True)
    if receipt is None or receipt.payload_hash != digest:
        raise RecordingConflict('This request ID was already used for a different record.')
    if not receipt.response_json:
        raise RecordingConflict('This record is still being saved. Retry the same request.')
    return receipt, json.loads(receipt.response_json)


def finish_recording(receipt, response):
    if receipt is not None:
        receipt.response_json = json.dumps(response, sort_keys=True, separators=(',', ':'))

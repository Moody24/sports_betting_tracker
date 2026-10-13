"""Authenticated delivery of the durable free basketball Games feed."""

import logging

from flask import Response, current_app, jsonify, request
from flask_login import login_required

from app import db
from app.services.basketball_feed import get_feed_snapshot
from app.services.feed_stream import StreamCapacityError, get_feed_hub

logger = logging.getLogger(__name__)


def _unavailable(code, message, status):
    response = jsonify(ok=False, data=None, error={'code': code, 'message': message, 'details': {}}, meta={})
    response.status_code = status
    response.headers['Retry-After'] = '15'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@login_required
def nba_live_feed():
    try:
        snapshot = get_feed_snapshot()
    except Exception as exc:
        logger.warning('Basketball snapshot unavailable (%s)', type(exc).__name__)
        return _unavailable('feed_unavailable', 'Scores are temporarily unavailable. Please retry.', 503)
    response = jsonify(ok=True, data=snapshot, error=None, meta={})
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@login_required
def nba_live_events():
    after_revision = request.headers.get('Last-Event-ID', request.args.get('last_event_id'))
    hub = get_feed_hub(current_app._get_current_object())
    # Flask-Login's user query must not reserve a pooled DB connection for the
    # lifetime of this response. The iterator uses no request/session objects.
    db.session.remove()
    try:
        subscriber = hub.subscribe(after_revision)
    except StreamCapacityError:
        return _unavailable('stream_capacity', 'Live updates are busy. Please retry.', 429)
    response = Response(subscriber.iter_sse(), mimetype='text/event-stream', headers={
        'Cache-Control': 'private, no-store',
        'X-Accel-Buffering': 'no',
    })
    response.call_on_close(subscriber.close)
    return response

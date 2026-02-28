"""
time_utils.py
-------------
Centralised time helpers for the JQC application.

All timestamps are stored as Eastern Time (America/New_York) so that
displayed dates reflect the local business timezone without any
conversion layer in templates or reports.
"""

from datetime import datetime
import pytz

EASTERN = pytz.timezone('America/New_York')


def now_eastern() -> datetime:
    """Return the current wall-clock time in US/Eastern (naive datetime).

    Stored as a naive datetime in the database so that existing DateTime
    columns require no schema change.  The value is always Eastern local
    time (auto-adjusts for EDT / EST).
    """
    return datetime.now(tz=EASTERN).replace(tzinfo=None)

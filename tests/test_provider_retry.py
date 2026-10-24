from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from core.adapters.llms.provider_gate import retry_delay


def test_retry_after_formats_are_bounded_and_honored():
    assert retry_delay({"retry-after": "120"}, 90) == 120
    assert retry_delay({"retry-after-ms": "1500"}, 90) == 2
    deadline = format_datetime(datetime.now(UTC) + timedelta(seconds=120))
    assert 119 <= retry_delay({"retry-after": deadline}, 90) <= 120
    assert retry_delay({"retry-after": "not-a-date"}, 90) == 90

import random
from types import SimpleNamespace

import oci
import pytest

from oci_a1_hunter.hunter import HuntAborted, HuntStats, Outcome, classify, hunt


def service_error(status, code, message):
    return oci.exceptions.ServiceError(status, code, {}, message)


CAPACITY = service_error(500, "InternalError", "Out of host capacity.")
THROTTLED = service_error(429, "TooManyRequests", "Too many requests for the user")
LIMIT = service_error(400, "LimitExceeded", "The following service limits were exceeded")


class FakeCloud:
    def __init__(self, results, existing=None):
        self.results = list(results)
        self.calls = []
        self.existing = existing

    def launch(self, details, availability_domain):
        self.calls.append(availability_domain)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def find_instance(self, display_name):
        return self.existing


def run(cloud, ads=("AD-1",), **kw):
    sleeps = []
    instance = hunt(
        cloud,
        SimpleNamespace(),
        list(ads),
        "a1-free",
        interval=60,
        jitter=0,
        max_backoff=600,
        sleep=sleeps.append,
        rng=random.Random(0),
        **kw,
    )
    return instance, sleeps


@pytest.mark.parametrize(
    "exc, outcome",
    [
        (CAPACITY, Outcome.CAPACITY),
        (THROTTLED, Outcome.THROTTLED),
        (LIMIT, Outcome.FATAL),
        (service_error(401, "NotAuthenticated", "bad key"), Outcome.FATAL),
        (service_error(404, "NotAuthorizedOrNotFound", "nope"), Outcome.FATAL),
        (service_error(502, "BadGateway", "x"), Outcome.TRANSIENT),
        (ConnectionError("reset"), Outcome.TRANSIENT),
        (oci.exceptions.ConnectTimeout("t"), Outcome.TRANSIENT),
    ],
)
def test_classify(exc, outcome):
    assert classify(exc).outcome is outcome


def test_classify_reraises_unknown():
    with pytest.raises(ZeroDivisionError):
        classify(ZeroDivisionError())


def test_fatal_message_has_hint():
    assert "free A1 quota" in classify(LIMIT).message


def test_retries_capacity_until_success():
    ok = SimpleNamespace(id="ocid1.instance.x")
    cloud = FakeCloud([CAPACITY, CAPACITY, ok])
    instance, sleeps = run(cloud)
    assert instance is ok
    assert sleeps == [60, 60]


def test_rotates_ads_within_a_round():
    ok = SimpleNamespace(id="i")
    cloud = FakeCloud([CAPACITY, CAPACITY, CAPACITY, CAPACITY, ok])
    instance, sleeps = run(cloud, ads=("A", "B", "C"))
    assert cloud.calls == ["A", "B", "C", "A", "B"]
    # 2s gap between ADs, interval between rounds
    assert sleeps == [2, 2, 60, 2]


def test_throttle_backs_off_exponentially_and_resets():
    ok = SimpleNamespace(id="i")
    cloud = FakeCloud([THROTTLED, THROTTLED, THROTTLED, THROTTLED, CAPACITY, ok])
    _, sleeps = run(cloud)
    assert sleeps == [120, 240, 480, 600, 60]


def test_throttle_skips_remaining_ads():
    ok = SimpleNamespace(id="i")
    cloud = FakeCloud([THROTTLED, ok])
    run(cloud, ads=("A", "B"))
    assert cloud.calls == ["A", "A"]


def test_fatal_stops_immediately():
    cloud = FakeCloud([CAPACITY, LIMIT])
    with pytest.raises(HuntAborted) as e:
        run(cloud)
    assert e.value.info.outcome is Outcome.FATAL
    assert len(cloud.calls) == 2


def test_transient_error_detects_instance_that_was_created():
    existing = SimpleNamespace(id="ocid1.instance.created")
    cloud = FakeCloud([ConnectionError("reset")], existing=existing)
    instance, _ = run(cloud)
    assert instance is existing


def test_max_attempts():
    stats = HuntStats()
    cloud = FakeCloud([CAPACITY] * 10)
    with pytest.raises(HuntAborted):
        run(cloud, max_attempts=3, stats=stats)
    assert stats.attempts == 3
    assert stats.by_outcome[Outcome.CAPACITY] == 3

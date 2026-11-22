"""The retry loop and error classification. Kept free of I/O details so it is easy to test."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import oci


class Outcome(Enum):
    CAPACITY = "capacity"  # "Out of host capacity" - the normal case, keep going
    THROTTLED = "throttled"  # 429 - back off exponentially
    TRANSIENT = "transient"  # 5xx / network - retry, but check the launch did not go through
    FATAL = "fatal"  # bad config, auth, limits - retrying will never help


# OCI's (vendored) requests exceptions, ConnectionError and TimeoutError all derive from OSError.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (OSError,)

FATAL_HINTS = {
    "LimitExceeded": "Service limit reached - you probably already use your free A1 quota.",
    "QuotaExceeded": "Compartment quota exceeded.",
    "NotAuthenticated": "Check the API key / fingerprint in your OCI config.",
    "NotAuthorizedOrNotFound": "Wrong OCID, or the user lacks permissions in this compartment.",
    "InvalidParameter": "One of the launch parameters is invalid (image/shape/subnet/AD).",
}


@dataclass
class Classified:
    outcome: Outcome
    status: str
    message: str


def classify(exc: BaseException) -> Classified:
    if isinstance(exc, oci.exceptions.ServiceError):
        status, code = exc.status, exc.code or ""
        message = (exc.message or code or "").strip()
        label = f"{status} {code}".strip()
        if status == 429:
            return Classified(Outcome.THROTTLED, label, message)
        if "capacity" in message.lower():
            return Classified(Outcome.CAPACITY, label, message)
        if status >= 500 or status == 409:
            return Classified(Outcome.TRANSIENT, label, message)
        hint = FATAL_HINTS.get(code)
        return Classified(Outcome.FATAL, label, f"{message} {hint}" if hint else message)
    if isinstance(exc, NETWORK_ERRORS):
        return Classified(Outcome.TRANSIENT, "NET", f"{type(exc).__name__}: {exc}")
    raise exc


class HuntAborted(Exception):
    def __init__(self, info: Classified):
        super().__init__(f"[{info.status}] {info.message}")
        self.info = info


class LaunchTarget(Protocol):
    def launch(self, details: Any, availability_domain: str) -> Any: ...
    def find_instance(self, display_name: str) -> Any | None: ...


@dataclass
class HuntStats:
    attempts: int = 0
    started: float = field(default_factory=time.monotonic)
    by_outcome: dict[Outcome, int] = field(default_factory=lambda: dict.fromkeys(Outcome, 0))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


LogFn = Callable[[Outcome | None, str], None]


def jittered(seconds: float, jitter: float, rng: random.Random) -> float:
    return seconds * rng.uniform(1 - jitter, 1 + jitter) if jitter else seconds


def hunt(
    target: LaunchTarget,
    details: Any,
    ads: list[str],
    display_name: str,
    *,
    interval: float,
    jitter: float = 0.2,
    max_backoff: float = 600,
    ad_gap: float = 2.0,
    max_attempts: int | None = None,
    log: LogFn = lambda _o, _m: None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    stats: HuntStats | None = None,
) -> Any:
    """Try every AD once per round until one launch succeeds. Returns the instance."""
    if not ads:
        raise ValueError("No availability domains to try")
    rng = rng or random.Random()
    stats = stats or HuntStats()
    throttle_streak = 0

    while True:
        delay = interval
        for i, ad in enumerate(ads):
            if max_attempts is not None and stats.attempts >= max_attempts:
                raise HuntAborted(Classified(Outcome.FATAL, "MAX", "max attempts reached"))
            stats.attempts += 1
            ad_short = ad.split(":")[-1]
            try:
                instance = target.launch(details, ad)
            except Exception as exc:  # noqa: BLE001 - classify() re-raises unknown errors
                info = classify(exc)
                stats.by_outcome[info.outcome] += 1
                log(info.outcome, f"#{stats.attempts} {ad_short} [{info.status}] {info.message}")

                if info.outcome is Outcome.FATAL:
                    raise HuntAborted(info) from exc

                if info.outcome is Outcome.TRANSIENT:
                    # The request may have reached OCI before the connection dropped.
                    try:
                        existing = target.find_instance(display_name)
                    except Exception:  # noqa: BLE001
                        existing = None
                    if existing is not None:
                        log(None, f"Launch went through despite the error: {existing.id}")
                        return existing

                if info.outcome is Outcome.THROTTLED:
                    throttle_streak += 1
                    delay = min(interval * 2**throttle_streak, max_backoff)
                    break  # do not hammer the remaining ADs while throttled

                throttle_streak = 0
                if i < len(ads) - 1:
                    sleep(jittered(ad_gap, jitter, rng))
                continue

            log(None, f"#{stats.attempts} {ad_short} launched {instance.id}")
            return instance

        sleep(jittered(delay, jitter, rng))

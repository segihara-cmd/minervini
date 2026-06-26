"""
HTTP / 크롤링 실패 시 재시도 데코레이터.

지수 백오프(exponential backoff)로 일시적 네트워크 오류를 완화합니다.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar

from config.settings import RETRY_MAX_ATTEMPTS, RETRY_WAIT_SECONDS

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# 재시도 대상 예외 (필요 시 확장)
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def retry_on_failure(
    max_attempts: int | None = None,
    wait_seconds: float | None = None,
    exceptions: tuple[type[Exception], ...] = RETRYABLE_EXCEPTIONS,
) -> Callable[[F], F]:
    """
    함수 실행 실패 시 재시도하는 데코레이터.

    Parameters
    ----------
    max_attempts : int, optional
        최대 시도 횟수 (기본: settings.RETRY_MAX_ATTEMPTS)
    wait_seconds : float, optional
        기본 대기 시간(초); 시도마다 2배씩 증가
    exceptions : tuple
        재시도할 예외 타입
    """

    attempts = max_attempts or RETRY_MAX_ATTEMPTS
    base_wait = wait_seconds or RETRY_WAIT_SECONDS

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == attempts:
                        logger.error(
                            "%s 최종 실패 (%d/%d): %s",
                            func.__name__,
                            attempt,
                            attempts,
                            exc,
                        )
                        raise
                    sleep_time = base_wait * (2 ** (attempt - 1))
                    logger.warning(
                        "%s 실패 (%d/%d), %.1f초 후 재시도: %s",
                        func.__name__,
                        attempt,
                        attempts,
                        sleep_time,
                        exc,
                    )
                    time.sleep(sleep_time)
            # 타입 체커용 (도달 불가)
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator

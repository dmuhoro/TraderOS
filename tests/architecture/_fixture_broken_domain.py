# Deliberately-broken domain file with an infrastructure import.
# This file EXISTS ONLY to prove the dependency-direction check can detect violations.
# It is NOT imported or used by any production code.
# The fitness test asserts this file triggers the dependency checker.
from traderos.infrastructure.retry import retry_with_backoff


class BrokenDomainService:
    def do_something(self) -> str:
        return retry_with_backoff(lambda: "ok", max_retries=1)

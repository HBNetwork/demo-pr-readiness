"""Stable cache keys for release requests."""


def store_key(repository: str, request_number: int) -> str:
    """Build the key used when caching a release request."""
    return f"{repository.casefold()}#{request_number}"


def lookup_key(repository: str, request_number: int) -> str:
    """Build the key used when retrieving a release request."""
    return f"{repository.casefold()}#{request_number}"

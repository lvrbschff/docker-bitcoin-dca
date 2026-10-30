"""Only positively classified exchange rejections may clear a pending action."""
import requests

class DefinitiveRejection(requests.RequestException):
    pass


class UncertainWithdrawalError(ValueError):
    """A financial submission is unresolved and requires verification."""

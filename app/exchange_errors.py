"""Only positively classified exchange rejections may clear a pending action."""
import requests

class DefinitiveRejection(requests.RequestException):
    pass

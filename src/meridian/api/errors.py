"""Stable HTTP problem codes without database or provider internals."""


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(message)

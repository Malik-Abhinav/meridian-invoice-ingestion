"""Bound multipart request bytes before Starlette buffers or parses an upload."""

from fastapi.responses import JSONResponse


class UploadBodyLimit:
    def __init__(self, app, *, max_upload_bytes: int):
        self.app = app
        # Multipart framing, headers, and the filename need a small separate allowance.
        self.max_request_bytes = max_upload_bytes + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/v1/invoices/upload":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", ()))
        content_length = headers.get(b"content-length", b"")
        if content_length.isdigit() and int(content_length) > self.max_request_bytes:
            await self._reject(scope, receive, send)
            return
        buffered = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_request_bytes:
                await self._reject(scope, receive, send)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay():
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope, receive, send):
        request_id = scope.get("state", {}).get("correlation_id", "")
        response = JSONResponse(status_code=413, content={"error": {
            "code": "upload_too_large", "message": "PDF exceeds the configured upload limit.",
            "correlation_id": request_id}})
        await response(scope, receive, send)

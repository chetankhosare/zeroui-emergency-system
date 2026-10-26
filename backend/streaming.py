"""
Byte-range file streaming
=========================

Browser ``<audio>`` elements request ``Range: bytes=...`` when the user seeks.
Starlette's ``FileResponse`` does not honour ranges, so segments are served
through this small helper.  Segment files are ~1–2 MB (60 s of 16 kHz mono PCM
in a WAV container), so reading the requested window into memory is fine.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import Response

logger = logging.getLogger(__name__)

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _read(path: str, start: int, length: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(start)
        return handle.read(length)


def stream_file(
    path: str,
    media_type: str,
    range_header: Optional[str] = None,
    filename: Optional[str] = None,
    download: bool = False,
) -> Response:
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Audio segment file is missing on disk")

    size = os.path.getsize(path)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    if filename:
        disposition = "attachment" if download else "inline"
        headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'

    if range_header:
        match = RANGE_RE.match(range_header.strip())
        if match:
            start_raw, end_raw = match.groups()
            if start_raw:
                start = int(start_raw)
                end = int(end_raw) if end_raw else size - 1
            else:
                # suffix range: last N bytes
                suffix = int(end_raw or 0)
                start = max(0, size - suffix)
                end = size - 1

            if start >= size or start > end:
                headers["Content-Range"] = f"bytes */{size}"
                return Response(status_code=416, headers=headers)

            end = min(end, size - 1)
            chunk = _read(path, start, end - start + 1)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(len(chunk))
            return Response(content=chunk, status_code=206, media_type=media_type, headers=headers)

    data = _read(path, 0, size)
    headers["Content-Length"] = str(size)
    return Response(content=data, status_code=200, media_type=media_type, headers=headers)

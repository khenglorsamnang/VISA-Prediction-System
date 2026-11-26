from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from ..core.config import BANK_STATEMENT_MAX_FILE_SIZE, settings
from .dependencies import get_current_principal

logger = logging.getLogger(__name__)
router = APIRouter()
SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


async def _extract(file: UploadFile, requirement_code: str) -> JSONResponse:
    filename = Path(file.filename or "document").name
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        return JSONResponse(status_code=400, content={"message": "Only PDF, JPG, JPEG, and PNG files are supported"})
    max_file_size = (
        BANK_STATEMENT_MAX_FILE_SIZE
        if requirement_code == "bank_statement"
        else settings.max_file_size
    )
    content = await file.read(max_file_size + 1)
    await file.close()
    if len(content) > max_file_size:
        return JSONResponse(status_code=413, content={"message": "Uploaded file exceeds the configured maximum size"})
    if not content:
        return JSONResponse(status_code=400, content={"message": "The uploaded file is empty"})
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_service_timeout_seconds, connect=5.0)
        ) as client:
            response = await client.post(
                f"{settings.ai_service_url}/ocr/extract",
                files={"file": (filename, content)},
                data={"requirement_code": requirement_code},
            )
        response.raise_for_status()
        return JSONResponse(content=response.json())
    except httpx.TimeoutException as error:
        logger.warning("OCR service timed out")
        return JSONResponse(status_code=504, content={"message": "OCR service timed out"})
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("OCR service request failed: %s", error.__class__.__name__)
        return JSONResponse(status_code=503, content={"message": "OCR service is unavailable"})


@router.post("/extract")
async def extract_document(
    file: UploadFile = File(...),
    requirement_code: str = Form(default="unknown", alias="requirement_code"),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return await _extract(file, requirement_code.strip().lower() or "unknown")


@router.post("/passport")
async def extract_passport(
    file: UploadFile = File(...),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return await _extract(file, "passport")


@router.post("/visa")
async def extract_visa_document(
    file: UploadFile = File(...),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return await _extract(file, "unknown")


import os
import re
import json
import asyncio
import logging
import time

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

import pytesseract

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dotenv import load_dotenv

from mrz_parser import clean_mrz_line, parse_mrz
from .ocr_engine import (
    OCRCancelledError,
    OCRDocument,
    OCRProcessingError,
    OCRService,
    PDFPageLimitError,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
# Share launcher defaults while keeping process and service-specific settings first.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


# =========================================================
# CONFIGURATION
# =========================================================

APP_NAME = "VisaDoc AI OCR Service"
APP_VERSION = "4.2.0"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("visadoc-ocr")

MAX_FILE_SIZE = int(
    os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024))
)

MAX_TEXT_LENGTH = 100_000
MAX_AI_TEXT = 25_000

OCR_LANGUAGE = os.getenv(
    "TESSERACT_LANG",
    "eng+khm",
)

TESSERACT_PATH = os.getenv(
    "TESSERACT_PATH",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)

if Path(TESSERACT_PATH).is_file():
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

ALLOWED_ORIGINS = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        os.getenv("CLIENT_URL", "http://localhost:5174,http://127.0.0.1:5174"),
    ).split(",")
    if item.strip()
]

DOCUMENT_TYPES = {
    "passport",
    "bank_statement",
    "fixed_deposit",
    "employment_letter",
    "employment_certificate",
    "salary_slip",
    "salary_certificate",
    "national_id",
    "family_book",
    "residence_book",
    "flight_booking",
    "flight_reservation",
    "hotel_booking",
    "travel_itinerary",
    "invitation_letter",
    "travel_insurance",
    "business_license",
    "tax_document",
    "education_certificate",
    "sponsorship_letter",
    "marriage_certificate",
    "birth_certificate",
    "other_supporting_document",
    "other",
    "unknown",
}


# =========================================================
# DOCUMENT FIELD DEFINITIONS
# =========================================================

DOCUMENT_REQUIRED_FIELDS = {
    "passport": [
        "passportNumber",
        "fullName",
        "dateOfBirth",
        "expiryDate",
        "nationality",
    ],
    "national_id": ["fullName", "nationalIdNumber", "dateOfBirth"],
    "bank_statement": [
        "bankName",
        "accountHolderName",
        "accountNumber",
        "openingBalance",
        "closingBalance",
        "currency",
        "statementStartDate",
        "statementEndDate",
        "deposits",
        "withdrawals",
        "transactionHistory",
        "averageBalance",
    ],
    "fixed_deposit": [
        "depositAmount",
        "currency",
        "maturityDate",
    ],
    "employment_letter": [
        "employerName",
        "jobTitle",
        "monthlySalary",
        "documentDate",
    ],
    "salary_slip": [
        "fullName",
        "monthlySalary",
        "documentDate",
    ],
    "marriage_certificate": ["spouse1Name", "spouse2Name", "marriageDate"],
    "birth_certificate": ["fullName", "dateOfBirth", "placeOfBirth"],
    "invitation_letter": [
        "inviterName",
        "inviteeName",
        "invitationDate",
        "stayAddress",
    ],
    "hotel_booking": [
        "guestName",
        "hotelName",
        "checkInDate",
        "checkOutDate",
    ],
    "flight_booking": [
        "passengerName",
        "departureDate",
        "origin",
        "destination",
    ],
}

_DOCUMENT_TYPE_ALIASES = {
    "national_id_card": "national_id",
    "nationalid": "national_id",
    "nationalidcard": "national_id",
    "national_identity": "national_id",
    "national_identity_card": "national_id",
    "identity_card": "national_id",
    "id_card": "national_id",
    "identity_document": "national_id",
    "nid": "national_id",
    "employment_certificate": "employment_letter",
    "salary_certificate": "salary_slip",
    "flight_reservation": "flight_booking",
    "other_supporting_document": "other",
}


def _normalize_document_type(value: str | None) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value or "").strip().casefold(),
    ).strip("_")
    return _DOCUMENT_TYPE_ALIASES.get(normalized, normalized)


# =========================================================
# APPLICATION
# =========================================================

ocr_service = OCRService()
ocr_jobs: dict[str, dict[str, Any]] = {}
ocr_job_keys: dict[str, str] = {}
ocr_jobs_lock = Lock()


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(ocr_service.initialize)
    yield


app = FastAPI(
    title=APP_NAME,
    description="PaddleOCR and Tesseract document OCR for VisaDoc AI",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


# =========================================================
# RESPONSE MODEL
# =========================================================

class OCRResponse(BaseModel):
    filename: str
    extracted_text: str = ""
    confidence: float = 0.0
    document_type: str = "unknown"
    fields: dict[str, Any] = Field(default_factory=dict)
    field_evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ai_analysis: dict[str, Any] = Field(default_factory=dict)
    pages: list[dict[str, Any]] = Field(default_factory=list)
    failed_pages: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    document_verification_score: int | None = None
    document_verification_score_basis: str | None = None
    status: str
    message: str | None = None


class OCRJobError(BaseModel):
    code: str
    message: str
    totalPages: int | None = None
    maximumAllowedPages: int | None = None


class OCRJob(BaseModel):
    id: str
    status: Literal[
        "QUEUED",
        "OCR_PENDING",
        "OCR_PROCESSING",
        "OCR_COMPLETED",
        "OCR_PARTIAL",
        "OCR_FAILED",
    ]
    progressPercent: int = Field(default=0, ge=0, le=100)
    pagesProcessed: int = Field(default=0, ge=0)
    totalPages: int = Field(default=0, ge=0)
    failedPages: list[dict[str, Any]] = Field(default_factory=list)
    message: str = "OCR job queued"
    result: dict[str, Any] | None = None
    error: OCRJobError | None = None


class OCRJobEnvelope(BaseModel):
    job: OCRJob


# =========================================================
# FILE DETECTION
# =========================================================

def detect_file_type(data: bytes) -> str | None:

    if data.startswith(b"%PDF-"):
        return "pdf"

    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    return None


# =========================================================
# TESSERACT CONFIGURATION
# =========================================================

def get_ocr_language() -> str:

    try:
        installed = set(
            pytesseract.get_languages(config="")
        )

        requested = OCR_LANGUAGE.split("+")

        available = [
            language
            for language in requested
            if language in installed
        ]

        if not available:
            raise RuntimeError(
                f"No OCR language installed. Requested: {OCR_LANGUAGE}"
            )

        if len(available) != len(requested):
            logger.warning(
                "Some OCR languages are unavailable. Using: %s",
                available,
            )

        return "+".join(available)

    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract executable not found. Check TESSERACT_PATH."
        ) from exc


# =========================================================
# HEALTH ENDPOINT
# =========================================================

@app.get("/")
async def root():

    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    runtime = ocr_service.runtime_info
    paddle_gpu_count = runtime["hardware"].get("paddle_gpu_count")
    language = None
    tesseract_status = "disabled"
    if ocr_service.tesseract_fallback_enabled:
        try:
            language = get_ocr_language()
            tesseract_status = "connected"
        except RuntimeError as exc:
            logger.warning("Tesseract health error: %s", exc)
            tesseract_status = "offline"

    return {
        "status": "ok" if runtime["initialized"] else "degraded",
        "health_status": "healthy" if runtime["initialized"] else "degraded",
        "service": "visadoc-ocr",
        "version": APP_VERSION,
        "provider": runtime["engine"],
        "ocr_engine": runtime["engine"],
        "gpu_available": bool(
            runtime["engine"] == "paddleocr"
            and runtime["hardware"].get("paddle_cuda_supported")
            and isinstance(paddle_gpu_count, int)
            and paddle_gpu_count > 0
        ),
        "gpu_detected": bool(runtime["hardware"].get("nvidia_gpu_available")),
        "gpu_initialized": bool(
            runtime["engine"] == "paddleocr"
            and str(runtime.get("device") or "").startswith("gpu")
        ),
        "fallback_available": bool(ocr_service._tesseract_ready),
        "ollama": "disabled",
        "tesseract": tesseract_status,
        "ocr_language": language,
        "max_file_size": MAX_FILE_SIZE,
        "max_pdf_pages": ocr_service.max_pdf_pages,
        "ocr_batch_size": ocr_service.batch_size,
        "ocr": runtime,
    }


# =========================================================
# OCR COMPATIBILITY HELPERS
# =========================================================

def extract_image_text(data: bytes) -> tuple[str, float]:
    result = ocr_service.process_document(data, "image")
    return result.text, result.confidence


def extract_pdf_text(data: bytes) -> tuple[str, float]:
    result = ocr_service.process_document(data, "pdf")
    return result.text, result.confidence


# =========================================================
# DATE EXTRACTION
# =========================================================

DATE_PATTERN = re.compile(
    r"\b(?:"
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}"
    r")\b",
    re.I,
)


def normalize_date(value: str) -> str | None:

    value = re.sub(
        r"(\d)(st|nd|rd|th)\b",
        r"\1",
        value.strip(),
        flags=re.I,
    )

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
    ]

    for fmt in formats:

        try:
            return datetime.strptime(
                value.replace(",", ""),
                fmt,
            ).date().isoformat()

        except ValueError:
            continue

    return None


def _normalize_national_id_number(value: str) -> str | None:
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    digit_map = str.maketrans(
        {
            "O": "0",
            "Q": "0",
            "D": "0",
            "I": "1",
            "L": "1",
            "Z": "2",
            "S": "5",
            "B": "8",
        }
    )
    alpha_prefix = re.fullmatch(r"([A-Z]{1,3})([0-9OQILSZBD]{4,12})", compact)
    if alpha_prefix:
        normalized = alpha_prefix.group(1) + alpha_prefix.group(2).translate(digit_map)
        return normalized if 6 <= len(normalized) <= 14 else None
    normalized = compact.translate(digit_map)
    return normalized if 6 <= len(normalized) <= 14 and normalized.isdigit() else None


# =========================================================
# LABEL-BASED FIELD EXTRACTION
# =========================================================

AMOUNT_TOKEN = re.compile(r"\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{1,2}|\d{1,3}(?:[.,]\d{3})+|\d+")


def normalize_amount(value: str) -> str | None:
    """Normalize 15,020.83 and 15.020,83 into a machine-safe decimal string."""
    token = AMOUNT_TOKEN.search(str(value or ""))
    if not token:
        return None
    number = token.group(0)
    comma = number.rfind(",")
    dot = number.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        normalized = number.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif comma >= 0:
        normalized = number.replace(",", ".") if len(number) - comma - 1 in (1, 2) else number.replace(",", "")
    elif dot >= 0:
        normalized = number if len(number) - dot - 1 in (1, 2) else number.replace(".", "")
    else:
        normalized = number
    try:
        return format(float(normalized), ".2f")
    except ValueError:
        return None


def extract_transactions(text: str) -> list[dict[str, Any]]:
    """Extract candidate transactions from every PDF page marker and line."""
    transactions = []
    current_page = 1
    column_label = re.compile(
        r"\b(?P<label>running\s+balance|balance|debit|credit)\b\s*[:=]?\s*",
        re.I,
    )
    for line in text.splitlines():
        page_match = re.match(r"--- PAGE (\d+)", line)
        if page_match:
            current_page = int(page_match.group(1))
            continue
        if re.search(
            r"\b(?:statement\s+period|period\s+from|opening\s+balance|"
            r"beginning\s+balance|closing\s+balance|ending\s+balance|"
            r"average\s+balance|statement\s+date)\b",
            line,
            re.I,
        ):
            continue
        date_match = DATE_PATTERN.search(line)
        if not date_match:
            continue
        transaction_date = normalize_date(date_match.group(0))
        if not transaction_date:
            continue

        transaction_text = line[date_match.end():].strip(" -|:")
        monetary = list(AMOUNT_TOKEN.finditer(transaction_text))
        if not monetary:
            continue

        labels = list(column_label.finditer(transaction_text))

        def labeled_amount(label: str) -> re.Match[str] | None:
            for index, match in enumerate(labels):
                if match.group("label").casefold() != label:
                    continue
                next_label_start = (
                    labels[index + 1].start()
                    if index + 1 < len(labels)
                    else len(transaction_text)
                )
                token = next(
                    (
                        token
                        for token in monetary
                        if match.end() <= token.start() < next_label_start
                    ),
                    None,
                )
                if token:
                    return token
            return None

        debit_match = labeled_amount("debit")
        credit_match = labeled_amount("credit")
        balance_match = labeled_amount("balance") or labeled_amount("running balance")
        explicit_direction = None
        amount_match = None
        if debit_match or credit_match:
            debit_amount = normalize_amount(debit_match.group(0)) if debit_match else None
            credit_amount = normalize_amount(credit_match.group(0)) if credit_match else None
            debit_value = float(debit_amount) if debit_amount is not None else 0
            credit_value = float(credit_amount) if credit_amount is not None else 0
            if debit_value > 0 and credit_value == 0:
                amount_match = debit_match
                explicit_direction = "debit"
            elif credit_value > 0 and debit_value == 0:
                amount_match = credit_match
                explicit_direction = "credit"
            elif debit_value == 0 and credit_value == 0:
                amount_match = debit_match or credit_match
            # If both columns contain values, the row is ambiguous; retain its
            # source evidence without assigning a transaction amount.
        else:
            amount_candidates = [
                token for token in monetary if token is not balance_match
            ]
            if not amount_candidates:
                continue
            amount_match = (
                amount_candidates[-2]
                if len(amount_candidates) > 1 and balance_match is None
                else amount_candidates[-1]
            )
            if balance_match is None and len(monetary) > 1:
                balance_match = monetary[-1]
                amount_match = monetary[-2]

        parsed_amount = (
            normalize_amount(amount_match.group(0)) if amount_match else None
        )
        if amount_match is None and not (debit_match or credit_match):
            continue

        description_end = min(
            (match.start() for match in labels),
            default=(monetary[0].start()),
        )
        description = transaction_text[:description_end].strip(" -|:")[:160]
        balance = normalize_amount(balance_match.group(0)) if balance_match else None
        preceding = (
            transaction_text[max(0, amount_match.start() - 2):amount_match.start()]
            if amount_match
            else ""
        )
        is_credit = bool(
            re.search(r"\b(?:credit|deposit|salary|income|money\s+in|cr)\b", description, re.I)
        )
        is_debit = bool(
            re.search(r"\b(?:debit|withdraw(?:al)?|payment|purchase|fee|money\s+out|dr)\b", description, re.I)
        )
        if explicit_direction:
            transaction_type = explicit_direction
        elif preceding.rstrip().endswith("+"):
            is_credit = True
        elif preceding.rstrip().endswith("-") or preceding.rstrip().endswith("("):
            is_debit = True
        if not explicit_direction:
            transaction_type = (
                "credit" if is_credit and not is_debit
                else "debit" if is_debit and not is_credit
                else "unknown"
            )

        transaction = {
            "date": transaction_date,
            "description": description,
            "amount": parsed_amount,
            "type": transaction_type,
            "page": str(current_page),
            "source_text": line.strip()[:500],
        }
        if balance:
            transaction["balance"] = balance
        transactions.append({
            key: value for key, value in transaction.items() if value is not None
        })
    return transactions


def extract_monthly_balances(text: str) -> list[dict[str, str]]:
    """Extract explicitly labeled month-end opening and closing balance lines."""
    month_names = {
        name.casefold(): index
        for index, name in enumerate(
            (
                "",
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            )
        )
        if name
    }
    month_token = re.compile(
        r"\b(?:(?P<iso_year>\d{4})[-/](?P<iso_month>0?[1-9]|1[0-2])|"
        r"(?P<named_month>January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(?P<named_year>\d{4})|"
        r"(?P<numeric_month>0?[1-9]|1[0-2])[-/](?P<numeric_year>\d{4}))\b",
        re.I,
    )
    balance_label = re.compile(
        r"\b(?P<kind>opening|beginning|closing|ending)\s+balance\b", re.I
    )
    amount_pattern = re.compile(
        r"(?:(USD|US\s+DOLLARS?|KHR|RIEL|EUR|THB|[$៛])\s*)?"
        r"(\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{1,2}|"
        r"\d{1,3}(?:[.,]\d{3})+|\d+)"
        r"(?:\s*(USD|US\s+DOLLARS?|KHR|RIEL|EUR|THB|[$៛]))?",
        re.I,
    )
    records: dict[str, dict[str, str]] = {}
    current_page = 1
    for line in text.splitlines():
        page_match = re.match(r"--- PAGE (\d+)", line)
        if page_match:
            current_page = int(page_match.group(1))
            continue
        month_match = month_token.search(line)
        label_match = balance_label.search(line)
        if not month_match or not label_match:
            continue
        if month_match.group("iso_year"):
            year = int(month_match.group("iso_year"))
            month = int(month_match.group("iso_month"))
        elif month_match.group("named_year"):
            year = int(month_match.group("named_year"))
            month = month_names[month_match.group("named_month").casefold()]
        else:
            year = int(month_match.group("numeric_year"))
            month = int(month_match.group("numeric_month"))
        key = f"{year:04d}-{month:02d}"
        amount_match = amount_pattern.search(line, label_match.end())
        if not amount_match:
            continue
        amount = normalize_amount(amount_match.group(2))
        if amount is None:
            continue
        currency = amount_match.group(1) or amount_match.group(3)
        if currency:
            currency = currency.upper()
            if currency in {"US DOLLAR", "US DOLLARS", "$"}:
                currency = "USD"
            elif currency in {"RIEL", "៛"}:
                currency = "KHR"
        record = records.setdefault(
            key,
            {
                "month": key,
                "page": str(current_page),
                "sourceText": line.strip()[:500],
            },
        )
        kind = label_match.group("kind").casefold()
        opening = kind in {"opening", "beginning"}
        field = "openingBalance" if opening else "closingBalance"
        record[field] = amount
        evidence_prefix = "opening" if opening else "closing"
        record[f"{evidence_prefix}Page"] = str(current_page)
        record[f"{evidence_prefix}SourceText"] = line.strip()[:500]
        if currency:
            record["currency"] = currency
        if not opening or "closingPage" not in record:
            record["page"] = str(current_page)
            record["sourceText"] = line.strip()[:500]
    return [records[key] for key in sorted(records)]


def fallback_extract_fields(
    text: str,
    requirement_code: str | None = None,
) -> dict[str, Any]:

    fields = {}

    if not isinstance(text, str):
        return fields

    def find_date(labels: str):

        for match in re.finditer(labels, text, re.I):

            segment = text[
                match.end():match.end() + 120
            ]

            candidate = DATE_PATTERN.search(segment)

            if candidate:

                result = normalize_date(
                    candidate.group(0)
                )

                if result:
                    return result

        return None

    def find_amount(labels: str):

        pattern = re.compile(
            r"(?:(USD|US\s+DOLLARS?|KHR|RIEL|EUR|THB|[$៛])\s*)?"
            r"(\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{1,2}|\d{1,3}(?:[.,]\d{3})+|\d+)"
            r"(?:\s*(USD|US\s+DOLLARS?|KHR|RIEL|EUR|THB|[$៛]))?",
            re.I,
        )

        for match in re.finditer(labels, text, re.I):

            segment = text[
                match.end():match.end() + 120
            ].splitlines()[0]

            amount = pattern.search(segment)

            if not amount:
                continue

            currency = amount.group(1) or amount.group(3)

            if currency:
                currency_value = currency.upper()
                if currency_value in {"US DOLLAR", "US DOLLARS", "$"}:
                    currency_value = "USD"
                elif currency_value == "RIEL" or currency_value == "៛":
                    currency_value = "KHR"
                currency = currency_value

            normalized_amount = normalize_amount(amount.group(2))
            if normalized_amount is None:
                continue
            return {
                "amount": normalized_amount,
                "currency": currency,
            }

        return None

    def find_text(labels: str, *, max_length: int = 120):
        match = re.search(
            rf"(?:{labels})\s*[:#\-]?\s*([^\n]{{1,{max_length}}})",
            text,
            re.I,
        )
        if not match:
            return None
        value = re.split(
            r"\s{2,}|\s+\|\s+|\s+(?:phone|email|telephone)\s*[:#-]",
            match.group(1),
            maxsplit=1,
            flags=re.I,
        )[0].strip(" \t,;|")
        return value or None

    requirement_code = _normalize_document_type(requirement_code)

    # -----------------------------------------------------
    # BANK STATEMENT
    # -----------------------------------------------------

    if requirement_code == "bank_statement":

        def find_label_value(labels: str):
            return find_text(labels, max_length=100)

        bank_name = find_label_value(
            r"(?:bank\s+name|name\s+of\s+bank|financial\s+institution)"
        )
        holder = find_label_value(
            r"(?:account\s+holder(?:\s+name)?|account\s+name|customer\s+name)"
        )
        account_number = find_label_value(
            r"(?:account\s+(?:number|no\.?|#|id)|a/c\s*(?:number|no\.?|#)?)"
        )
        if bank_name:
            fields["bankName"] = bank_name
        if holder:
            fields["accountHolderName"] = holder
        if account_number:
            fields["accountNumber"] = re.sub(r"[^A-Za-z0-9]", "", account_number)

        currency_match = re.search(
            r"\b(?:currency|currency\s+code)\s*[:\-]?\s*(USD|KHR|EUR|THB|GBP|CAD|AUD)\b",
            text,
            re.I,
        )
        if not currency_match:
            currency_match = re.search(
                r"\b(USD|KHR|EUR|THB|GBP|CAD|AUD)\b",
                text,
                re.I,
            )
        if currency_match:
            fields["currency"] = currency_match.group(1).upper()

        opening = find_amount(r"(?:opening|beginning)\s+balance")
        if opening:
            fields["openingBalance"] = opening["amount"]
            if opening["currency"]:
                fields["currency"] = opening["currency"]

        balance = find_amount(
            r"(?:closing|ending|available|current)\s+balance"
        )

        if balance:

            fields["closingBalance"] = balance["amount"]

            if balance["currency"]:
                fields["currency"] = balance["currency"]

        deposits = find_amount(
            r"(?:total\s+)?(?:deposits|credits|income|total\s+money\s+in|total\s+credits)"
        )
        withdrawals = find_amount(
            r"(?:total\s+)?(?:withdrawals|debits|total\s+debits)"
        )
        average = find_amount(r"average\s+balance")
        if deposits:
            fields["deposits"] = deposits["amount"]
        if withdrawals:
            fields["withdrawals"] = withdrawals["amount"]
        if average:
            fields["averageBalance"] = average["amount"]

        transactions = extract_transactions(text)
        if transactions:
            fields["transactionHistory"] = transactions
        monthly_balances = extract_monthly_balances(text)
        if monthly_balances:
            fields["monthlyBalanceHistory"] = monthly_balances
            fields["monthlyClosingBalances"] = [
                item for item in monthly_balances if item.get("closingBalance")
            ]
            fields["monthlyOpeningBalances"] = [
                item for item in monthly_balances if item.get("openingBalance")
            ]
        if "UNREADABLE" in text:
            fields["missingPages"] = True

        period_match = re.search(
            r"(?:statement\s+period|period\s+from|for\s+the\s+period)\s*[:\-]?\s*([^\n]{0,160})",
            text,
            re.I,
        )

        if period_match:

            period_segment = period_match.group(1)

            date_candidates = DATE_PATTERN.findall(
                period_segment
            )

            normalized = []

            for candidate in date_candidates:

                result = normalize_date(candidate)

                if result:
                    normalized.append(result)

            if len(normalized) >= 2:

                fields["statementStartDate"] = normalized[0]
                fields["statementEndDate"] = normalized[1]
        if not fields.get("statementStartDate") or not fields.get("statementEndDate"):
            period_dates = [
                normalize_date(candidate)
                for candidate in DATE_PATTERN.findall(text[:MAX_TEXT_LENGTH])
            ]
            period_dates = [value for value in period_dates if value]
            if len(period_dates) >= 2 and re.search(
                r"\b(?:statement\s+period|period\s+from|from\s+\d|to\s+\d)\b",
                text,
                re.I,
            ):
                fields.setdefault("statementStartDate", period_dates[0])
                fields.setdefault("statementEndDate", period_dates[1])

        statement_date = find_date(
            r"(?:statement\s+date|as\s+of)"
        )

        if statement_date:
            fields["documentDate"] = statement_date

    # -----------------------------------------------------
    # FIXED DEPOSIT
    # -----------------------------------------------------

    elif requirement_code == "fixed_deposit":

        deposit = find_amount(
            r"(?:deposit(?:ed)?\s+amount|principal\s+amount|principal)"
        )

        maturity = find_date(
            r"(?:maturity\s+date|matures?\s+on|due\s+date)"
        )

        if deposit:

            fields["depositAmount"] = deposit["amount"]

            if deposit["currency"]:
                fields["currency"] = deposit["currency"]

        if maturity:
            fields["maturityDate"] = maturity

    # -----------------------------------------------------
    # PASSPORT
    # -----------------------------------------------------

    elif requirement_code == "passport":
        passport_number = find_text(
            r"(?:passport\s*(?:number|no\.?|#)|document\s*(?:number|no\.?))",
            max_length=40,
        )
        full_name = find_text(
            r"(?:full\s+name|name\s+of\s+holder|surname\s*/?\s*given\s+names?)",
            max_length=100,
        )
        nationality = find_text(r"nationality", max_length=50)
        birth = find_date(
            r"(?:date\s+of\s+birth|birth\s+date|DOB)"
        )

        expiry = find_date(
            r"(?:date\s+of\s+expiry|expiry\s+date|"
            r"expiration\s+date|valid\s+until)"
        )

        if birth:
            fields["dateOfBirth"] = birth
        if passport_number:
            fields["passportNumber"] = re.sub(
                r"[^A-Za-z0-9]", "", passport_number
            )
        if full_name:
            fields["fullName"] = full_name
        if nationality:
            fields["nationality"] = nationality

        if expiry:
            fields["expiryDate"] = expiry

    elif requirement_code == "national_id":
        national_id_number = find_text(
            r"(?:national\s+(?:identity\s+)?id(?:\s+card)?|"
            r"national\s+identity\s+card|identity\s+card|id\s+card|"
            r"document)\s*(?:number|no\.?|#)",
            max_length=50,
        )
        normalized_number = _normalize_national_id_number(national_id_number or "")
        if not normalized_number:
            for line in text.splitlines():
                candidate = line.strip()
                if not candidate or normalize_date(candidate):
                    continue
                if not re.fullmatch(r"[0-9OQILSZBD](?:[0-9OQILSZBD \-]{5,18})", candidate, re.I):
                    continue
                normalized_number = _normalize_national_id_number(candidate)
                if normalized_number:
                    break
        full_name = find_text(
            r"(?:family\s+and\s+given\s+names?|full\s+name|"
            r"name\s+of\s+(?:holder|citizen)|cardholder\s+name)",
            max_length=100,
        )
        if full_name:
            full_name = re.sub(r"^\s*s\s*[:;|]\s*", "", full_name, flags=re.I)
            full_name = re.split(
                r"\s+(?:sex|gender|height|date\s+of\s+birth)\s*[:#-]?",
                full_name,
                maxsplit=1,
                flags=re.I,
            )[0].strip(" \t,;|:")
        birth = find_date(r"(?:date\s+of\s+birth|birth\s+date|DOB)")
        nationality = find_text(r"nationality", max_length=50)
        gender_match = re.search(
            r"\b(?:gender|sex)\s*[:#-]?\s*(female|male|f|m)\b",
            text,
            re.I,
        )
        gender = (
            "Female"
            if gender_match and gender_match.group(1).casefold() in {"female", "f"}
            else "Male"
            if gender_match
            else None
        )
        address = find_text(r"(?:residential\s+address|address)", max_length=160)
        expiry = find_date(
            r"(?:date\s+of\s+expiry|expiry\s+date|expiration\s+date|valid\s+until)"
        )
        if not expiry:
            validity_match = re.search(r"\bvalidity\b", text, re.I)
            if validity_match:
                date_candidates = DATE_PATTERN.findall(
                    text[validity_match.start():validity_match.start() + 240]
                )
                if date_candidates:
                    expiry = normalize_date(date_candidates[-1])
        if normalized_number:
            fields["nationalIdNumber"] = normalized_number
        if full_name:
            fields["fullName"] = full_name
        if birth:
            fields["dateOfBirth"] = birth
        if nationality:
            fields["nationality"] = nationality
        if gender:
            fields["gender"] = gender
        if address:
            fields["address"] = address
        if expiry:
            fields["expiryDate"] = expiry

    elif requirement_code == "employment_letter":
        for field, labels in (
            ("fullName", r"(?:employee|staff|applicant)\s+name|full\s+name"),
            ("employerName", r"(?:employer|company|organization)\s+name"),
            ("jobTitle", r"(?:job|position|designation)\s+title|occupation"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        salary = find_amount(
            r"(?:monthly\s+salary|salary\s+per\s+month|monthly\s+income|salary)"
        )
        if salary:
            fields["monthlySalary"] = salary["amount"]
            if salary["currency"]:
                fields["currency"] = salary["currency"]
        document_date = find_date(
            r"(?:date\s+of\s+issue|issued\s+on|certificate\s+date|date)"
        )
        if document_date:
            fields["documentDate"] = document_date

    elif requirement_code == "salary_slip":
        for field, labels in (
            ("fullName", r"(?:employee|staff|payee)\s+name|full\s+name"),
            ("employerName", r"(?:employer|company)\s+name"),
            ("jobTitle", r"(?:job|position|designation)\s+title"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        salary = find_amount(
            r"(?:net\s+salary|gross\s+salary|monthly\s+salary|total\s+earnings)"
        )
        if salary:
            fields["monthlySalary"] = salary["amount"]
            if salary["currency"]:
                fields["currency"] = salary["currency"]
        document_date = find_date(
            r"(?:pay\s+date|salary\s+period|statement\s+date|date)"
        )
        if document_date:
            fields["documentDate"] = document_date

    elif requirement_code == "marriage_certificate":
        for field, labels in (
            ("spouse1Name", r"(?:spouse\s*1|husband|groom|first\s+party)\s*(?:name)?"),
            ("spouse2Name", r"(?:spouse\s*2|wife|bride|second\s+party)\s*(?:name)?"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        marriage_date = find_date(
            r"(?:date\s+of\s+marriage|marriage\s+date|date\s+married)"
        )
        if marriage_date:
            fields["marriageDate"] = marriage_date
        certificate_number = find_text(
            r"(?:certificate|registration)\s*(?:number|no\.?|#)",
            max_length=50,
        )
        if certificate_number:
            fields["certificateNumber"] = certificate_number

    elif requirement_code == "birth_certificate":
        for field, labels in (
            ("fullName", r"(?:child|person|registered)\s+name|full\s+name|name\s+of\s+child"),
            ("placeOfBirth", r"(?:place|location)\s+of\s+birth|birthplace"),
            ("parentName", r"(?:mother|father|parent)\s+(?:name|full\s+name)"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        birth = find_date(r"(?:date\s+of\s+birth|birth\s+date|DOB)")
        if birth:
            fields["dateOfBirth"] = birth
        certificate_number = find_text(
            r"(?:certificate|registration)\s*(?:number|no\.?|#)",
            max_length=50,
        )
        if certificate_number:
            fields["certificateNumber"] = certificate_number

    elif requirement_code == "invitation_letter":
        for field, labels in (
            ("inviterName", r"(?:inviter|host|sponsor)\s+name"),
            ("inviteeName", r"(?:invitee|visitor|guest)\s+name"),
            ("stayAddress", r"(?:accommodation|stay|residential)\s+address|address"),
            ("inviterContact", r"(?:inviter|host)\s+(?:phone|telephone|email|contact)"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        invitation_date = find_date(
            r"(?:invitation|letter|document)\s+date|date\s+of\s+issue"
        )
        if invitation_date:
            fields["invitationDate"] = invitation_date
        arrival = find_date(r"(?:arrival|start)\s+date|from\s+date")
        departure = find_date(r"(?:departure|end)\s+date|until\s+date")
        if arrival:
            fields["arrivalDate"] = arrival
        if departure:
            fields["departureDate"] = departure

    elif requirement_code == "hotel_booking":
        for field, labels in (
            ("guestName", r"(?:guest|customer|booked\s+by)\s+name|lead\s+guest"),
            ("hotelName", r"(?:hotel|property|accommodation)\s+name"),
            ("bookingReference", r"(?:booking|reservation|confirmation)\s*(?:reference|number|no\.?|#)"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        check_in = find_date(r"(?:check[\s-]?in|arrival)\s+date")
        check_out = find_date(r"(?:check[\s-]?out|departure)\s+date")
        if check_in:
            fields["checkInDate"] = check_in
        if check_out:
            fields["checkOutDate"] = check_out

    elif requirement_code == "flight_booking":
        for field, labels in (
            ("passengerName", r"(?:passenger|traveler|traveller)\s+name"),
            ("bookingReference", r"(?:booking|reservation|confirmation)\s*(?:reference|number|no\.?|#)"),
            ("airline", r"(?:airline|carrier)\s+name"),
            ("origin", r"(?:origin|departure\s+airport|from\s+airport)"),
            ("destination", r"(?:destination|arrival\s+airport|to\s+airport)"),
        ):
            value = find_text(labels)
            if value:
                fields[field] = value
        departure = find_date(
            r"(?:departure|travel|flight)\s+date|date\s+of\s+travel"
        )
        if departure:
            fields["departureDate"] = departure

    return fields


# =========================================================
# PUBLIC FIELD EXTRACTION API
# =========================================================

def extract_fields(
    text: str,
    document_type: str,
) -> dict[str, Any]:
    """
    Public extraction function for unit tests and modules.

    Supported:
    - passport
    - bank_statement
    - fixed_deposit

    Only extracts values that have matching labels.
    """

    if not isinstance(text, str) or not text.strip():
        return {}

    document_type = (
        document_type.strip().lower()
        if isinstance(document_type, str)
        else ""
    )

    fields = fallback_extract_fields(
        text,
        document_type,
    )

    if document_type == "passport":

        result = {}

        if "dateOfBirth" in fields:
            result["passportDateOfBirth"] = fields["dateOfBirth"]

        if "expiryDate" in fields:
            result["passportExpiryDate"] = fields["expiryDate"]

        return result

    return fields


# =========================================================
# MISSING FIELD DETECTION
# =========================================================

def get_missing_fields(
    fields: dict[str, Any],
    document_type: str,
) -> list[str]:

    required = DOCUMENT_REQUIRED_FIELDS.get(
        document_type,
        [],
    )

    return [
        field
        for field in required
        if fields.get(field) is None
        or fields.get(field) == ""
    ]




def _merge_mrz_fields(
    fields: dict[str, Any],
    text: str,
    requirement_code: str | None,
    mrz: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mrz = mrz if isinstance(mrz, dict) else parse_mrz(text)
    data = mrz.get("data") if isinstance(mrz, dict) else None
    if not isinstance(data, dict):
        return fields

    if requirement_code == "national_id":
        national_id_fields = {
            "nationalIdNumber": data.get("national_id_number"),
            "fullName": data.get("full_name")
            or " ".join(
                part
                for part in (data.get("surname"), data.get("given_names"))
                if part
            ),
            "dateOfBirth": data.get("date_of_birth"),
            "nationality": (
                "Cambodia"
                if str(data.get("nationality") or "").upper() in {"KH", "KHM"}
                else data.get("nationality")
            ),
            "gender": (
                "Female"
                if data.get("sex") == "F"
                else "Male"
                if data.get("sex") == "M"
                else None
            ),
            "expiryDate": data.get("expiry_date"),
        }
        for field, value in national_id_fields.items():
            if value not in (None, "", []) and fields.get(field) in (None, "", []):
                fields[field] = value
        return fields

    if not requirement_code or requirement_code in {"passport", "unknown"}:
        if data.get("passport_number"):
            fields.setdefault("passportNumber", str(data["passport_number"]))
        if data.get("date_of_birth"):
            fields.setdefault("dateOfBirth", str(data["date_of_birth"]))
        if data.get("expiry_date"):
            fields.setdefault("expiryDate", str(data["expiry_date"]))
        if data.get("nationality"):
            fields.setdefault("nationality", str(data["nationality"]))
        if data.get("sex") and data.get("sex") != "<":
            fields.setdefault("gender", str(data["sex"]))
        if data.get("surname") or data.get("given_names"):
            full_name = " ".join(part for part in [data.get("surname"), data.get("given_names")] if part)
            if full_name:
                fields.setdefault("fullName", full_name)
    return fields


def _field_evidence(
    fields: dict[str, Any],
    processed: OCRDocument,
    mrz: dict[str, Any] | None,
    document_type: str,
) -> dict[str, dict[str, Any]]:
    """Persist field provenance without claiming evidence that OCR did not see."""
    evidence: dict[str, dict[str, Any]] = {}
    mrz_data = mrz.get("data") if isinstance(mrz, dict) else None
    mrz_field_lines = mrz.get("field_lines") if isinstance(mrz, dict) else {}
    if document_type == "national_id":
        field_sources = {
            "nationalIdNumber": "national_id_number",
            "fullName": "full_name",
            "dateOfBirth": "date_of_birth",
            "expiryDate": "expiry_date",
            "nationality": "nationality",
            "gender": "sex",
        }

        def same_value(field: str, left: Any, right: Any) -> bool:
            if field == "nationalIdNumber":
                return _normalize_national_id_number(str(left or "")) == (
                    _normalize_national_id_number(str(right or ""))
                )
            if field in {"dateOfBirth", "expiryDate"}:
                return normalize_date(str(left or "")) == normalize_date(
                    str(right or "")
                )
            if field == "nationality":
                return str(left or "").casefold() in {
                    str(right or "").casefold(),
                    "kh",
                    "khm",
                    "cambodia",
                    "cambodian",
                } and str(right or "").casefold() in {
                    str(left or "").casefold(),
                    "kh",
                    "khm",
                    "cambodia",
                    "cambodian",
                }
            if field == "gender":
                return str(left or "").casefold()[:1] == str(right or "").casefold()[:1]
            if field == "fullName":
                normalize_name_value = lambda value: tuple(
                    sorted(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
                )
                return normalize_name_value(left) == normalize_name_value(right)
            return str(left or "").strip().casefold() == str(right or "").strip().casefold()

        def source_line(page_text: str, field: str, value: Any) -> str:
            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            if field == "nationalIdNumber":
                for line in lines:
                    if _normalize_national_id_number(line) == _normalize_national_id_number(
                        str(value or "")
                    ):
                        return line
            label_patterns = {
                "fullName": r"family\s+and\s+given\s+names?|full\s+name|cardholder\s+name",
                "dateOfBirth": r"date\s+of\s+birth|birth\s+date|\bDOB\b",
                "expiryDate": r"\bvalidity\b|date\s+of\s+expiry|expiry\s+date|valid\s+until",
                "gender": r"\b(?:sex|gender)\b",
                "address": r"\baddress\b",
            }
            pattern = label_patterns.get(field)
            if pattern:
                for index, line in enumerate(lines):
                    if re.search(pattern, line, re.I):
                        if field in {"dateOfBirth", "expiryDate"} and index + 1 < len(lines):
                            return f"{line} {lines[index + 1]}"
                        if (
                            field == "address"
                            and re.fullmatch(r"\s*address\s*[:#-]?\s*", line, re.I)
                            and index + 1 < len(lines)
                        ):
                            return f"{line} {lines[index + 1]}"
                        return line
            return ""

        pages = processed.pages
        page_fields = [
            (page, fallback_extract_fields(page.text, document_type))
            for page in pages
        ]
        page_mrz_results = [
            (page, parse_mrz(page.text))
            for page in pages
        ]
        for field, value in fields.items():
            if value in (None, "", []):
                continue
            source = "labelled_ocr"
            source_text = ""
            source_page = None
            mrz_key = field_sources.get(field)
            mrz_value = None
            if isinstance(mrz_data, dict) and mrz_key:
                mrz_value = mrz_data.get(mrz_key)
                if field == "gender" and mrz_value in {"F", "M"}:
                    mrz_value = "Female" if mrz_value == "F" else "Male"
                elif field == "nationality" and str(mrz_value or "").upper() in {"KH", "KHM"}:
                    mrz_value = "Cambodia"
            for page, page_values in page_fields:
                page_value = page_values.get(field)
                if page_value not in (None, "", []) and same_value(
                    field, page_value, value
                ):
                    source_text = source_line(page.text, field, value) or str(page_value)
                    source_page = page
                    break
            if source_page is None and mrz_value not in (None, "", []) and same_value(
                field, mrz_value, value
            ):
                for page, page_mrz in reversed(page_mrz_results):
                    page_data = (
                        page_mrz.get("data")
                        if isinstance(page_mrz, dict)
                        else None
                    )
                    page_value = (
                        page_data.get(mrz_key)
                        if isinstance(page_data, dict)
                        else None
                    )
                    if field == "gender" and page_value in {"F", "M"}:
                        page_value = "Female" if page_value == "F" else "Male"
                    elif field == "nationality" and str(page_value or "").upper() in {"KH", "KHM"}:
                        page_value = "Cambodia"
                    if page_value not in (None, "", []) and same_value(
                        field, page_value, value
                    ):
                        page_lines = page_mrz.get("field_lines") or {}
                        source = "mrz"
                        source_text = str(page_lines.get(mrz_key) or "")
                        source_page = page
                        break
            if source_page is None and mrz_value not in (None, "", []) and same_value(
                field, mrz_value, value
            ):
                mrz_line = (
                    mrz_field_lines.get(mrz_key)
                    if isinstance(mrz_field_lines, dict)
                    else None
                )
                if mrz_line:
                    for page in reversed(pages):
                        if any(
                            clean_mrz_line(line) == clean_mrz_line(mrz_line)
                            for line in page.text.splitlines()
                        ):
                            source = "mrz"
                            source_text = mrz_line
                            source_page = page
                            break
            if source_page is None:
                for page in pages:
                    candidate_line = source_line(page.text, field, value)
                    if candidate_line:
                        source_text = candidate_line
                        source_page = page
                        break
            evidence[field] = {
                "value": value,
                "source": source,
                "evidenceText": source_text[:500] if source_text else str(value)[:500],
                "page": source_page.page if source_page else None,
                "confidence": source_page.confidence if source_page else None,
                "documentType": document_type,
            }
        return evidence

    mrz_fields = {
        "passportNumber": "passport_number",
        "fullName": "name",
        "dateOfBirth": "date_of_birth",
        "expiryDate": "expiry_date",
        "nationality": "nationality",
        "gender": "sex",
    }
    mrz_lines = mrz.get("mrz_lines") if isinstance(mrz, dict) else []
    for field, value in fields.items():
        if value in (None, "", []):
            continue
        source = "labelled_ocr"
        source_text = str(value)
        if isinstance(mrz_data, dict) and mrz_data.get(mrz_fields.get(field, "")):
            source = "mrz"
            source_text = "\n".join(str(line) for line in mrz_lines)
        page = next(
            (item for item in processed.pages if item.page == 1),
            None,
        )
        page_confidence = page.confidence if page else None
        evidence[field] = {
            "value": value,
            "source": source,
            "evidenceText": source_text[:500],
            "page": 1,
            "confidence": page_confidence,
            "documentType": document_type,
        }
    return evidence


def detect_document_type(text: str) -> tuple[str | None, list[str]]:
    """Classify text only when it contains a document title or multiple cues."""
    patterns = {
        "passport": (
            (r"\bpassport\b", True),
            (r"\b(?:passport|document)\s*(?:number|no\.?|#)\b", False),
            (r"\b(?:date of expiry|date of birth|nationality)\b", False),
        ),
        "national_id": (
            (r"\b(?:national\s+)?(?:identity|id)\s+card\b", True),
            (r"\b(?:national\s+id|national\s+identity)\b", True),
            (r"\b(?:national\s+id|identity)\s*(?:number|no\.?|#)\b", False),
        ),
        "bank_statement": (
            (r"\b(?:bank|account)\s+statement\b", True),
            (r"\b(?:opening|closing|available)\s+balance\b", False),
            (r"\b(?:debit|credit|transaction)\b", False),
        ),
        "employment_letter": (
            (r"\b(?:employment|employment verification)\s+(?:certificate|letter)\b", True),
            (r"\b(?:employer|job title|monthly salary)\b", False),
            (r"\b(?:employed|position|designation)\b", False),
        ),
        "salary_slip": (
            (r"\b(?:salary|pay)\s+(?:certificate|slip|statement)\b", True),
            (r"\b(?:net salary|gross salary|payroll)\b", False),
            (r"\b(?:employee|pay period|earnings)\b", False),
        ),
        "marriage_certificate": (
            (r"\bmarriage\s+certificate\b", True),
            (r"\b(?:spouse|husband|wife|groom|bride)\b", False),
            (r"\b(?:date of marriage|married on)\b", False),
        ),
        "birth_certificate": (
            (r"\bbirth\s+certificate\b", True),
            (r"\b(?:date|place) of birth\b", False),
            (r"\b(?:mother|father|parent)\b", False),
        ),
        "invitation_letter": (
            (r"\binvitation\s+(?:letter|certificate)\b", True),
            (r"\b(?:inviter|invitee|host|visitor)\b", False),
            (r"\b(?:invited|stay address|accommodation address)\b", False),
        ),
        "hotel_booking": (
            (r"\b(?:hotel|accommodation)\s+(?:booking|reservation|confirmation)\b", True),
            (r"\b(?:check[\s-]?in|check[\s-]?out)\b", False),
            (r"\b(?:guest|property|room)\b", False),
        ),
        "flight_booking": (
            (r"\b(?:flight|airline)\s+(?:booking|reservation|itinerary|ticket)\b", True),
            (r"\b(?:passenger|departure|arrival)\b", False),
            (r"\b(?:origin|destination|boarding)\b", False),
        ),
    }
    folded = str(text or "").casefold()
    scored: list[tuple[int, str, list[str]]] = []
    for document_type, cues in patterns.items():
        matched = [
            pattern
            for pattern, _ in cues
            if re.search(pattern, folded, re.I)
        ]
        has_title = any(
            is_title and re.search(pattern, folded, re.I)
            for pattern, is_title in cues
        )
        if has_title or len(matched) >= 2:
            scored.append((len(matched) + int(has_title), document_type, matched))
    if not scored:
        return None, []
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, []
    return scored[0][1], scored[0][2]


def _build_ocr_response(
    filename: str,
    requirement_code: str | None,
    processed: OCRDocument,
) -> OCRResponse:
    extracted_text = processed.text[:MAX_TEXT_LENGTH].strip()
    confidence = processed.confidence
    pages = [asdict(page) for page in processed.pages]
    failed_pages = processed.failed_pages or [
        {"page": page.page, "error": page.error or "OCR failed for this page."}
        for page in processed.pages
        if page.status == "OCR_FAILED"
    ]
    if processed.metrics.get("status") == "OCR_PARTIAL" and not failed_pages:
        raise OCRProcessingError(
            "OCR reported a partial result without identifying any failed pages."
        )
    is_partial = bool(failed_pages)
    metrics = {
        **processed.metrics,
        "page_count": len(processed.pages),
        "pages_processed": len(processed.pages),
        "failed_pages": failed_pages,
        "ocr_page_count": sum(page.method == "ocr" for page in processed.pages),
        "embedded_text_page_count": sum(
            page.method == "embedded_text" for page in processed.pages
        ),
    }

    if not extracted_text:
        return OCRResponse(
            filename=filename,
            extracted_text="",
            confidence=confidence,
            document_type=_normalize_document_type(requirement_code) or "unknown",
            status="partial" if is_partial else "manual_review",
            warnings=[
                "No readable text was extracted.",
                *(
                    [
                        "Some pages failed to process; this result is incomplete."
                    ]
                    if is_partial
                    else []
                ),
            ],
            pages=pages,
            failed_pages=failed_pages,
            metrics=metrics,
            message=(
                "OCR partially completed. Some pages could not be read; retry or review them manually."
                if is_partial
                else "No readable text was extracted. Please upload a clearer document."
            ),
        )

    logger.info(
        "OCR %s: filename=%s characters=%s failed_pages=%s",
        "partially completed" if is_partial else "completed",
        filename,
        len(extracted_text),
        len(failed_pages),
    )

    detected_type, type_evidence = detect_document_type(extracted_text)
    expected_type = _normalize_document_type(requirement_code)
    effective_type = detected_type or expected_type or "unknown"
    type_mismatch = bool(
        detected_type
        and expected_type
        and expected_type not in {"unknown", "other"}
        and detected_type != expected_type
    )
    fields = fallback_extract_fields(extracted_text, effective_type)
    mrz_result = (
        parse_mrz(extracted_text)
        if effective_type in {"passport", "national_id"}
        else None
    )
    fields = _merge_mrz_fields(fields, extracted_text, effective_type, mrz_result)
    if effective_type == "passport":
        passport_date = fields.get("dateOfBirth") or fields.get("passportDateOfBirth")
        if passport_date:
            fields.setdefault("passportDateOfBirth", passport_date)
        expiry_date = fields.get("expiryDate") or fields.get("passportExpiryDate")
        if expiry_date:
            fields.setdefault("passportExpiryDate", expiry_date)

    missing_fields = (
        get_missing_fields(fields, effective_type)
        if effective_type in DOCUMENT_REQUIRED_FIELDS
        else []
    )
    field_evidence = _field_evidence(fields, processed, mrz_result, effective_type)

    warnings = []
    if not fields:
        warnings.append("No structured fields were confidently extracted from the document.")
    if effective_type == "unknown":
        warnings.append("Document type could not be determined automatically; manual review is required.")
    if type_mismatch:
        warnings.append(
            "The detected document type differs from the selected requirement; human review is required."
        )
    if missing_fields:
        warnings.append("Some required document fields are missing.")
    if is_partial:
        failed_page_numbers = ", ".join(
            str(item.get("page")) for item in failed_pages
        )
        warnings.append(
            f"OCR failed on page(s) {failed_page_numbers}; the result is incomplete."
        )

    low_confidence_pages = [
        page
        for page in processed.pages
        if page.confidence is not None and page.confidence < 0.65
    ]
    if low_confidence_pages:
        warnings.append("Some OCR pages have low confidence and require manual verification.")

    needs_review = (
        effective_type == "unknown"
        or type_mismatch
        or bool(missing_fields)
        or not fields
        or bool(low_confidence_pages)
    )
    status = (
        "partial"
        if is_partial
        else "manual_review"
        if needs_review
        else "success"
    )
    required_fields = DOCUMENT_REQUIRED_FIELDS.get(effective_type, [])
    verification_score = (
        round(
            100
            * sum(
                fields.get(field) not in (None, "", [])
                for field in required_fields
            )
            / len(required_fields)
        )
        if required_fields and not is_partial
        else None
    )
    runtime = ocr_service.runtime_info

    return OCRResponse(
        filename=filename,
        extracted_text=extracted_text,
        confidence=confidence,
        document_type=effective_type,
        fields=fields,
        missing_fields=missing_fields,
        warnings=warnings,
        ai_analysis={
            "provider": runtime["engine"],
            "model": runtime["model"],
            "model_version": runtime["model_version"],
            "inference_device": runtime["device"],
            "document_type_confidence": None,
            "document_type_detection": {
                "source": "ocr_text" if detected_type else "requirement",
                "evidence": type_evidence,
                "requirement_type": expected_type or "unknown",
                "mismatch": type_mismatch,
            },
            "mrz": mrz_result,
            "fieldEvidence": field_evidence,
            "summary": "OCR extracted structured fields with rule-based validation and MRZ parsing; no external AI model is required.",
            "inconsistencies": [],
            "manual_review_required": needs_review or is_partial,
            "authenticity_verified": False,
        },
        pages=pages,
        failed_pages=failed_pages,
        metrics=metrics,
        field_evidence=field_evidence,
        document_verification_score=verification_score,
        document_verification_score_basis=(
            "Percentage of required document fields extracted; this is not a visa approval probability or authenticity result."
            if verification_score is not None
            else None
        ),
        status=status,
        message=(
            "OCR partially completed. Some pages could not be read; retry or review them manually."
            if is_partial
            else
            "OCR completed successfully."
            if status == "success"
            else "Document requires manual verification."
        ),
    )


# =========================================================
# OCR ENDPOINT
# =========================================================

@app.post(
    "/ocr/extract",
    response_model=OCRResponse,
)
async def extract_ocr(
    request: Request,
    file: UploadFile = File(...),
    requirement_code: str | None = Form(default=None),
):

    filename = Path(
        file.filename or "document"
    ).name[:255]

    logger.info(
        "Upload received: filename=%s content_type=%s",
        filename,
        file.content_type,
    )

    # Validate document type.
    if requirement_code:

        requirement_code = _normalize_document_type(requirement_code)

        if requirement_code not in DOCUMENT_TYPES:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid requirement_code: {requirement_code}"
                ),
            )

    # Read upload.
    try:

        data = await file.read(
            MAX_FILE_SIZE + 1
        )

    finally:
        await file.close()

    if not data:

        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    if len(data) > MAX_FILE_SIZE:

        raise HTTPException(
            status_code=413,
            detail=(
                f"Maximum upload size is {MAX_FILE_SIZE} bytes."
            ),
        )

    # Validate actual file signature.
    detected_type = detect_file_type(data)

    if detected_type is None:

        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported or invalid file. "
                "Upload a valid PDF, JPG or PNG file."
            ),
        )

    logger.info(
        "File validated: %s (%s bytes, detected=%s)",
        filename,
        len(data),
        detected_type,
    )

    cancellation_event = Event()
    worker = None
    disconnect_watcher = None

    async def watch_disconnect() -> None:
        while worker is not None and not worker.done():
            if await request.is_disconnected():
                cancellation_event.set()
                return
            await asyncio.sleep(0.25)

    def report_background_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Background OCR task failed after request ended: %s", error)

    # OCR.
    try:
        worker = asyncio.create_task(
            asyncio.to_thread(
                ocr_service.process_document,
                data,
                detected_type,
                cancellation_event,
            )
        )
        disconnect_watcher = asyncio.create_task(watch_disconnect())
        processed = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=ocr_service.process_timeout,
        )
    except asyncio.TimeoutError as exc:
        cancellation_event.set()
        logger.warning(
            "OCR timed out after %s seconds for filename=%s",
            ocr_service.process_timeout,
            filename,
        )
        raise HTTPException(
            status_code=504,
            detail=f"OCR processing exceeded {ocr_service.process_timeout} seconds.",
        ) from exc
    except asyncio.CancelledError:
        cancellation_event.set()
        raise
    except ValueError as exc:
        logger.warning(
            "Invalid document: %s",
            exc,
        )

        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except (OCRProcessingError, OCRCancelledError, TimeoutError) as exc:
        logger.exception("OCR engine failure")
        status_code = 409 if isinstance(exc, OCRCancelledError) else 503
        raise HTTPException(
            status_code=status_code,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected OCR failure")
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(exc)}",
        ) from exc
    finally:
        if disconnect_watcher is not None:
            disconnect_watcher.cancel()
            await asyncio.gather(disconnect_watcher, return_exceptions=True)
        if worker is not None and not worker.done():
            cancellation_event.set()
            worker.add_done_callback(report_background_failure)

    return _build_ocr_response(filename, requirement_code, processed)


def _public_ocr_job(job: dict[str, Any]) -> dict[str, Any]:
    error = job.get("error")
    if isinstance(error, str):
        error = {"code": "OCR_PROCESSING_ERROR", "message": error}
    payload = {
        "id": job["id"],
        "status": (
            "QUEUED" if job["status"] == "OCR_PENDING" else job["status"]
        ),
        "pagesProcessed": job["pagesProcessed"],
        "totalPages": job["totalPages"],
        "failedPages": job.get("failedPages") or [],
        "progressPercent": job["progressPercent"],
        "message": job["message"],
        "error": error,
        "result": job["result"],
    }
    return OCRJob.model_validate(payload).model_dump(mode="json")


def _purge_expired_ocr_jobs() -> None:
    cutoff = time.time() - 3600
    for job_id, job in list(ocr_jobs.items()):
        if job["updatedAt"] >= cutoff or job["status"] not in {
            "OCR_COMPLETED",
            "OCR_PARTIAL",
            "OCR_FAILED",
        }:
            continue
        ocr_jobs.pop(job_id, None)
        key = job.get("idempotencyKey")
        if key and ocr_job_keys.get(key) == job_id:
            ocr_job_keys.pop(key, None)


async def _run_ocr_job(
    job_id: str,
    data: bytes,
    file_type: str,
    filename: str,
    requirement_code: str | None,
) -> None:
    reported_total_pages = 0

    def update_progress(pages_processed: int, total_pages: int) -> None:
        nonlocal reported_total_pages
        with ocr_jobs_lock:
            if total_pages > 0:
                reported_total_pages = total_pages
            job = ocr_jobs.get(job_id)
            if not job or job["status"] in {
                "OCR_COMPLETED",
                "OCR_PARTIAL",
                "OCR_FAILED",
            }:
                return
            job.update(
                status="OCR_PROCESSING",
                pagesProcessed=pages_processed,
                totalPages=total_pages,
                progressPercent=round(
                    100 * pages_processed / total_pages
                )
                if total_pages
                else 0,
                message="Reading document...",
                updatedAt=time.time(),
            )

    cancellation_event = Event()
    try:
        with ocr_jobs_lock:
            job = ocr_jobs[job_id]
            job.update(
                status="OCR_PROCESSING",
                message="Reading document...",
                updatedAt=time.time(),
            )
        processed = await asyncio.wait_for(
            asyncio.to_thread(
                ocr_service.process_document,
                data,
                file_type,
                cancellation_event,
                update_progress,
            ),
            timeout=ocr_service.process_timeout,
        )
        result = _build_ocr_response(filename, requirement_code, processed).model_dump(
            mode="json"
        )
        pages = result.get("pages") or []
        total_pages = int(
            result.get("metrics", {}).get("page_count") or len(pages)
        )
        pages_processed = int(
            result.get("metrics", {}).get("pages_processed") or total_pages
        )
        failed_pages = result.get("failed_pages") or []
        page_numbers = sorted(
            int(page.get("page") or 0)
            for page in pages
            if isinstance(page, dict)
        )
        if (
            reported_total_pages < 1
            or total_pages != reported_total_pages
            or pages_processed != total_pages
            or len(pages) != total_pages
            or page_numbers != list(range(1, total_pages + 1))
        ):
            raise OCRProcessingError(
                "OCR did not return a result for every document page."
            )
        status = "OCR_PARTIAL" if failed_pages else "OCR_COMPLETED"
        failed_numbers = {
            int(item.get("page") or 0)
            for item in failed_pages
            if isinstance(item, dict)
        }
        page_failed_numbers = {
            int(page.get("page") or 0)
            for page in pages
            if isinstance(page, dict) and page.get("status") == "OCR_FAILED"
        }
        if failed_numbers != page_failed_numbers:
            raise OCRProcessingError(
                "OCR page failure details do not match the page results."
            )
        partial_message = (
            "OCR partially completed. Failed page(s): "
            + ", ".join(str(item["page"]) for item in failed_pages)
            + ". Retry or review the original document."
        )
        with ocr_jobs_lock:
            job = ocr_jobs[job_id]
            job.update(
                status=status,
                pagesProcessed=pages_processed,
                totalPages=total_pages,
                failedPages=failed_pages,
                progressPercent=round(100 * pages_processed / total_pages),
                message=(
                    partial_message
                    if failed_pages
                    else "OCR completed. Ready for verification."
                ),
                error=(
                    {"code": "OCR_PARTIAL", "message": partial_message}
                    if failed_pages
                    else None
                ),
                result=result,
                updatedAt=time.time(),
            )
    except Exception as exc:
        cancellation_event.set()
        logger.exception("OCR job failed: job_id=%s", job_id)
        with ocr_jobs_lock:
            job = ocr_jobs.get(job_id)
            if job:
                page_limit_error = isinstance(exc, PDFPageLimitError)
                error: dict[str, Any] = {
                    "code": (
                        "PDF_PAGE_LIMIT_EXCEEDED"
                        if page_limit_error
                        else "OCR_TIMEOUT"
                        if isinstance(exc, TimeoutError)
                        else "OCR_PROCESSING_ERROR"
                    ),
                    "message": str(exc) or "OCR processing failed.",
                }
                if page_limit_error:
                    error["totalPages"] = exc.total_pages
                    error["maximumAllowedPages"] = exc.maximum_pages
                job.update(
                    status="OCR_FAILED",
                    totalPages=reported_total_pages,
                    message="Document reading failed. Please retry.",
                    error=error,
                    result=None,
                    updatedAt=time.time(),
                )


@app.post("/ocr/jobs", status_code=202, response_model=OCRJobEnvelope)
async def create_ocr_job(
    request: Request,
    file: UploadFile = File(...),
    requirement_code: str | None = Form(default=None),
) -> OCRJobEnvelope:
    filename = Path(file.filename or "document").name[:255]
    requirement_code = (
        _normalize_document_type(requirement_code) if requirement_code else None
    )
    if requirement_code and requirement_code not in DOCUMENT_TYPES:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail=f"Invalid requirement_code: {requirement_code}",
        )
    try:
        data = await file.read(MAX_FILE_SIZE + 1)
    finally:
        await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Maximum upload size is {MAX_FILE_SIZE} bytes.",
        )
    file_type = detect_file_type(data)
    if file_type is None:
        raise HTTPException(
            status_code=415,
            detail="Unsupported or invalid file. Upload a valid PDF, JPG or PNG file.",
        )
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency key is too long.")

    with ocr_jobs_lock:
        _purge_expired_ocr_jobs()
        existing_job_id = ocr_job_keys.get(idempotency_key) if idempotency_key else None
        if existing_job_id and existing_job_id in ocr_jobs:
            return OCRJobEnvelope(
                job=OCRJob.model_validate(
                    _public_ocr_job(ocr_jobs[existing_job_id])
                )
            )
        job_id = str(uuid4())
        job = {
            "id": job_id,
            "status": "OCR_PENDING",
            "pagesProcessed": 0,
            "totalPages": 0,
            "failedPages": [],
            "progressPercent": 0,
            "message": "OCR job queued",
            "error": None,
            "result": None,
            "idempotencyKey": idempotency_key or None,
            "updatedAt": time.time(),
        }
        ocr_jobs[job_id] = job
        if idempotency_key:
            ocr_job_keys[idempotency_key] = job_id
        job["task"] = asyncio.create_task(
            _run_ocr_job(
                job_id,
                data,
                file_type,
                filename,
                requirement_code,
            )
        )
        return OCRJobEnvelope(
            job=OCRJob.model_validate(_public_ocr_job(job))
        )


@app.get("/ocr/jobs/{job_id}", response_model=OCRJobEnvelope)
async def get_ocr_job(job_id: str) -> OCRJobEnvelope:
    with ocr_jobs_lock:
        _purge_expired_ocr_jobs()
        job = ocr_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="OCR job not found.")
        return OCRJobEnvelope(job=OCRJob.model_validate(_public_ocr_job(job)))


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
    )

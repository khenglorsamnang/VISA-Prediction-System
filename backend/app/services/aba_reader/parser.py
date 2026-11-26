"""Coordinate-based ABA table extraction; never executes PDF content or follows links."""
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import threading
from datetime import datetime
from decimal import Decimal
import fitz

DATE = re.compile(r"\b([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})\b")
AMOUNT = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d{1,2})?)(?:\s*(USD|KHR))?(?![\w.])")
_rapid_engine = None
_ocr_lock = threading.Lock()


class ParseError(ValueError):
    pass


def cents(value):
    return int((Decimal(value.replace(",", ""))*100).to_integral_exact())


def parse_date(value):
    normalized=re.sub(r",\s*", ", ", re.sub(r"\s+", " ", value))
    return datetime.strptime(normalized, "%b %d, %Y").date().isoformat()


def ocr_engine():
    configured = os.environ.get("TESSERACT_CMD")
    candidates = [configured, shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  str(Path.home()/"AppData/Local/Programs/Tesseract-OCR/tesseract.exe")]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def words_for(page, force_ocr=False):
    native = page.get_text("words")
    if not force_ocr and len(native) >= 20:
        return [tuple(w[:5]) for w in native], "Text"
    engine = ocr_engine()
    if not engine:
        try:
            from rapidocr_onnxruntime import RapidOCR
            import numpy as np
            from PIL import Image
            global _rapid_engine
            with _ocr_lock:
                if _rapid_engine is None:
                    _rapid_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
                scale=2.5
                pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                result,_=_rapid_engine(np.array(Image.open(io.BytesIO(pix.tobytes("png")))))
            words=[]
            for box,text,confidence in result or []:
                # OCR returns text runs. Split proportionally into word boxes so column regions can be read.
                left=min(p[0] for p in box)/scale;right=max(p[0] for p in box)/scale
                top=min(p[1] for p in box)/scale;bottom=max(p[1] for p in box)/scale
                for match in re.finditer(r"\S+",text):
                    x=left+(right-left)*match.start()/max(1,len(text));x2=left+(right-left)*match.end()/max(1,len(text))
                    words.append((x,top,x2,bottom,match[0]))
            return words,"Local OCR"
        except ImportError as exc:
            raise ParseError("This page needs local OCR. Install rapidocr-onnxruntime, or Tesseract OCR, and restart. No document was sent externally.") from exc
    import pytesseract
    from PIL import Image
    pytesseract.pytesseract.tesseract_cmd = engine
    scale = 2.5
    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    langs = pytesseract.get_languages(config="")
    language = "eng+khm" if "khm" in langs else "eng"
    data = pytesseract.image_to_data(image, lang=language, config="--psm 6", output_type=pytesseract.Output.DICT, timeout=120)
    words = []
    for k,text in enumerate(data["text"]):
        if text.strip():
            x,y,w,h = [data[a][k]/scale for a in ("left","top","width","height")]
            words.append((x,y,x+w,y+h,text))
    return words, "Local OCR"


def lines(words):
    groups = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        group = next((g for g in reversed(groups[-4:]) if abs(g[0][1]-word[1]) < 3), None)
        if group is None:
            groups.append([word])
        else:
            group.append(word)
    return [sorted(g,key=lambda w:w[0]) for g in sorted(groups,key=lambda g:g[0][1])]


def parse_pdf(data, filename, password="", progress=None, cancelled=None, force_ocr=False):
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ParseError("This file could not be opened as a PDF.") from exc
    with doc:
        if doc.needs_pass and not doc.authenticate(password):
            raise ParseError("PASSWORD_REQUIRED: Enter the correct PDF password and upload again.")
        if not 1 <= len(doc) <= 300:
            raise ParseError("Upload a PDF with 1-300 pages.")
        s = {"filename": Path(filename).name, "holder": None, "account": None, "account_type": None,
             "currency": None, "bank": None, "start": None, "end": None, "opening": None,
             "ending": None, "reported_in": None, "reported_out": None, "transactions": [],
             "pages": len(doc), "page_sizes": [], "extraction_issues": [], "methods": [], "identity_notices": [],
             "direction": "ascending", "audit": [], "metadata_sources": {}}
        texts, fingerprints, page_numbers = [], set(), []
        for index,page in enumerate(doc):
            if cancelled and cancelled():
                raise ParseError("Processing cancelled.")
            words,method = words_for(page, force_ocr)
            grouped = lines(words)
            full = "\n".join(" ".join(w[4] for w in line) for line in grouped)
            # Native sorted text provides stable summary reading order; transactions remain coordinate based.
            text = page.get_text(sort=True) if method == "Text" else full
            texts.append(text)
            s["methods"].append(method)
            width,height = page.rect.width,page.rect.height
            s["page_sizes"].append([width,height])
            fingerprint = hashlib.sha256(full.encode()).hexdigest()
            if fingerprint in fingerprints:
                s["extraction_issues"].append({"code":"duplicate_page", "message":"Repeated page content requires review.", "page":index+1})
            fingerprints.add(fingerprint)
            page_no = re.search(r"\bPage\s+(\d+)\b",text)
            page_numbers.append(int(page_no[1]) if page_no else None)
            # Header positions allow small layout shifts while keeping amounts out of descriptions.
            header = next((line for line in grouped if "Date" in [w[4] for w in line] and any(w[4] == "Balance" for w in line)), None)
            table_top = min(w[1] for w in header)+10 if header else height*.08
            bands = [width*.52,width*.635,width*.77,width*.94]
            if header:
                mon = [w for w in header if w[4] == "Money"]
                bal = [w for w in header if w[4] == "Balance"]
                if len(mon)==2 and bal:
                    bands = [mon[0][0]-18, (mon[0][0]+mon[1][0])/2+13,
                             (mon[1][0]+bal[0][0])/2, min(width,bal[0][2]+12)]
            starts=[]
            for line in grouped:
                left = " ".join(w[4] for w in line if w[0]<width*.19)
                match=DATE.search(left)
                y=min(w[1] for w in line)
                if match and y>table_top:
                    starts.append((y,parse_date(match[1])))
            # Description continued from the preceding page, before this page's first dated row.
            if s["transactions"] and header and starts:
                cont=[w for w in words if table_top<w[1]<starts[0][0]-2 and width*.19<=w[0]<bands[0]]
                if cont:
                    s["transactions"][-1]["description"] += " " + " ".join(w[4] for line in lines(cont) for w in line)
            for row_index,(top,posting_date) in enumerate(starts):
                bottom=starts[row_index+1][0]-2 if row_index+1<len(starts) else height*.91
                region=[w for w in words if top-2<=w[1]<bottom]
                values=[]
                for lo,hi in zip(bands,bands[1:]):
                    zone=[w for w in region if lo<=w[0]<hi and w[1]<top+22]
                    raw=" ".join(w[4] for line in lines(zone) for w in line)
                    match=AMOUNT.search(raw)
                    values.append(cents(match[1]) if match else None)
                desc_words=[w for w in region if width*.19<=w[0]<bands[0]]
                description=" ".join(w[4] for line in lines(desc_words) for w in line if w[1]<height*.90)
                reference=re.search(r"\b(?:REF#|REF\s*#)\s*([A-Za-z0-9]+)",description)
                bbox=[width*.075,top-3,width*.94,min(bottom,height*.91)]
                t={"id":len(s["transactions"])+1, "date":posting_date,"description":description,
                   "money_in":values[0],"money_out":values[1],"balance":values[2],"reference":reference[1] if reference else None,
                   "page":index+1,"bbox":bbox,"method":method,"raw_amounts":values.copy(),"uncertain":any(v is None for v in values)}
                s["transactions"].append(t)
            if not header:
                s["extraction_issues"].append({"code":"table_header", "message":"Account activity table header was not recognized on this page.","page":index+1})
            if progress:
                progress(index+1,len(doc))
        first=texts[0]
        alltext="\n".join(texts)
        period_line=re.search(r"For\s+period:[^\n]*",first)
        dates=DATE.findall(period_line[0]) if period_line else []
        if len(dates)>=2:
            s["start"],s["end"]=map(parse_date,dates[:2])
            s["metadata_sources"]["start"]={"page":1,"raw":period_line[0]}
            s["metadata_sources"]["end"]={"page":1,"raw":period_line[0]}
        amount_fields = (
            ("opening", "Opening Balance", range(len(texts))),
            ("ending", "Ending Balance", range(len(texts))),
            ("reported_in", "Total Money In", range(len(texts))),
            ("reported_out", "Total Money Out", range(len(texts))),
        )
        for key,label,page_indexes in amount_fields:
            candidates=[]
            for page_index in page_indexes:
                for match in re.finditer(label+r"\s*[-+]?\s*([\d,]+(?:\.\d{1,2})?)\s*(USD|KHR)",texts[page_index],re.I):
                    candidates.append((page_index+1,match[0],cents(match[1])))
            unique_values={candidate[2] for candidate in candidates}
            if len(unique_values)>1:
                s["extraction_issues"].append({
                    "code":"summary_field_conflict",
                    "message":f"Conflicting {label.lower()} values were extracted from multiple pages.",
                    "field":key,
                    "pages":[candidate[0] for candidate in candidates],
                })
                s["metadata_sources"][key]={"candidates":[
                    {"page":page,"raw":raw,"value":value}
                    for page,raw,value in candidates
                ]}
            elif candidates:
                page,raw,value=candidates[-1]
                s[key]=value
                s["metadata_sources"][key]={"page":page,"raw":raw}
        def field(label):
            match=re.search(label+r"\s+([^\n]+)",first)
            return match[1].strip() if match else None
        s["holder"]=field("Account Holder Name")
        holder_match=re.search(r"Account Holder Name\s+([^\n]+)",first)
        if holder_match:
            s["metadata_sources"]["holder"]={"page":1,"raw":holder_match[0]}
        raw_account=field(r"Account No\.") or ""
        digits=re.sub(r"\D","",raw_account)
        s["account"]="•••• "+digits[-4:] if digits else None
        account_match=re.search(r"Account No\.\s+([^\n]+)",first)
        if account_match:
            s["metadata_sources"]["account"]={
                "page":1,
                "masked":s["account"],
                "label":"Account No.",
            }
        s["account_type"]=field("Account Type")
        currency=field("Account Currency")
        s["currency"]=currency.split()[0] if currency else None
        if currency:
            s["metadata_sources"]["currency"]={
                "page":1,
                "raw":re.search(r"Account Currency\s+[^\n]+",first)[0],
            }
        bank_match=re.search(r"\b(?:ABA\s+BANK|ADVANCED BANK OF ASIA(?:\s+LTD\.?)?)\b",alltext,re.I)
        if bank_match:
            s["bank"]="ABA Bank"
            for page_index,text in enumerate(texts):
                page_match=re.search(r"\b(?:ABA\s+BANK|ADVANCED BANK OF ASIA(?:\s+LTD\.?)?)\b",text,re.I)
                if page_match:
                    s["metadata_sources"]["bank"]={"page":page_index+1,"raw":page_match[0]}
                    break
        else:
            s["extraction_issues"].append({
                "code":"bank_name_unreadable",
                "message":"The bank name could not be identified from the statement text.",
            })
        if "ABA" not in alltext.upper() or "ACCOUNT STATEMENT" not in alltext.upper():
            s["extraction_issues"].append({"code":"unsupported_bank","message":"The document could not be identified as an ABA account statement."})
        if page_numbers != list(range(1,len(doc)+1)):
            s["extraction_issues"].append({"code":"page_sequence","message":"Printed page numbers are missing, repeated, or out of order."})
        if not re.search(r"Ending Balance",texts[-1]) or not re.search(r"Total\s+[\d,]+(?:\.\d{1,2})?\s*(?:USD|KHR)",texts[-1]):
            s["extraction_issues"].append({"code":"missing_final_summary","message":"The final activity summary could not be found; the PDF may be incomplete."})
        # Prove direction with all row balances, including same-day order, before normalizing.
        rows=s["transactions"]
        def valid_order(candidate):
            previous=s["opening"]
            if previous is None:return False
            for t in candidate:
                if any(t[k] is None for k in ("balance","money_in","money_out")):return False
                if previous+t["money_in"]-t["money_out"]!=t["balance"]:return False
                previous=t["balance"]
            return previous==s["ending"]
        if rows and any(a["date"]>b["date"] for a,b in zip(rows,rows[1:])):
            reversed_rows=list(reversed(rows))
            if all(a["date"]<=b["date"] for a,b in zip(reversed_rows,reversed_rows[1:])) and valid_order(reversed_rows):
                s["transactions"]=reversed_rows;s["direction"]="descending normalized using reconciliation"
        return s

"""Extract structured, selectable-text statement tables without guessing OCR values."""
import io
import re
import pdfplumber

from app.ingestion.column_mapper import ColumnMapper


class PDFParser:
    @staticmethod
    def parse(content: bytes):
        if not content.startswith(b"%PDF-"):
            raise ValueError("This file is not a valid PDF.")
        headers = []
        rows = []
        with pdfplumber.open(io.BytesIO(content)) as document:
            if len(document.pages) > 100:
                raise ValueError("PDF statements must contain at most 100 pages.")
            for page in document.pages:
                tables = page.extract_tables()
                if not tables:
                    tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
                for table in tables:
                    for cells in table:
                        cleaned = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in cells]
                        if not any(cleaned):
                            continue
                        mapping = ColumnMapper.auto_detect(cleaned)
                        if not ColumnMapper.validate_mapping(mapping, cleaned):
                            if headers and cleaned != headers:
                                continue
                            headers = cleaned
                            continue
                        if not headers or len(cleaned) != len(headers):
                            continue
                        row = dict(zip(headers, cleaned))
                        row["_row_number"] = len(rows) + 1
                        rows.append(row)
        if not headers or not rows:
            from app.ingestion.pdf_ai import extract_pdf
            return extract_pdf(content)
        return headers, rows

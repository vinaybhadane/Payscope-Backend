from unittest.mock import MagicMock, patch
import pytest
from app.ingestion.pdf_parser import PDFParser
from app.ingestion.import_service import ImportService
from app.ingestion.file_validator import FileValidator


def test_real_selectable_text_pdf_table():
    # A small in-memory PDF with a ruled statement table; exercise pdfplumber itself.
    stream = b"""BT /F1 10 Tf 60 746 Td (Date) Tj 150 0 Td (Description) Tj 200 0 Td (Amount) Tj ET
BT /F1 10 Tf 60 726 Td (2026-11-01) Tj 150 0 Td (Synthetic Plan) Tj 200 0 Td (199.00) Tj ET
50 760 m 500 760 l S 50 740 m 500 740 l S 50 720 m 500 720 l S
50 720 m 50 760 l S 200 720 m 200 760 l S 400 720 m 400 760 l S 500 720 m 500 760 l S
"""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream"]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(content)
    content += b"xref\n0 6\n0000000000 65535 f \n"
    content += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    content += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    headers, rows = PDFParser.parse(content)
    assert headers == ["Date", "Description", "Amount"]
    assert rows[0]["Amount"] == "199.00"
    assert rows[0]["Description"] == "Synthetic Plan"


def test_pdf_table_headers_repeated_across_pages():
    page = MagicMock()
    page.extract_tables.return_value = [[
        ["Date", "Description", "Amount"],
        ["2026-10-01", "Synthetic subscription", "199.00"],
        ["Date", "Description", "Amount"],
        ["2026-11-01", "Synthetic subscription", "199.00"],
    ]]
    document = MagicMock()
    document.__enter__.return_value.pages = [page]
    with patch("app.ingestion.pdf_parser.pdfplumber.open", return_value=document):
        kind, headers, rows = ImportService._parse_content("statement.pdf", b"%PDF-1.4")
    assert kind == "pdf"
    assert headers == ["Date", "Description", "Amount"]
    assert len(rows) == 2
    assert rows[1]["Amount"] == "199.00"


def test_pdf_requires_valid_signature():
    with pytest.raises(ValueError, match="valid PDF"):
        PDFParser.parse(b"not a PDF")


def test_scanned_pdf_returns_actionable_error():
    page = MagicMock()
    page.extract_tables.return_value = []
    document = MagicMock()
    document.__enter__.return_value.pages = [page]
    with patch("app.ingestion.pdf_parser.pdfplumber.open", return_value=document):
        with pytest.raises(ValueError, match="Scanned PDFs"):
            PDFParser.parse(b"%PDF-1.4")


def test_pdf_extension_accepted_with_synthetic_confirmation():
    assert FileValidator.validate_upload("statement.pdf", b"%PDF-1.4", True).is_valid
    assert not FileValidator.validate_upload("statement.pdf", b"%PDF-1.4", False).is_valid

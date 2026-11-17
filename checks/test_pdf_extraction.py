"""Offline PDF provider boundary and bank column checks."""
import json
import unittest
from unittest.mock import patch, Mock
from app.core.config import settings
from app.ingestion.pdf_ai import extract_pdf, _cache
from app.ingestion.import_service import ImportService
from app.ingestion.column_mapper import ColumnMapper
from app.ingestion.schema_normalizer import SchemaNormalizer


class PDFChecks(unittest.TestCase):
    def setUp(self):
        _cache.clear()

    def response(self, transactions, warnings=None, finish="STOP"):
        return Mock(ok=True, json=lambda: {"candidates": [{"finishReason": finish,
            "content": {"parts": [{"text": json.dumps({"transactions": transactions, "warnings": warnings or []})}]}}]})

    def transaction(self, **overrides):
        return dict(date="2026-11-03", description="Orbit Storage", amount="247.00", currency="INR",
            direction="DEBIT", page=1, **overrides)

    def test_pdf_preview_and_import_reuse_validated_extraction(self):
        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch("app.ingestion.pdf_ai.requests.post",
                return_value=self.response([self.transaction()])) as provider:
            headers, rows = extract_pdf(b"synthetic-pdf-test")
            assert rows[0]["Amount"] == "247.00"
            assert rows[0]["Currency"] == "INR"
            extract_pdf(b"synthetic-pdf-test")
            assert provider.call_count == 1

    def test_partial_extraction_is_not_published(self):
        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch("app.ingestion.pdf_ai.requests.post",
                return_value=self.response([self.transaction()], ["Unreadable row on page 2"])):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                extract_pdf(b"synthetic-pdf-test")

    def test_truncated_extraction_is_rejected(self):
        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch("app.ingestion.pdf_ai.requests.post",
                return_value=self.response([], finish="MAX_TOKENS")):
            with self.assertRaisesRegex(ValueError, "complete statement"):
                extract_pdf(b"synthetic-pdf-test")

    def test_separate_debit_credit_columns_preserve_direction(self):
        content = b"Date,Description,Debit,Credit,Balance\n2026-11-01,Orbit,247,,10000\n2026-11-02,Refund,,100,10100\n"
        _, headers, rows = ImportService._parse_content("statement.csv", content)
        normalized = SchemaNormalizer.normalize_transactions(rows, ColumnMapper.auto_detect(headers))
        assert not normalized.errors
        assert [str(r["amount"]) for r in normalized.valid_records] == ["247.00", "100.00"]
        assert [r["direction"].lower() for r in normalized.valid_records] == ["debit", "credit"]


if __name__ == "__main__":
    unittest.main()

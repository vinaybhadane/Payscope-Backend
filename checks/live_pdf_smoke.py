"""Opt-in live Gemini/Neo4j smoke check using only a disposable synthetic guest."""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import app


def run():
    if "--live" not in sys.argv:
        raise SystemExit("Pass --live to make real provider requests with synthetic data.")
    folder = Path(__file__).resolve().parents[2] / "tmp" / "pdfs"
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 950), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 28)
    lines = ["PAYSCOPE - SYNTHETIC BANK STATEMENT", "Currency: INR. Test data only.", "",
             "Date        Description             Debit     Credit    Balance",
             "2026-09-03  ORBIT CLOUD MONTHLY      247.00    -         9753.00",
             "2026-10-03  ORBIT CLOUD MONTHLY      247.00    -         9506.00",
             "2026-11-03  ORBIT CLOUD MONTHLY      247.00    -         9259.00",
             "2026-11-10  REFUND                   -         100.00    9359.00"]
    for i, line in enumerate(lines):
        draw.text((65, 70 + i * 65), line, font=font, fill="black")
    image.save(folder / "synthetic-statement-source.png")
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=(800, 475))
    canvas.drawImage(ImageReader(image), 0, 0, width=800, height=475)
    canvas.save()
    pdf = stream.getvalue()
    (folder / "synthetic-scanned-statement.pdf").write_bytes(pdf)
    with patch.object(settings, "RUN_MIGRATIONS", False), patch.object(settings, "EMAIL_WORKER_ENABLED", False), TestClient(app) as client:
        guest = client.post("/api/v1/auth/guest")
        guest.raise_for_status()
        auth = {"Authorization": "Bearer " + guest.json()["access_token"]}
        try:
            preview = client.post("/api/v1/imports/preview", headers=auth,
                files={"file": ("synthetic-scanned.pdf", pdf, "application/pdf")})
            assert preview.status_code == 200, preview.text
            data = preview.json()
            assert data["total_rows"] == 4, data
            assert data["total_debits"] == 741 and data["total_credits"] == 100, data
            imported = client.post("/api/v1/imports", headers=auth,
                files={"file": ("synthetic-scanned.pdf", pdf, "application/pdf")})
            assert imported.status_code == 201, imported.text
            refresh = imported.json()["workspace_refresh"]
            assert refresh["analysis_status"] == "completed", refresh
            result = client.get("/api/v1/mobile/dashboard", headers=auth)
            result.raise_for_status()
            dashboard = result.json()
            assert float(dashboard["monthly_recurring_spend"]) == 247, dashboard
            assert dashboard["active_subscriptions_count"] == 1, dashboard
            assert len(dashboard["historical_months"]) == 3, dashboard
            graph = client.get("/api/v1/graph/explorer", headers=auth)
            graph.raise_for_status()
            assert any(n["data"]["node_type"] == "Transaction" for n in graph.json()["nodes"])
            print(json.dumps({"pdf_rows": data["total_rows"], "debits": data["total_debits"],
                "credits": data["total_credits"], "ai_analysis": refresh["analysis_status"],
                "monthly_spend": dashboard["monthly_recurring_spend"],
                "subscriptions": dashboard["active_subscriptions_count"],
                "graph_sync": refresh["graph_status"], "graph_source": graph.json()["source"]}))
        finally:
            cleared = client.post("/api/v1/workspace/clear", headers=auth, json={"confirmation": "CLEAR"})
            assert cleared.status_code == 200, cleared.text
            print("Disposable synthetic workspace cleared.")


if __name__ == "__main__":
    run()

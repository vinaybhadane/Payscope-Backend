"""Opt-in direct PDF import check against the running local API, without preview."""
import io
import json
import sys
import requests
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors


def run():
    if "--live" not in sys.argv:
        raise SystemExit("Pass --live to test a generated PDF using real Gemini and the local API.")
    stream = io.BytesIO()
    canvas = Canvas(stream)
    rows = [["Date", "Description", "Amount", "Type", "Currency"]]
    for index in range(1, 11):
        rows.append([f"2026-11-{index:02}", f"One-off purchase reference {index}",
                     str(index * 100), "CREDIT" if index in (4, 9) else "DEBIT", "INR"])
    table = Table(rows, colWidths=[82, 210, 62, 65, 55])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                              ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    table.wrapOn(canvas, 500, 650)
    table.drawOn(canvas, 25, 500)
    canvas.save()
    base = "http://127.0.0.1:8000/api/v1"
    guest = requests.post(base + "/auth/guest", timeout=15)
    guest.raise_for_status()
    auth = {"Authorization": "Bearer " + guest.json()["access_token"]}
    try:
        imported = requests.post(base + "/imports", headers=auth,
            files={"file": ("synthetic-ten-records.pdf", stream.getvalue(), "application/pdf")}, timeout=600)
        imported.raise_for_status()
        record = imported.json()
        assert record["accepted_count"] == 10, record
        assert record["workspace_refresh"]["analysis_status"] == "completed", record
        status = requests.get(base + f'/imports/{record["import_id"]}/status', headers=auth, timeout=30)
        status.raise_for_status()
        assert status.json()["status"] == "completed"
        assert status.json()["accepted_count"] == 10
        snapshot = requests.get(base + "/mobile/dashboard", headers=auth, timeout=30)
        snapshot.raise_for_status()
        data = snapshot.json()
        assert data["commitments"] == [], data
        assert data["imported_transactions_count"] == 10
        assert len(data["recent_transactions"]) == 10
        assert data["statement_totals"] == [{"currency": "INR", "debits": 4200, "credits": 1300}]
        print(json.dumps({"saved_records": 10, "verified_persistence": status.json()["status"],
            "dashboard_records": len(data["recent_transactions"]), "recurring_subscriptions": len(data["commitments"]),
            "debits": 4200, "credits": 1300, "analysis_status": data["analysis_status"]}))
    finally:
        cleared = requests.post(base + "/workspace/clear", headers=auth, json={"confirmation": "CLEAR"}, timeout=30)
        cleared.raise_for_status()
        print("Disposable synthetic workspace cleared.")


if __name__ == "__main__":
    run()

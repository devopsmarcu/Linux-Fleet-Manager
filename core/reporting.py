from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict
from core.config import get_settings
from core.audit import AuditManager, AuditEntry

class ReportGenerator:
    """
    Gera relatórios consolidados a partir do histórico de auditoria.
    """
    def __init__(self) -> None:
        self.settings = get_settings()
        self.audit_manager = AuditManager()

    def aggregate_data(self) -> Dict[str, Any]:
        """Agrega dados do histórico para o relatório."""
        entries = self.audit_manager.get_history(limit=1000)
        if not entries:
            return {}

        total_ops = len(entries)
        success_ops = sum(1 for e in entries if e.status == "SUCCESS")
        failed_ops = sum(1 for e in entries if e.status == "FAILED")
        warning_ops = sum(1 for e in entries if e.status == "WARNING")

        total_success_hosts = sum(e.hosts_success for e in entries)
        total_failed_hosts = sum(e.hosts_failed for e in entries)

        return {
            "summary": {
                "total_operations": total_ops,
                "success_count": success_ops,
                "failed_count": failed_ops,
                "warning_count": warning_ops,
                "total_success_hosts": total_success_hosts,
                "total_failed_hosts": total_failed_hosts,
                "success_rate": f"{(success_ops/total_ops*100):.2f}%" if total_ops > 0 else "0%"
            },
            "history": [e.model_dump() for e in entries]
        }

    def export_json(self, data: Dict[str, Any], filename: str) -> Path:
        path = self.settings.reports_dir / filename
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, default=str)
        return path

    def export_csv(self, data: Dict[str, Any], filename: str) -> Path:
        path = self.settings.reports_dir / filename
        history = data.get("history", [])
        if not history:
            path.write_text("No data available")
            return path

        fields = ["timestamp", "user", "action", "targets", "status", "duration"]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(history)
        return path

    def export_html(self, data: Dict[str, Any], filename: str) -> Path:
        path = self.settings.reports_dir / filename
        summary = data.get("summary", {})
        history = data.get("history", [])

        rows = ""
        for e in history:
            color = "green" if e["status"] == "SUCCESS" else "red" if e["status"] == "FAILED" else "orange"
            rows += f"<tr><td>{e['timestamp']}</td><td>{e['action']}</td><td>{e['targets']}</td><td style='color: {color}'>{e['status']}</td></tr>"

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; margin: 40px; background: #f9f9f9; }}
                .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background: #eee; }}
                .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
                .stat-box {{ padding: 15px; background: #f0f7ff; border-radius: 5px; border-left: 5px solid #007bff; }}
            </style>
        </head>
        <body>
            <h1>LFM Fleet Audit Report</h1>
            <div class="card">
                <h2>Summary</h2>
                <div class="summary-grid">
                    <div class="stat-box"><strong>Total Ops:</strong> {summary.get('total_operations', 0)}</div>
                    <div class="stat-box"><strong>Success:</strong> {summary.get('success_count', 0)}</div>
                    <div class="stat-box"><strong>Failed:</strong> {summary.get('failed_count', 0)}</div>
                    <div class="stat-box"><strong>Success Rate:</strong> {summary.get('success_rate', '0%')}</div>
                </div>
            </div>
            <div class="card">
                <h2>Operation History</h2>
                <table>
                    <thead><tr><th>Timestamp</th><th>Action</th><th>Targets</th><th>Status</th></tr></thead>
                    <tbody>{rows}</tbody>
                </table>
            </div>
        </body>
        </html>
        """
        path.write_text(html, encoding="utf-8")
        return path

report_generator = ReportGenerator()

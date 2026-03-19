import json
import asyncio
import re
import shutil
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.app_factory import build_app_data
from backend.services.agent_engine import agent_engine
from backend.services.llm import llm_service
from backend.services.model_selection import best_available_gguf, clear_selected_model, load_selected_model, resolve_startup_model, save_selected_model
from backend.services.traffic_monitor import traffic_monitor
from backend.services.reminders import delete_reminder, list_reminders
from backend.routers import lan as lan_router
from backend.config import MODEL_SELECTION_PATH, TRAFFIC_SNAPSHOTS_DIR


class GeneratedReportAppTests(unittest.TestCase):
    slug = "test-report-app"
    app_dir = Path("generated_apps") / slug

    @classmethod
    def setUpClass(cls):
        cls.app_dir.mkdir(parents=True, exist_ok=True)
        (cls.app_dir / "manifest.json").write_text(json.dumps({
            "slug": cls.slug,
            "name": "Test Report App",
            "description": "Upload files and generate a report summary.",
            "context": "",
            "created_at": time.time(),
            "generation_time_s": 0.1,
            "status": "success",
            "errors": [],
            "has_backend": True,
            "has_frontend": True,
            "app_kind": "report_generator",
            "widget_count": 3,
        }, indent=2), encoding="utf-8")
        (cls.app_dir / "spec.json").write_text(json.dumps({
            "app_kind": "report_generator",
            "summary": "Upload files and generate a report summary.",
            "widgets": [],
        }, indent=2), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if cls.app_dir.exists():
            shutil.rmtree(cls.app_dir)

    def setUp(self):
        if (self.app_dir / "uploads").exists():
            shutil.rmtree(self.app_dir / "uploads")
        if (self.app_dir / "reports").exists():
            shutil.rmtree(self.app_dir / "reports")
        self._saved_llm_state = (
            llm_service._ready,
            llm_service._provider,
            llm_service._model_path,
            llm_service._llm,
        )
        self._saved_recent_frames = list(traffic_monitor._recent_frames)
        self._saved_last_scan = lan_router._last_scan_result
        self._saved_conversations = dict(traffic_monitor._conversations)
        self._saved_stats = dict(traffic_monitor._stats)
        self._created_agent_ids = []
        self._created_snapshot_ids = []
        self._created_reminder_ids = []
        self._saved_model_selection = MODEL_SELECTION_PATH.read_text(encoding="utf-8") if MODEL_SELECTION_PATH.exists() else None

    def tearDown(self):
        (
            llm_service._ready,
            llm_service._provider,
            llm_service._model_path,
            llm_service._llm,
        ) = self._saved_llm_state
        traffic_monitor._recent_frames = self._saved_recent_frames
        lan_router._last_scan_result = self._saved_last_scan
        traffic_monitor._conversations = self._saved_conversations
        traffic_monitor._stats = self._saved_stats
        for agent_id in self._created_agent_ids:
            agent = agent_engine.get(agent_id)
            if agent:
                if agent.status.value == 'running':
                    asyncio.run(agent_engine.stop(agent_id))
                agent_engine.remove(agent_id)
        for snapshot_id in self._created_snapshot_ids:
            traffic_monitor.delete_snapshot(snapshot_id)
        for reminder_id in self._created_reminder_ids:
            delete_reminder(reminder_id)
        if self._saved_model_selection is None:
            clear_selected_model()
        else:
            MODEL_SELECTION_PATH.write_text(self._saved_model_selection, encoding="utf-8")

    def _disable_llm(self):
        llm_service._ready = False
        llm_service._provider = "none"
        llm_service._model_path = None
        llm_service._llm = None

    def test_report_app_upload_generate_and_export(self):
        sample = (
            b"OPORD 2026-03-13\n"
            b"Mission: Harbor systems validation\n"
            b"Risks: NMEA feed intermittent on bridge repeater.\n"
            b"Decisions: Keep engineering monitor active through next watch.\n"
            b"Next actions: Validate GPS sentence integrity and brief duty section.\n"
        )

        with TestClient(app) as client:
            # Force deterministic fallback so test does not depend on model quality.
            self._disable_llm()

            upload = client.post(
                f"/api/apps/{self.slug}/files",
                files=[("files", ("opord.txt", sample, "text/plain"))],
            )
            self.assertEqual(upload.status_code, 200)
            self.assertEqual(upload.json()["saved"][0]["name"], "opord.txt")

            workspace = client.get(f"/api/apps/{self.slug}/workspace")
            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(len(workspace.json()["files"]), 1)

            report = client.post(
                f"/api/apps/{self.slug}/actions/report-summary",
                json={
                    "report_type": "daily_opord_summary",
                    "prompt": "Summarize the uploaded OPORD notes into a watch brief.",
                },
            )
            self.assertEqual(report.status_code, 200)
            payload = report.json()
            self.assertIn("Mission: Harbor systems validation.", payload["summary"])
            self.assertEqual(payload["recommended_actions"], ["Validate GPS sentence integrity and brief duty section."])
            self.assertTrue((self.app_dir / "reports" / "latest.json").exists())

            reports = client.get(f"/api/apps/{self.slug}/reports")
            self.assertEqual(reports.status_code, 200)
            self.assertEqual(len(reports.json()["reports"]), 1)

            latest_md = client.get(f"/api/apps/{self.slug}/reports/latest.md")
            self.assertEqual(latest_md.status_code, 200)
            self.assertIn("# Test Report App Summary", latest_md.text)
            self.assertIn("## Recommended Actions", latest_md.text)

    def test_report_summary_rejects_unsupported_only_files(self):
        with TestClient(app) as client:
            self._disable_llm()

            upload = client.post(
                f"/api/apps/{self.slug}/files",
                files=[("files", ("diagram.pdf", b"%PDF-1.7 fake", "application/pdf"))],
            )
            self.assertEqual(upload.status_code, 200)

            report = client.post(
                f"/api/apps/{self.slug}/actions/report-summary",
                json={"report_type": "uploaded_report", "prompt": ""},
            )
            self.assertEqual(report.status_code, 400)
            self.assertIn("not readable text formats", report.json()["detail"])

    def test_chat_returns_bounded_response_for_off_domain_request(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/chat/", json={
                "messages": [],
                "message": "Create my a daily to do list based on the actions I did not complete yesterday",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("provide", payload["response"].lower())
            self.assertIn("checklist", payload["response"].lower())

    def test_chat_can_create_recurring_reminder_action(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/chat/", json={
                "messages": [],
                "message": "Remind me every day at 0600 to review GPS integrity",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("created reminder", payload["response"].lower())
            reminders = list_reminders()
            self.assertTrue(reminders)
            self._created_reminder_ids.append(reminders[-1]["id"])

    def test_chat_can_create_todo_workspace_tasks(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/chat/", json={
                "messages": [],
                "message": "Create a todo list: validate GPS feed, brief engineering, review LAN scan",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("added 3 task", payload["response"].lower())
            match = re.search(r"/api/apps/([^/]+)/ui", payload["response"])
            self.assertIsNotNone(match)
            slug = match.group(1)
            workspace = client.get(f"/api/apps/{slug}/workspace")
            self.assertEqual(workspace.status_code, 200)
            titles = {task["title"] for task in workspace.json()["tasks"]}
            self.assertTrue({"Validate GPS feed", "Brief engineering", "Review LAN scan"}.issubset(titles))

    def test_report_app_can_delete_files_and_reports(self):
        sample = b"Mission: Test cleanup flow\nNext actions: Delete stale artifacts.\n"

        with TestClient(app) as client:
            self._disable_llm()
            client.post(
                f"/api/apps/{self.slug}/files",
                files=[("files", ("cleanup.txt", sample, "text/plain"))],
            )
            report = client.post(
                f"/api/apps/{self.slug}/actions/report-summary",
                json={"report_type": "uploaded_report", "prompt": ""},
            )
            self.assertEqual(report.status_code, 200)
            report_id = report.json()["id"]

            delete_file = client.delete(f"/api/apps/{self.slug}/files/cleanup.txt")
            self.assertEqual(delete_file.status_code, 200)
            self.assertFalse((self.app_dir / "uploads" / "cleanup.txt").exists())

            delete_report = client.delete(f"/api/apps/{self.slug}/reports/{report_id}")
            self.assertEqual(delete_report.status_code, 200)
            self.assertFalse((self.app_dir / "reports" / f"{report_id}.json").exists())

    def test_generate_app_succeeds_without_loaded_model(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/apps/generate", json={
                "name": "Offline Report Generator",
                "description": "Allow user to upload files and create a summary.",
                "context": "",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["app_kind"], "report_generator")
            generated_dir = Path("generated_apps") / payload["slug"]
            self.assertTrue((generated_dir / "manifest.json").exists())
            shutil.rmtree(generated_dir)

    def test_generate_app_normalizes_recommendation_style_metadata(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/apps/generate", json={
                "name": "High host density detected",
                "description": "High host density detected — consider creating a Ship Systems Overview dashboard",
                "context": "Multiple systems detected on ship LAN.",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["name"], "Ship Systems Overview")
            self.assertNotIn("High host density detected", payload["description"])
            generated_dir = Path("generated_apps") / payload["slug"]
            manifest = json.loads((generated_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "Ship Systems Overview")
            self.assertIn("operational picture", manifest["description"].lower())
            shutil.rmtree(generated_dir)

    def test_selected_model_preference_persists_and_resolves_on_startup(self):
        clear_selected_model()
        model_path = str((Path("models") / "test-llama3.gguf").resolve())
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        Path(model_path).write_bytes(b"gguf")
        save_selected_model(model_path, provider="gguf", n_gpu_layers=24)
        saved = load_selected_model()
        self.assertEqual(saved["model_path"], model_path)
        self.assertEqual(saved["provider"], "gguf")
        resolved = resolve_startup_model("gguf", "")
        self.assertEqual(resolved["model_path"], model_path)
        self.assertEqual(resolved["provider"], "gguf")
        self.assertEqual(resolved["source"], "saved")
        Path(model_path).unlink()

    def test_resolve_startup_model_falls_back_from_missing_saved_ollama_to_local_gguf(self):
        clear_selected_model()
        fallback_path = str((Path("models") / "fallback-test.gguf").resolve())
        Path(fallback_path).parent.mkdir(parents=True, exist_ok=True)
        Path(fallback_path).write_bytes(b"gguf-fallback")
        save_selected_model("llama3:latest", provider="ollama", n_gpu_layers=0)
        resolved = resolve_startup_model("gguf", "")
        self.assertEqual(resolved["provider"], "ollama")
        self.assertEqual(resolved["model_path"], "llama3:latest")
        self.assertTrue(best_available_gguf().endswith(".gguf"))
        self.assertTrue(Path(best_available_gguf()).exists())
        Path(fallback_path).unlink()

    def test_update_app_rewrites_manifest(self):
        with TestClient(app) as client:
            self._disable_llm()
            created = client.post("/api/apps/generate", json={
                "name": "Temp Dashboard",
                "description": "Show system status.",
                "context": "",
            })
            self.assertEqual(created.status_code, 200)
            slug = created.json()["slug"]

            updated = client.post(f"/api/apps/{slug}/update", json={
                "name": "Bridge Navigation Console",
                "description": "Show live GPS position, heading, speed, and depth from bridge feeds.",
                "context": "NMEA 0183 on 192.168.1.50:10110",
            })
            self.assertEqual(updated.status_code, 200)
            payload = updated.json()
            self.assertEqual(payload["slug"], slug)
            self.assertEqual(payload["name"], "Bridge Navigation Console")

            manifest = json.loads((Path("generated_apps") / slug / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "Bridge Navigation Console")
            self.assertEqual(manifest["app_kind"], "navigation_dashboard")
            self.assertIn("position", manifest["description"].lower())
            ui = client.get(f"/api/apps/{slug}/ui")
            self.assertEqual(ui.status_code, 200)
            self.assertIn(f"/?view=apps&edit={slug}", ui.text)
            shutil.rmtree(Path("generated_apps") / slug)

    def test_generate_lan_app_succeeds_without_loaded_model(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/apps/generate", json={
                "name": "LAN Investigator",
                "description": "Create a live LAN investigation workspace for network discovery, host analysis, and scan export.",
                "context": "",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["app_kind"], "lan_investigator")
            slug = payload["slug"]
            ui = client.get(f"/api/apps/{slug}/ui")
            self.assertEqual(ui.status_code, 200)
            self.assertIn("Run Scan", ui.text)
            self.assertIn("/api/lan/scan/export", ui.text)
            self.assertIn("Operational Footprint", ui.text)
            self.assertIn("Close", ui.text)
            shutil.rmtree(Path("generated_apps") / slug)

    def test_generate_navigation_app_succeeds_without_loaded_model(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/apps/generate", json={
                "name": "Navigation Dashboard",
                "description": "Create a live bridge navigation dashboard for GPS, heading, speed, and depth.",
                "context": "",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["app_kind"], "navigation_dashboard")
            slug = payload["slug"]
            ui = client.get(f"/api/apps/{slug}/ui")
            self.assertEqual(ui.status_code, 200)
            self.assertIn("Start Capture", ui.text)
            self.assertIn("Decoded Feed", ui.text)
            self.assertIn("NMEA / Bridge Systems", ui.text)
            shutil.rmtree(Path("generated_apps") / slug)

    def test_generate_task_workspace_and_manage_tasks(self):
        with TestClient(app) as client:
            self._disable_llm()
            response = client.post("/api/apps/generate", json={
                "name": "Daily Todo Generator",
                "description": "Create a daily todo list based on unfinished actions from yesterday.",
                "context": "Requested directly from the WarClaw AI Assistant.",
            })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["app_kind"], "task_workspace")
            slug = payload["slug"]

            ui = client.get(f"/api/apps/{slug}/ui")
            self.assertEqual(ui.status_code, 200)
            self.assertIn("Daily Task Board", ui.text)

            created = client.post(f"/api/apps/{slug}/tasks", json={
                "title": "Review incomplete maintenance actions",
                "notes": "Carry forward from previous watch.",
                "due_label": "Next watch",
            })
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task"]["id"]

            workspace = client.get(f"/api/apps/{slug}/workspace")
            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(len(workspace.json()["tasks"]), 1)

            updated = client.patch(f"/api/apps/{slug}/tasks/{task_id}", json={"completed": True})
            self.assertEqual(updated.status_code, 200)
            self.assertTrue(updated.json()["task"]["completed"])

            carry = client.post(f"/api/apps/{slug}/tasks/carry-forward")
            self.assertEqual(carry.status_code, 200)
            self.assertEqual(carry.json()["created"], 0)

            removed = client.delete(f"/api/apps/{slug}/tasks/{task_id}")
            self.assertEqual(removed.status_code, 200)
            shutil.rmtree(Path("generated_apps") / slug)

    def test_navigation_workspace_extracts_decoded_metrics(self):
        traffic_monitor._recent_frames = [
            {
                "timestamp": 1000.0,
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "content_type": "nmea",
                "payload_preview": "$GPRMC,...",
                "payload_decoded": {
                    "type": "RMC",
                    "checksum_ok": True,
                    "data": {
                        "type": "Navigation (RMC)",
                        "latitude": 36.9123,
                        "longitude": -76.1234,
                        "speed_knots": 12.4,
                        "course_deg": 182.5,
                        "status": "Active",
                    },
                },
            },
            {
                "timestamp": 1001.0,
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "content_type": "nmea",
                "payload_preview": "$HEHDT,...",
                "payload_decoded": {
                    "type": "HDT",
                    "checksum_ok": True,
                    "data": {
                        "type": "True Heading",
                        "heading_deg": 184.2,
                    },
                },
            },
            {
                "timestamp": 1002.0,
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "content_type": "nmea",
                "payload_preview": "$SDDBT,...",
                "payload_decoded": {
                    "type": "DBT",
                    "checksum_ok": True,
                    "data": {
                        "type": "Depth",
                        "depth_m": 18.7,
                    },
                },
            },
        ]
        lan_router._last_scan_result = {
            "network": "192.168.1.0/24",
            "hosts_up": 1,
            "scan_duration_s": 2.1,
            "recommendations": [],
            "hosts": [
                {
                    "ip": "192.168.1.50",
                    "hostname": "bridge-gps",
                    "services": [
                        {"port": 10110, "protocol": "nmea/iec61162"},
                        {"port": 20000, "protocol": "unknown"},
                    ],
                    "integration_hints": ["Navigation system detected at 192.168.1.50"],
                }
            ],
        }
        spec = {"app_kind": "navigation_dashboard", "summary": "Nav", "widgets": []}
        data = build_app_data(self.slug, spec)
        self.assertEqual(data["navigation"]["position"], "36.9123, -76.1234")
        self.assertEqual(data["navigation"]["heading_deg"], 184.2)
        self.assertEqual(data["navigation"]["speed_knots"], 12.4)
        self.assertEqual(data["navigation"]["depth_m"], 18.7)
        self.assertEqual(len(data["navigation_frames"]), 3)
        self.assertEqual(len(data["navigation_sources"]), 1)

    def test_build_app_data_returns_agent_list(self):
        spec = {"app_kind": "operational_dashboard", "summary": "Ops", "widgets": []}
        data = build_app_data(self.slug, spec)
        self.assertIsInstance(data["agents"], list)
        self.assertIn("total_agents", data["agent_summary"])

    def test_traffic_topology_and_recommendations(self):
        traffic_monitor._conversations = {
            "a": type("Conv", (), {
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "protocol": "NMEA",
                "packet_count": 120,
                "byte_count": 6400,
                "first_seen": 1.0,
                "last_seen": 5.0,
                "sample_payloads": [{"preview": "$GPRMC,..."}],
            })(),
            "b": type("Conv", (), {
                "src": "192.168.1.60:502",
                "dst": "192.168.1.20:41000",
                "protocol": "MODBUS",
                "packet_count": 48,
                "byte_count": 3200,
                "first_seen": 1.0,
                "last_seen": 5.0,
                "sample_payloads": [{"preview": "modbus frame"}],
            })(),
        }
        traffic_monitor._recent_frames = [
            {
                "content_type": "nmea",
                "payload_decoded": {"checksum_ok": False},
            }
        ]
        topology = traffic_monitor.get_topology()
        recs = traffic_monitor.get_recommendations()
        self.assertGreaterEqual(len(topology["nodes"]), 4)
        self.assertGreaterEqual(len(topology["edges"]), 2)
        titles = {item["title"] for item in recs}
        self.assertIn("Navigation feed available", titles)
        self.assertIn("Engineering telemetry detected", titles)
        self.assertIn("NMEA checksum anomalies observed", titles)
        snapshot = traffic_monitor.get_snapshot(recent_limit=20)
        self.assertIn("topology", snapshot)
        self.assertIn("recommendations", snapshot)
        self.assertIn("conversations", snapshot)
        self.assertIn("recent_frames", snapshot)

    def test_traffic_topology_falls_back_to_last_scan(self):
        traffic_monitor._conversations = {}
        lan_router._last_scan_result = {
            "network": "192.168.1.0/24",
            "hosts_up": 2,
            "hosts": [
                {
                    "ip": "192.168.1.50",
                    "hostname": "bridge-gps",
                    "services": [{"port": 10110, "protocol": "nmea/iec61162"}],
                },
                {
                    "ip": "192.168.1.60",
                    "hostname": "plant-plc",
                    "services": [{"port": 502, "protocol": "modbus"}],
                },
            ],
        }
        topology = traffic_monitor.get_topology()
        recs = traffic_monitor.get_recommendations()
        self.assertEqual(topology["source"], "scan")
        self.assertGreaterEqual(len(topology["nodes"]), 4)
        self.assertGreaterEqual(len(topology["edges"]), 2)
        titles = {item["title"] for item in recs}
        self.assertIn("Network map available from LAN scan", titles)
        self.assertIn("Navigation sources discovered", titles)

    def test_traffic_snapshot_persistence(self):
        traffic_monitor._conversations = {
            "a": type("Conv", (), {
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "protocol": "NMEA",
                "packet_count": 120,
                "byte_count": 6400,
                "first_seen": 1.0,
                "last_seen": 5.0,
                "sample_payloads": [{"preview": "$GPRMC,..."}],
            })(),
        }
        traffic_monitor._recent_frames = [{"content_type": "nmea", "payload_preview": "$GPRMC,..."}]
        snapshot = traffic_monitor.save_snapshot(recent_limit=20)
        self._created_snapshot_ids.append(snapshot["id"])
        self.assertTrue((TRAFFIC_SNAPSHOTS_DIR / f"{snapshot['id']}.json").exists())
        listing = traffic_monitor.list_snapshots()
        self.assertTrue(any(item["id"] == snapshot["id"] for item in listing))
        loaded = traffic_monitor.get_saved_snapshot(snapshot["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], snapshot["id"])

    def test_traffic_snapshot_brief_endpoints(self):
        traffic_monitor._conversations = {
            "a": type("Conv", (), {
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "protocol": "NMEA",
                "packet_count": 14,
                "byte_count": 2048,
                "first_seen": 1.0,
                "last_seen": 5.0,
                "sample_payloads": [{"preview": "$GPRMC,watch handover"}],
            })(),
        }
        traffic_monitor._recent_frames = [{
            "src": "192.168.1.50:10110",
            "dst": "192.168.1.10:58000",
            "protocol": "NMEA",
            "content_type": "nmea",
            "payload_preview": "$GPRMC,watch handover",
        }]
        traffic_monitor._stats["packets_captured"] = 14
        traffic_monitor._stats["bytes_captured"] = 2048
        traffic_monitor._stats["protocols"]["NMEA"] += 14

        with TestClient(app) as client:
            live_brief = client.get("/api/traffic/snapshot/brief.md")
            self.assertEqual(live_brief.status_code, 200)
            self.assertIn("# WarClaw Traffic Watch Brief", live_brief.text)
            self.assertIn("Top Service Paths", live_brief.text)
            self.assertIn("Recommendations", live_brief.text)

            saved = client.post("/api/traffic/snapshots")
            self.assertEqual(saved.status_code, 200)
            snapshot_id = saved.json()["id"]
            self._created_snapshot_ids.append(snapshot_id)

            saved_brief = client.get(f"/api/traffic/snapshots/{snapshot_id}/brief.md")
            self.assertEqual(saved_brief.status_code, 200)
            self.assertIn(snapshot_id, saved_brief.headers.get("content-disposition", ""))
            self.assertIn("192.168.1.50:10110 -> 192.168.1.10:58000", saved_brief.text)

    def test_traffic_advisor_agent_pushes_recommendations(self):
        traffic_monitor._conversations = {
            "a": type("Conv", (), {
                "src": "192.168.1.50:10110",
                "dst": "192.168.1.10:58000",
                "protocol": "NMEA",
                "packet_count": 120,
                "byte_count": 6400,
                "first_seen": 1.0,
                "last_seen": 5.0,
                "sample_payloads": [{"preview": "$GPRMC,..."}],
            })(),
        }
        traffic_monitor._recent_frames = []
        traffic_monitor._stats["packets_captured"] = 120

        async def run_agent():
            agent = agent_engine.deploy(
                agent_type='traffic_advisor',
                target_host='0.0.0.0',
                target_port=0,
                config={"analysis_interval_s": 0.01, "emit_recommendation_events": False},
            )
            self._created_agent_ids.append(agent.id)
            await agent_engine.start(agent.id)
            await asyncio.sleep(0.03)
            await agent_engine.stop(agent.id)
            return agent_engine.get(agent.id)

        agent = asyncio.run(run_agent())
        self.assertIsNotNone(agent)
        self.assertGreaterEqual(agent.frames_processed, 120)
        self.assertTrue(agent.recommendations)
        self.assertEqual(agent.recommendations[0]["title"], "Navigation feed available")


if __name__ == "__main__":
    unittest.main()

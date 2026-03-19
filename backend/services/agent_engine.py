"""
Agent Engine — autonomous agent lifecycle management and auto-recommendation.

Agents are persistent microservices that:
  1. Subscribe to DataBus channels (protocol streams)
  2. Process incoming frames (analyze, alert, aggregate, transform)
  3. Expose their own API endpoints for queries
  4. Run continuously until stopped

The engine auto-recommends agents based on LAN scan results — matching
discovered protocols/services to agent templates.
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..config import BASE_DIR, GENERATED_APPS_DIR
from .data_bus import DataBus, DataFrame, data_bus
from .mission_log import emit as log_event

AGENTS_STATE_PATH = BASE_DIR / "agents.json"

log = logging.getLogger("warclaw.agent_engine")


class AgentStatus(str, Enum):
    READY = "ready"          # Created but not started
    RUNNING = "running"      # Actively processing data
    STOPPED = "stopped"      # Manually stopped
    ERROR = "error"          # Crashed / unrecoverable


class AgentType(str, Enum):
    NAV_WATCH = "nav_watch"
    ENGINEERING_MONITOR = "engineering_monitor"
    WEATHER_STATION = "weather_station"
    SECURITY_MONITOR = "security_monitor"
    TRAFFIC_ADVISOR = "traffic_advisor"
    PROTOCOL_LOGGER = "protocol_logger"
    CUSTOM = "custom"


# ── Agent Template Registry ──────────────────────────────────────────────
AGENT_TEMPLATES: dict[str, dict] = {
    AgentType.NAV_WATCH: {
        "name": "Navigation Watch Agent",
        "description": (
            "Continuously monitors NMEA GPS, heading, depth, and speed data. "
            "Alerts on position drift, depth shoaling, SOG anomalies, and loss of fix. "
            "Maintains a rolling track log with COG/SOG history."
        ),
        "required_protocols": ["nmea", "nmea/iec61162", "nmea_candidate"],
        "required_sentences": ["GGA", "RMC", "HDT", "DBT"],
        "icon": "⚓",
        "category": "navigation",
        "priority": "critical",
        "config_schema": {
            "depth_alert_m": {"type": "float", "default": 5.0, "label": "Minimum depth alert (m)"},
            "position_drift_nm": {"type": "float", "default": 0.5, "label": "Position drift alert (NM)"},
            "sog_max_knots": {"type": "float", "default": 30.0, "label": "Max SOG alert (knots)"},
            "track_log_interval_s": {"type": "int", "default": 60, "label": "Track log interval (sec)"},
        },
    },
    AgentType.ENGINEERING_MONITOR: {
        "name": "Engineering Plant Monitor",
        "description": (
            "Monitors MODBUS registers from engineering plant sensors. "
            "Tracks temperature, pressure, RPM, and fluid levels. "
            "Alerts on values outside normal operating ranges."
        ),
        "required_protocols": ["modbus", "modbus_candidate"],
        "icon": "⚙",
        "category": "engineering",
        "priority": "high",
        "config_schema": {
            "poll_interval_s": {"type": "int", "default": 5, "label": "Poll interval (sec)"},
            "temp_alert_c": {"type": "float", "default": 95.0, "label": "High temp alert (°C)"},
            "register_start": {"type": "int", "default": 0, "label": "Start register"},
            "register_count": {"type": "int", "default": 32, "label": "Number of registers"},
        },
    },
    AgentType.WEATHER_STATION: {
        "name": "Weather Station Agent",
        "description": (
            "Aggregates wind speed/direction, water temperature, and barometric data "
            "from NMEA MWV/MTW/XDR sentences. Computes running averages and detects "
            "weather trend changes. Alerts on gale-force winds or rapid pressure drops."
        ),
        "required_protocols": ["nmea", "nmea/iec61162"],
        "required_sentences": ["MWV", "MTW"],
        "icon": "☁",
        "category": "weather",
        "priority": "medium",
        "config_schema": {
            "wind_alert_knots": {"type": "float", "default": 34.0, "label": "Gale alert (knots)"},
            "averaging_window_s": {"type": "int", "default": 300, "label": "Averaging window (sec)"},
        },
    },
    AgentType.SECURITY_MONITOR: {
        "name": "LAN Security Monitor",
        "description": (
            "Continuously scans for new hosts appearing on the ship LAN. "
            "Alerts on unknown devices, rogue access points, and port changes. "
            "Maintains a baseline of authorized hosts."
        ),
        "required_protocols": [],  # Works on any LAN
        "icon": "🛡",
        "category": "security",
        "priority": "critical",
        "config_schema": {
            "scan_interval_s": {"type": "int", "default": 300, "label": "Re-scan interval (sec)"},
            "alert_on_new_host": {"type": "bool", "default": True, "label": "Alert on new host"},
        },
    },
    AgentType.TRAFFIC_ADVISOR: {
        "name": "Traffic Intercept Advisor",
        "description": (
            "Continuously inspects captured traffic flows, identifies service-to-service relationships, "
            "and pushes actionable recommendations when new operational patterns appear."
        ),
        "required_protocols": [],
        "icon": "⬡",
        "category": "traffic",
        "priority": "high",
        "config_schema": {
            "analysis_interval_s": {"type": "int", "default": 15, "label": "Analysis interval (sec)"},
            "emit_recommendation_events": {"type": "bool", "default": True, "label": "Push mission log recommendations"},
        },
    },
    AgentType.PROTOCOL_LOGGER: {
        "name": "Protocol Data Logger",
        "description": (
            "Records all protocol traffic on a channel to disk for post-voyage analysis. "
            "Supports NMEA, MODBUS, and IEC 61162 with timestamped JSONL output. "
            "Configurable rotation and retention."
        ),
        "required_protocols": ["nmea", "modbus", "nmea/iec61162", "nmea_candidate", "modbus_candidate"],
        "icon": "📋",
        "category": "logging",
        "priority": "low",
        "config_schema": {
            "max_file_mb": {"type": "int", "default": 100, "label": "Max log file size (MB)"},
            "rotate_count": {"type": "int", "default": 10, "label": "Log file rotation count"},
        },
    },
}


@dataclass
class Agent:
    """A running or configured agent instance."""
    id: str
    agent_type: str
    name: str
    description: str
    icon: str
    category: str
    priority: str
    status: AgentStatus
    target_host: str
    target_port: int
    channel: str                       # DataBus channel to subscribe to
    config: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    frames_processed: int = 0
    alerts_fired: int = 0
    last_alert: Optional[str] = None
    recommendations: list[dict] = field(default_factory=list)
    error_message: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_type": self.agent_type,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "priority": self.priority,
            "status": self.status.value,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "channel": self.channel,
            "config": self.config,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "frames_processed": self.frames_processed,
            "alerts_fired": self.alerts_fired,
            "last_alert": self.last_alert,
            "recommendations": self.recommendations,
            "error_message": self.error_message,
            "uptime_s": round(time.time() - self.started_at) if self.started_at and self.status == AgentStatus.RUNNING else None,
        }


class AgentEngine:
    """Manages agent lifecycle: recommend, deploy, start, stop, monitor."""

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._load_state()

    # ── Persistence ──────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist non-running agent definitions to agents.json."""
        try:
            data = []
            for agent in self._agents.values():
                # Only save agents that should survive a restart (not errored)
                if agent.status == AgentStatus.ERROR:
                    continue
                data.append({
                    "id": agent.id,
                    "agent_type": agent.agent_type,
                    "name": agent.name,
                    "description": agent.description,
                    "icon": agent.icon,
                    "category": agent.category,
                    "priority": agent.priority,
                    "target_host": agent.target_host,
                    "target_port": agent.target_port,
                    "channel": agent.channel,
                    "config": agent.config,
                    "created_at": agent.created_at,
                    # Restore as STOPPED so operator re-starts intentionally
                    "status": AgentStatus.STOPPED.value,
                })
            AGENTS_STATE_PATH.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning("Failed to save agent state: %s", e)

    def _load_state(self) -> None:
        """Restore agent definitions from agents.json on startup."""
        if not AGENTS_STATE_PATH.exists():
            return
        try:
            data = json.loads(AGENTS_STATE_PATH.read_text())
            for entry in data:
                agent = Agent(
                    id=entry["id"],
                    agent_type=entry["agent_type"],
                    name=entry["name"],
                    description=entry["description"],
                    icon=entry["icon"],
                    category=entry["category"],
                    priority=entry["priority"],
                    status=AgentStatus.STOPPED,
                    target_host=entry["target_host"],
                    target_port=entry["target_port"],
                    channel=entry["channel"],
                    config=entry.get("config", {}),
                    created_at=entry.get("created_at", time.time()),
                )
                self._agents[agent.id] = agent
            log.info("Restored %d agent(s) from state file", len(data))
        except Exception as e:
            log.warning("Failed to load agent state: %s", e)

    @property
    def agents(self) -> list[Agent]:
        return list(self._agents.values())

    def get(self, agent_id: str) -> Optional[Agent]:
        return self._agents.get(agent_id)

    def recommend_agents(self, scan_result: dict) -> list[dict]:
        """
        Analyze a LAN scan result and recommend agents to deploy.

        Returns a list of recommendation dicts, each containing:
          - agent_type, template info, target host/port, reason
        """
        recommendations = []
        hosts = scan_result.get("hosts", [])
        seen_types: set[tuple[str, str]] = set()  # (agent_type, host_ip)

        for host in hosts:
            ip = host.get("ip", "")
            services = host.get("services", [])

            for service in services:
                protocol = service.get("protocol", "unknown")
                port = service.get("port", 0)

                for agent_type, template in AGENT_TEMPLATES.items():
                    req_protos = template.get("required_protocols", [])

                    # Check if this protocol matches the template
                    if req_protos and not any(protocol.startswith(rp) or protocol == rp for rp in req_protos):
                        continue

                    key = (agent_type, ip)
                    if key in seen_types:
                        continue
                    seen_types.add(key)

                    # Check if already deployed
                    already_deployed = any(
                        a.agent_type == agent_type and a.target_host == ip
                        for a in self._agents.values()
                        if a.status in (AgentStatus.RUNNING, AgentStatus.READY)
                    )

                    rec = {
                        "agent_type": agent_type,
                        "name": template["name"],
                        "description": template["description"],
                        "icon": template["icon"],
                        "category": template["category"],
                        "priority": template["priority"],
                        "target_host": ip,
                        "target_port": port,
                        "protocol": protocol,
                        "reason": _build_recommendation_reason(template, ip, port, protocol, service),
                        "already_deployed": already_deployed,
                        "config_schema": template.get("config_schema", {}),
                    }
                    recommendations.append(rec)

        # Always recommend Security Monitor if 3+ hosts found
        if len(hosts) >= 3:
            sec_key = (AgentType.SECURITY_MONITOR, "0.0.0.0")
            if sec_key not in seen_types:
                template = AGENT_TEMPLATES[AgentType.SECURITY_MONITOR]
                already = any(
                    a.agent_type == AgentType.SECURITY_MONITOR
                    for a in self._agents.values()
                    if a.status in (AgentStatus.RUNNING, AgentStatus.READY)
                )
                recommendations.append({
                    "agent_type": AgentType.SECURITY_MONITOR,
                    "name": template["name"],
                    "description": template["description"],
                    "icon": template["icon"],
                    "category": template["category"],
                    "priority": template["priority"],
                    "target_host": "0.0.0.0",
                    "target_port": 0,
                    "protocol": "lan",
                    "reason": f"{len(hosts)} hosts detected — deploy LAN security baseline monitoring",
                    "already_deployed": already,
                    "config_schema": template.get("config_schema", {}),
                })

        # Recommend the traffic advisor when there is enough network surface to monitor.
        if len(hosts) >= 2:
            template = AGENT_TEMPLATES[AgentType.TRAFFIC_ADVISOR]
            already = any(
                a.agent_type == AgentType.TRAFFIC_ADVISOR
                for a in self._agents.values()
                if a.status in (AgentStatus.RUNNING, AgentStatus.READY)
            )
            recommendations.append({
                "agent_type": AgentType.TRAFFIC_ADVISOR,
                "name": template["name"],
                "description": template["description"],
                "icon": template["icon"],
                "category": template["category"],
                "priority": template["priority"],
                "target_host": "0.0.0.0",
                "target_port": 0,
                "protocol": "traffic",
                "reason": "Multiple hosts are present on the LAN — deploy continuous traffic recommendations and topology monitoring.",
                "already_deployed": already,
                "config_schema": template.get("config_schema", {}),
            })

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recommendations.sort(key=lambda r: priority_order.get(r["priority"], 9))

        return recommendations

    def deploy(self, agent_type: str, target_host: str, target_port: int,
               config: dict | None = None, name_override: str | None = None,
               description: str | None = None, icon: str | None = None,
               category: str | None = None, priority: str | None = None) -> Agent:
        """Create and register an agent instance (does not start it yet)."""
        template = AGENT_TEMPLATES.get(agent_type)

        if not template and agent_type != AgentType.CUSTOM:
            raise ValueError(f"Unknown agent type: {agent_type}")

        # Support custom agents without a template
        if not template:
            template = {
                "name": name_override or "Custom Agent",
                "description": description or "User-defined custom monitoring agent.",
                "icon": icon or "⬡",
                "category": category or "custom",
                "priority": priority or "medium",
                "config_schema": {},
            }

        agent_id = f"{agent_type}-{uuid.uuid4().hex[:8]}"
        channel = f"{agent_type}:{target_host}:{target_port}"

        # Merge template defaults with user config
        merged_config = {}
        for key, schema in template.get("config_schema", {}).items():
            merged_config[key] = schema.get("default")
        if config:
            merged_config.update(config)

        agent = Agent(
            id=agent_id,
            agent_type=agent_type,
            name=name_override or template["name"],
            description=description or template["description"],
            icon=icon or template["icon"],
            category=category or template["category"],
            priority=priority or template["priority"],
            status=AgentStatus.READY,
            target_host=target_host,
            target_port=target_port,
            channel=channel,
            config=merged_config,
        )

        self._agents[agent_id] = agent
        self._save_state()
        log_event("success", "system",
                  f"Agent deployed: {agent.name} → {target_host}:{target_port}",
                  {"agent_id": agent_id, "type": agent_type})
        log.info("Agent deployed: %s (%s) → %s:%d", agent_id, agent.name, target_host, target_port)
        return agent

    async def start(self, agent_id: str) -> Agent:
        """Start an agent — it begins processing DataBus frames."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.status == AgentStatus.RUNNING:
            return agent

        agent.status = AgentStatus.RUNNING
        agent.started_at = time.time()
        agent.error_message = None

        # Launch the agent's processing loop as a background task
        agent._task = asyncio.create_task(self._run_agent(agent))

        log_event("success", "system",
                  f"Agent started: {agent.name}",
                  {"agent_id": agent_id})
        log.info("Agent started: %s", agent_id)
        return agent

    async def stop(self, agent_id: str) -> Agent:
        """Stop a running agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        if agent._task and not agent._task.done():
            agent._task.cancel()
            try:
                await agent._task
            except asyncio.CancelledError:
                pass

        agent.status = AgentStatus.STOPPED
        agent.stopped_at = time.time()
        self._save_state()

        log_event("info", "system",
                  f"Agent stopped: {agent.name}",
                  {"agent_id": agent_id})
        log.info("Agent stopped: %s", agent_id)
        return agent

    def remove(self, agent_id: str) -> bool:
        """Remove an agent (must be stopped first)."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        if agent.status == AgentStatus.RUNNING:
            raise ValueError("Cannot remove a running agent — stop it first")
        del self._agents[agent_id]
        self._save_state()
        return True

    async def _run_agent(self, agent: Agent) -> None:
        """
        Core agent processing loop. Subscribes to the DataBus and processes frames.

        Different agent types have different processing logic.
        """
        try:
            if agent.agent_type == AgentType.SECURITY_MONITOR:
                await self._run_security_agent(agent)
            elif agent.agent_type == AgentType.TRAFFIC_ADVISOR:
                await self._run_traffic_advisor(agent)
            elif agent.agent_type == AgentType.CUSTOM:
                await self._run_custom_agent(agent)
            elif agent.agent_type in (AgentType.NAV_WATCH, AgentType.WEATHER_STATION,
                                      AgentType.PROTOCOL_LOGGER, AgentType.ENGINEERING_MONITOR):
                await self._run_stream_agent(agent)
            else:
                await self._run_stream_agent(agent)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            agent.status = AgentStatus.ERROR
            agent.error_message = str(e)
            log_event("alert", "system",
                      f"Agent crashed: {agent.name} — {e}",
                      {"agent_id": agent.id, "error": str(e)})
            log.exception("Agent %s crashed", agent.id)

    async def _run_stream_agent(self, agent: Agent) -> None:
        """
        Generic stream agent — subscribes to a DataBus channel keyed by
        protocol:host:port and processes frames with agent-type-specific logic.
        """
        from ..protocols.nmea import parse_sentence
        import socket

        host = agent.target_host
        port = agent.target_port

        # If DataBus doesn't have this channel yet, we need to create our own
        # TCP reader and publish to the bus ourselves
        channel = f"raw:{host}:{port}"

        log.info("Agent %s starting stream ingestion from %s:%d", agent.id, host, port)
        log_event("info", "protocol",
                  f"{agent.name} connecting to {host}:{port}",
                  {"agent_id": agent.id})

        while agent.status == AgentStatus.RUNNING:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=10.0
                )
            except (OSError, asyncio.TimeoutError) as e:
                log_event("warn", "protocol",
                          f"{agent.name}: connection to {host}:{port} failed — retrying in 10s",
                          {"agent_id": agent.id, "error": str(e)})
                await asyncio.sleep(10)
                continue

            log_event("success", "protocol",
                      f"{agent.name}: connected to {host}:{port}",
                      {"agent_id": agent.id})

            try:
                while agent.status == AgentStatus.RUNNING:
                    try:
                        line = await asyncio.wait_for(reader.readline(), timeout=15.0)
                    except asyncio.TimeoutError:
                        continue
                    if not line:
                        break

                    text = line.decode("ascii", errors="ignore").strip()
                    agent.frames_processed += 1

                    # Parse and publish to data bus
                    payload: dict = {"raw": text}
                    if text.startswith("$"):
                        sentence = parse_sentence(text)
                        if sentence:
                            payload = {
                                "talker": sentence.talker,
                                "type": sentence.sentence_type,
                                "decoded": sentence.decoded,
                                "checksum_ok": sentence.checksum_ok,
                                "raw": text,
                            }
                            # Agent-specific alerting
                            self._check_agent_alerts(agent, sentence.sentence_type, sentence.decoded)

                    frame = DataFrame(
                        channel=channel,
                        protocol="nmea",
                        source_ip=host,
                        source_port=port,
                        payload=payload,
                        raw=text,
                    )
                    data_bus.publish(frame)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Agent %s stream error: %s — reconnecting", agent.id, e)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

            if agent.status == AgentStatus.RUNNING:
                log_event("warn", "protocol",
                          f"{agent.name}: connection lost to {host}:{port} — reconnecting in 5s",
                          {"agent_id": agent.id})
                await asyncio.sleep(5)

    async def _run_security_agent(self, agent: Agent) -> None:
        """Security agent — periodically re-scans the LAN and alerts on changes."""
        from .lan_monitor import scan_network

        interval = agent.config.get("scan_interval_s", 300)
        baseline_hosts: set[str] = set()
        first_scan = True

        while agent.status == AgentStatus.RUNNING:
            try:
                result = await scan_network()
                current_hosts = {h.ip for h in result.discovered}
                agent.frames_processed += 1

                if first_scan:
                    baseline_hosts = current_hosts
                    first_scan = False
                    log_event("info", "lan",
                              f"Security baseline established: {len(baseline_hosts)} host(s)",
                              {"agent_id": agent.id, "hosts": sorted(baseline_hosts)})
                else:
                    new_hosts = current_hosts - baseline_hosts
                    missing_hosts = baseline_hosts - current_hosts

                    if new_hosts and agent.config.get("alert_on_new_host", True):
                        agent.alerts_fired += 1
                        alert_msg = f"NEW HOST(S) DETECTED: {', '.join(sorted(new_hosts))}"
                        agent.last_alert = alert_msg
                        log_event("alert", "lan", alert_msg,
                                  {"agent_id": agent.id, "new_hosts": sorted(new_hosts)})

                    if missing_hosts:
                        agent.alerts_fired += 1
                        alert_msg = f"HOST(S) DISAPPEARED: {', '.join(sorted(missing_hosts))}"
                        agent.last_alert = alert_msg
                        log_event("warn", "lan", alert_msg,
                                  {"agent_id": agent.id, "missing_hosts": sorted(missing_hosts)})

                    # Update baseline with union
                    baseline_hosts = current_hosts

            except Exception as e:
                log.warning("Security scan failed: %s", e)

            await asyncio.sleep(interval)

    async def _run_traffic_advisor(self, agent: Agent) -> None:
        """Traffic advisor agent — periodically reviews traffic and pushes recommendations."""
        from .traffic_monitor import traffic_monitor

        interval = agent.config.get("analysis_interval_s", 15)
        emit_events = agent.config.get("emit_recommendation_events", True)
        previous_titles: set[str] = set()

        while agent.status == AgentStatus.RUNNING:
            try:
                recommendations = traffic_monitor.get_recommendations()
                topology = traffic_monitor.get_topology()
                agent.frames_processed = traffic_monitor.stats.get("packets_captured", 0)
                agent.recommendations = recommendations[:6]

                current_titles = {item["title"] for item in recommendations}
                new_titles = current_titles - previous_titles
                if new_titles and emit_events:
                    top = recommendations[0]
                    agent.alerts_fired += 1
                    agent.last_alert = top["title"]
                    log_event(
                        "info",
                        "traffic",
                        f"Traffic advisor update: {top['title']}",
                        {
                            "agent_id": agent.id,
                            "title": top["title"],
                            "rationale": top["rationale"],
                            "action": top["action"],
                            "node_count": len(topology.get("nodes", [])),
                            "edge_count": len(topology.get("edges", [])),
                        },
                    )
                previous_titles = current_titles
            except Exception as e:
                log.warning("Traffic advisor failed: %s", e)

            await asyncio.sleep(interval)

    async def _run_custom_agent(self, agent: Agent) -> None:
        """
        Custom agent — runs a lightweight monitoring loop.
        If target_host/port are set and non-zero, attempts TCP stream.
        Otherwise, operates as a passive agent that logs status periodically.
        """
        host = agent.target_host
        port = agent.target_port

        # If there's a real target, try to connect and stream
        if host and host != "0.0.0.0" and port > 0:
            await self._run_stream_agent(agent)
            return

        # Passive custom agent — periodic heartbeat
        log.info("Custom agent %s running in passive mode", agent.id)
        log_event("info", "system",
                  f"{agent.name} running in passive monitoring mode",
                  {"agent_id": agent.id})

        while agent.status == AgentStatus.RUNNING:
            agent.frames_processed += 1
            await asyncio.sleep(30)

    def _check_agent_alerts(self, agent: Agent, sentence_type: str, decoded: dict) -> None:
        """Check decoded NMEA data against agent alert thresholds."""
        if agent.agent_type == AgentType.NAV_WATCH:
            if sentence_type == "DBT":
                depth = decoded.get("depth_m")
                min_depth = agent.config.get("depth_alert_m", 5.0)
                if depth is not None and depth < min_depth:
                    agent.alerts_fired += 1
                    msg = f"SHOAL WATER: {depth}m (threshold: {min_depth}m)"
                    agent.last_alert = msg
                    log_event("alert", "protocol", msg, {"agent_id": agent.id, "depth_m": depth})

            if sentence_type == "RMC":
                speed = decoded.get("speed_knots")
                max_sog = agent.config.get("sog_max_knots", 30.0)
                if speed is not None and speed > max_sog:
                    agent.alerts_fired += 1
                    msg = f"SOG ALERT: {speed} kn (threshold: {max_sog} kn)"
                    agent.last_alert = msg
                    log_event("alert", "protocol", msg, {"agent_id": agent.id, "speed_knots": speed})

                status = decoded.get("status")
                if status == "Void":
                    agent.alerts_fired += 1
                    msg = "GPS FIX LOST — RMC status: Void"
                    agent.last_alert = msg
                    log_event("alert", "protocol", msg, {"agent_id": agent.id})

        elif agent.agent_type == AgentType.WEATHER_STATION:
            if sentence_type == "MWV":
                wind = decoded.get("wind_speed")
                threshold = agent.config.get("wind_alert_knots", 34.0)
                if wind is not None and wind > threshold:
                    agent.alerts_fired += 1
                    msg = f"HIGH WIND: {wind} kn (threshold: {threshold} kn)"
                    agent.last_alert = msg
                    log_event("alert", "protocol", msg, {"agent_id": agent.id, "wind_speed": wind})

    @property
    def summary(self) -> dict:
        """Dashboard summary of all agents."""
        agents_list = [a.to_dict() for a in self._agents.values()]
        running = sum(1 for a in self._agents.values() if a.status == AgentStatus.RUNNING)
        total_alerts = sum(a.alerts_fired for a in self._agents.values())
        return {
            "total_agents": len(self._agents),
            "running": running,
            "total_alerts": total_alerts,
            "agents": agents_list,
        }


def _build_recommendation_reason(template: dict, ip: str, port: int, protocol: str, service: dict) -> str:
    """Build a human-readable reason for recommending an agent."""
    name = template["name"]
    banner = service.get("banner", "")
    latency = service.get("latency_ms")

    parts = [f"{protocol.upper()} service detected at {ip}:{port}"]
    if banner:
        parts.append(f"banner: {banner[:60]}")
    if latency:
        parts.append(f"{latency}ms latency")
    parts.append(f"→ deploy {name}")
    return " — ".join(parts)


# Module-level singleton
agent_engine = AgentEngine()

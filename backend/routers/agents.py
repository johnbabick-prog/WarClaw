"""
Agent Management endpoints — deploy, start, stop, recommend, and monitor agents.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.agent_engine import agent_engine, AgentStatus, AGENT_TEMPLATES
from ..services.data_bus import data_bus
from ..services.mission_log import emit as log_event

log = logging.getLogger("warclaw.agents")
router = APIRouter(prefix="/api/agents", tags=["agents"])


# ── Models ────────────────────────────────────────────────────────────────

class DeployRequest(BaseModel):
    agent_type: str
    target_host: str
    target_port: int = 0
    config: dict = {}
    name: str = ""
    description: str = ""
    icon: str = ""
    category: str = ""
    priority: str = ""
    auto_start: bool = True


class ConfigUpdateRequest(BaseModel):
    config: dict


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/")
def list_agents():
    """List all deployed agents with their current status."""
    return agent_engine.summary


@router.get("/templates")
def list_templates():
    """List all available agent templates."""
    templates = []
    for agent_type, tmpl in AGENT_TEMPLATES.items():
        templates.append({
            "agent_type": agent_type,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "icon": tmpl["icon"],
            "category": tmpl["category"],
            "priority": tmpl["priority"],
            "required_protocols": tmpl.get("required_protocols", []),
            "config_schema": tmpl.get("config_schema", {}),
        })
    return {"templates": templates}


@router.post("/recommend")
async def recommend_agents(scan_result: dict):
    """
    Given a LAN scan result, return recommended agents to deploy.

    Pass the full JSON response from /api/lan/scan as the request body.
    """
    recommendations = agent_engine.recommend_agents(scan_result)
    return {
        "count": len(recommendations),
        "recommendations": recommendations,
    }


@router.get("/recommend/auto")
async def auto_recommend():
    """
    Run a fresh LAN scan and automatically generate agent recommendations.
    """
    from ..services.lan_monitor import scan_network

    log_event("info", "system", "Running auto-recommendation LAN scan")
    result = await scan_network()

    # Convert to the dict format the recommender expects
    scan_dict = {
        "hosts": [
            {
                "ip": h.ip,
                "hostname": h.hostname,
                "services": [
                    {"port": s.port, "protocol": s.protocol,
                     "banner": s.banner, "latency_ms": s.latency_ms}
                    for s in h.services
                ],
            }
            for h in result.discovered
        ]
    }

    recommendations = agent_engine.recommend_agents(scan_dict)
    log_event("success", "system",
              f"Auto-recommend complete: {len(recommendations)} agent(s) suggested "
              f"from {result.hosts_up} host(s)",
              {"count": len(recommendations)})

    return {
        "scan_summary": {
            "network": result.network,
            "hosts_up": result.hosts_up,
            "scan_duration_s": result.scan_duration_s,
        },
        "count": len(recommendations),
        "recommendations": recommendations,
    }


@router.post("/deploy")
async def deploy_agent(req: DeployRequest):
    """Deploy a new agent from a template. Optionally auto-start it."""
    try:
        agent = agent_engine.deploy(
            agent_type=req.agent_type,
            target_host=req.target_host,
            target_port=req.target_port,
            config=req.config or None,
            name_override=req.name or None,
            description=req.description or None,
            icon=req.icon or None,
            category=req.category or None,
            priority=req.priority or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.auto_start:
        agent = await agent_engine.start(agent.id)

    return agent.to_dict()


@router.post("/{agent_id}/start")
async def start_agent(agent_id: str):
    """Start a deployed agent."""
    try:
        agent = await agent_engine.start(agent_id)
        return agent.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """Stop a running agent."""
    try:
        agent = await agent_engine.stop(agent_id)
        return agent.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{agent_id}")
async def remove_agent(agent_id: str):
    """Remove a stopped agent."""
    agent = agent_engine.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.RUNNING:
        await agent_engine.stop(agent_id)
    try:
        agent_engine.remove(agent_id)
        return {"status": "removed", "agent_id": agent_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{agent_id}/config")
async def update_agent_config(agent_id: str, req: ConfigUpdateRequest):
    """Update an agent's configuration (thresholds, intervals, etc.)."""
    agent = agent_engine.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.config.update(req.config)
    agent_engine._save_state()
    log_event("info", "system",
              f"Agent config updated: {agent.name}",
              {"agent_id": agent_id, "config": agent.config})
    return agent.to_dict()


@router.get("/{agent_id}")
def get_agent(agent_id: str):
    """Get detailed status of a single agent."""
    agent = agent_engine.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.to_dict()


@router.get("/bus/stats")
def data_bus_stats():
    """Return DataBus statistics — channels, frame counts, subscriber counts."""
    return data_bus.stats

"""拓扑查询与重载路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.topology.service import load_topology, topology_dict

router = APIRouter(prefix="/api/v1", tags=["topology"])


@router.get("/topology")
def get_topology() -> dict[str, Any]:
    return topology_dict()


@router.post("/topology/reload")
def reload_topology() -> dict[str, Any]:
    load_topology(force=True)
    return topology_dict()

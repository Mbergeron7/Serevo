"""
connectors — pluggable API connectors for external WFM platforms
================================================================

Each connector knows how to talk to a specific WFM system's API and
normalize the response into Serevo's internal data shapes:

  - Employees  (roster with skills, planning unit, contract info)
  - Forecasts  (offered calls + AHT per interval)
  - Schedules  (shift blocks per employee per day)
  - Requirements (agents required per interval — derived from forecasts)

Connectors are registered in REGISTRY by provider name (matching
APIConnection.provider). The get_connector() factory returns the right
one for a given APIConnection record.
"""

from app.connectors.base import BaseConnector
from app.connectors.registry import get_connector, REGISTRY

__all__ = ["BaseConnector", "get_connector", "REGISTRY"]

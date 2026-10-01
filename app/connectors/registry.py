"""
connectors/registry — maps provider names to connector classes
==============================================================
"""

from app.connectors.wfm_legacy import WFMLegacyConnector

# Provider name (matches APIConnection.provider) → connector class
REGISTRY = {
    "wfm_legacy": WFMLegacyConnector,
    "injixo": WFMLegacyConnector,         # backwards compat for existing connections
}


def get_connector(connection):
    """
    Return an instantiated connector for the given APIConnection record.
    Raises ValueError if the provider is not supported.
    """
    cls = REGISTRY.get(connection.provider)
    if cls is None:
        raise ValueError(
            f"No connector registered for provider '{connection.provider}'. "
            f"Available: {', '.join(sorted(REGISTRY))}"
        )
    return cls(connection)

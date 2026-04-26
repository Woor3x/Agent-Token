"""Mock A2A Gateway service.

Routes ``POST /a2a/invoke`` to the upstream agent named in the
``X-Target-Agent`` header. Peer URLs are configured via env vars
``AGENT_<NAME>_URL`` (e.g. ``AGENT_DATA_AGENT_URL=http://data-agent:8101``).
"""

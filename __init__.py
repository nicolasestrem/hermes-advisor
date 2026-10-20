"""Hermes Advisor public plugin entry."""


def register(*args, **kwargs):
    """Load and forward to the host implementation only when invoked."""
    from .advisor_host import register as host_register

    return host_register(*args, **kwargs)

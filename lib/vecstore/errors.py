class Unavailable(RuntimeError):
    """The vector layer cannot answer (no daemon, no model, disabled, timeout). Callers fall back to keyword search."""

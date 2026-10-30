"""Log status transitions once, retaining repeated observations at DEBUG."""
import logging


def log_transition(logger, owner, key, state, level, message, *args):
    states = getattr(owner, "_logged_states", None)
    if not isinstance(states, dict):
        states = {}
        owner._logged_states = states
    repeated = key in states and states[key] == state
    if not repeated:
        if len(states) >= 128 and key not in states:
            states.pop(next(iter(states)))
        states[key] = state
    logger.log(logging.DEBUG if repeated else level, message, *args)


def clear_transition(owner, key):
    states = getattr(owner, "_logged_states", None)
    if isinstance(states, dict):
        states.pop(key, None)

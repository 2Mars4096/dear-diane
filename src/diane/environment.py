"""Canonical DIANE_* configuration with backwards-compatible DAN_* consumers."""
import os


def apply_environment_aliases(environment=None):
    env = os.environ if environment is None else environment
    for key, value in list(env.items()):
        if key.startswith('DIANE_'):
            env['DAN_' + key[len('DIANE_'):]] = value
    return env

"""Normalize DNS names without guessing a registrable domain."""

import re

from certbot import errors


def normalize_name(name: str) -> str:
    try:
        name = name.strip().encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise errors.PluginError("Invalid DNS name") from exc
    if name.endswith("."):
        name = name[:-1]
    if (
        not name
        or len(name) > 253
        or any(
            not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
            for label in name.split(".")
        )
    ):
        raise errors.PluginError("Expected a valid non-wildcard DNS name")
    return name

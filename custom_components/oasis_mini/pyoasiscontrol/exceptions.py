"""Exceptions."""


class UnauthenticatedError(Exception):
    """Unauthenticated."""


class CommandRejectedError(Exception):
    """The device explicitly rejected a command."""

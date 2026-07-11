class BadCellClassException(Exception):
    pass


class UnknownBackendException(Exception):
    """A hash names a content backend that isn't known/registered here."""


class CellUnavailable(Exception):
    """A hash could not be resolved to content: it isn't in the local repo, and
    either no daemon is running to fetch it or no peer provided it in time."""


class ArtifactPinMismatch(Exception):
    """An artifact's bytes do not hash to the pin its cell's nucleus declares.

    Raised when a blob in the cell dir fails verification and cannot be
    replaced (publish refuses to spread it), or when the cell's own recipe
    builds something other than what the nucleus pins -- in which case the
    cell is lying about itself and nothing downstream should run it."""

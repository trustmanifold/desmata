class BadCellClassException(Exception):
    pass


class UnknownBackendException(Exception):
    """A hash names a content backend that isn't known/registered here."""

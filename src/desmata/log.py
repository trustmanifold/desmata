import logging
from desmata.lower_protocols import Loggers, LogListener, LogMatcher, LogCallback, LogSubject
from rich.console import Console
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler

class LoggersBase(Loggers):

    proc: logging.Logger
    msg: logging.Logger

    def specialize(self, name: str) -> Loggers:
        self.proc = self.proc.getChild(name)
        self.msg = self.msg.getChild(name)
        return self

    def get(self, subject: LogSubject):
        match(subject):
            case LogSubject.proc:
                return self.proc
            case LogSubject.msg:
                return self.msg
                

class TestLoggers(LoggersBase, Loggers):
    def __init__(self):
        self.proc = logging.getLogger("test.proc")
        self.msg = logging.getLogger("test.msg")
        for logger in [self.proc, self.msg]:
            logger.propagate = False
            logger.addHandler(
                RichHandler(
                    show_time=False,
                    highlighter=NullHighlighter(),
                    console=Console(stderr=True),
                    show_path=True,
                    show_level=True,
                )
            )
            logger.setLevel(logging.DEBUG)

class CliLoggers(LoggersBase, Loggers):
    """Loggers for real CLI invocations (writes to stderr via rich)."""

    def __init__(self, verbose: bool = False):
        self.proc = logging.getLogger("desmata.proc")
        self.msg = logging.getLogger("desmata.msg")
        level = logging.DEBUG if verbose else logging.INFO
        for logger in [self.proc, self.msg]:
            # avoid accumulating handlers if constructed more than once in-process
            logger.handlers.clear()
            logger.propagate = False
            logger.addHandler(
                RichHandler(
                    show_time=False,
                    highlighter=NullHighlighter(),
                    console=Console(stderr=True),
                    show_path=False,
                    show_level=True,
                )
            )
            logger.setLevel(level)


class LocalCallbackLogListener(LogListener):

    log: Loggers

    def __init__(self, log: Loggers):
        self.log = log

    registered: dict[str, tuple[logging.Logger, logging.StreamHandler]] = {}

    def register(self, key: str, subject: LogSubject, matcher: LogMatcher, callback: LogCallback):

        class CustomHandler(logging.StreamHandler):
            def emit(self, record):
                callback(record)

        handler = CustomHandler()
        logger = self.log.get(subject)
        self.registered[key] = (logger, handler)
        logger.addHandler(handler)


    def unregister(self, key: str):
        logger, handler = self.registered[key]
        logger.removeHandler(handler)

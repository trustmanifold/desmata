from pathlib import Path
from injector import Binder, Injector, Module, singleton
from pytest import fixture

from desmata.log import TestLoggers, LocalCallbackLogListener
from desmata.lower_protocols import Loggers, LogListener, LogSubject


@fixture
def components(tmp_path: Path) -> Injector:
    class TestContext(Module):
        def configure(self, binder: Binder) -> None:
            log = TestLoggers()
            listener = LocalCallbackLogListener(log=log)
            binder.bind(Loggers, to=log, scope=singleton)
            binder.bind(LogListener, to=listener, scope=singleton)
    return Injector([TestContext])

def test_local_files(components: Injector):
    loggers = components.get(Loggers)
    listener = components.get(LogListener)

    events = []
    def callback(msg):
        events.append(msg)

    def matcher(msg):
        return True

    listener.register("foo", subject=LogSubject.msg, callback=callback, matcher=matcher)

    loggers.msg.info("here's a foo")

    listener.unregister("foo")
    loggers.msg.info("here's a bar")

    assert len(events) == 1
    print(events)



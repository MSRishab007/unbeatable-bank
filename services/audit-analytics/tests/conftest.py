"""PyFlink starts a JVM for the mini-cluster. When Python shuts down after the tests, that JVM
can crash ("Segmentation fault (core dumped)") even though every test passed, which fails CI.
So once pytest has printed its report, leave at once with pytest's own exit status and skip the
interpreter shutdown that triggers the crash. A failing test still fails the job."""

import os
import sys

_status = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _status["code"] = int(exitstatus)


def pytest_unconfigure(config):
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_status["code"])
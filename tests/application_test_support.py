"""Release resources held by an application loaded from a disposable copy."""

import logging
import sys


def unload_test_application():
    application = sys.modules.pop("main", None)
    handler = getattr(application, "_server_log_handler", None)
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()

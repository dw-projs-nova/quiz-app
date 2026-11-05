from controllers.shared import main

import controllers.admin  # noqa: F401
import controllers.auth  # noqa: F401
import controllers.student # noqa: F401
import controllers.instructor  # noqa: F401
import controllers.testing  # noqa: F401

__all__ = ["main"]
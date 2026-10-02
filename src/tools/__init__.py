"""
Importing this package populates the tool registry - every tools/*.py module
that defines tools calls registry.register() at its own module-load time, so
this file only needs to import each of them once. Add a new import line here
when a new tools/*.py module is created; forgetting to would make its tools
silently invisible to tools_for() rather than raise an error -
tests/test_tool_registry.py checks specific expected tool names are present
after import to catch exactly that class of mistake.
"""
from src.tools import core  # noqa: F401
from src.tools import pilot  # noqa: F401
from src.tools import batch2  # noqa: F401
from src.tools import batch3  # noqa: F401
from src.tools import batch4  # noqa: F401
from src.tools import batch5  # noqa: F401
from src.tools import batch6  # noqa: F401
from src.tools import batch9  # noqa: F401
from src.tools import batch10  # noqa: F401
from src.tools import batch12  # noqa: F401
from src.tools import batch13  # noqa: F401
from src.tools import batch14  # noqa: F401
from src.tools import batch15  # noqa: F401
from src.tools import batch16  # noqa: F401
from src.tools import batch17  # noqa: F401

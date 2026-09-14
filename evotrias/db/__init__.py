"""Filesystem-backed state under ``.evotrias/db``.

Keep this ``__init__`` free of imports: ``evotrias.logger`` reads
``evotrias.db.paths`` and ``evotrias.db.base`` imports the logger, so pulling
``base`` in here would close a cycle.
"""

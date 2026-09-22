"""Filesystem-backed state under ``.evotrias/db``.

Keep this ``__init__`` free of imports: ``evotrias.logger`` reads
``evotrias.db.paths`` and ``evotrias.db.base`` reaches the logger through
``evotrias.utils.io``, so pulling ``base`` in here would close a cycle.
"""

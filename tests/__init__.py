"""Marks ``tests`` as a regular package.

The package marker keeps ``from tests.<helper> import ...`` bound to this
repository's ``tests`` directory even when another ``tests`` package is already
importable in the interpreter. It is not a test module.
"""

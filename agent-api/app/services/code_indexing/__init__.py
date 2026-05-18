"""Code indexing package — split out of the legacy ``code_indexer`` module."""

# Phase 2: SCIP indexer integration — exports kept lazy so importing the
# package does not pull in protobuf bindings when SCIP is not in use.
__all__ = ["load_scip_index", "ScipBinaryMissing", "ScipSymbol"]


def __getattr__(name: str):
    if name in __all__:
        from app.services.code_indexing import scip_loader
        return getattr(scip_loader, name)
    raise AttributeError(f"module 'app.services.code_indexing' has no attribute {name!r}")

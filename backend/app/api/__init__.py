"""HTTP layer.

One module per resource, each exposing an `APIRouter` that `app.main` mounts
under /api/v1. Routers validate input, call application logic and shape the
response; no scoring or ranking happens here.

This file deliberately imports nothing from its own submodules -- they import
shared dependencies from `app.api.deps`, and re-exporting them here would make
that circular.
"""

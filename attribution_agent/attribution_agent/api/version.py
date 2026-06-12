from __future__ import annotations
import os

__version__ = "2.0.0"


def build_info() -> dict:
    return {
        "version": __version__,
        "git_sha": os.environ.get("GIT_SHA", "dev"),
        "deploy_target": os.environ.get("DATABRICKS_BUNDLE_TARGET", "local"),
    }

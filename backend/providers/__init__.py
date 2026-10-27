"""Picks the configured provider for each step."""
from .. import config
from . import fal, mock


def image_provider():
    return {"fal": fal, "mock": mock}[config.IMAGE_PROVIDER]


def bg_provider():
    return {"fal": fal, "mock": mock, "none": None}[config.BG_PROVIDER]


def mesh_provider():
    if config.MESH_PROVIDER == "hf_space":
        from . import hf_space

        return hf_space
    return {"fal": fal, "mock": mock}[config.MESH_PROVIDER]

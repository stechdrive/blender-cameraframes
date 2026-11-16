# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition-preserving paper and animation shooting guides for Blender."""
bl_info = {
    'name': 'Camera Frames',
    'author': 'Camera Frames contributors',
    'version': (0, 1, 18),
    'blender': (5, 2, 0),
    'location': '3D View > Sidebar > Camera Frames',
    'description': 'Preserve camera composition, edit shooting guides and export layered PSD',
    'category': 'Camera',
}

from . import translations, properties, camera, presets, creation, operators, interaction, ui, overlay

MODULES = (translations, properties, camera, presets, creation, operators, interaction, ui, overlay)


def register():
    registered = []
    try:
        for module in MODULES:
            module.register()
            registered.append(module)
    except Exception:
        for module in reversed(registered):
            module.unregister()
        raise


def unregister():
    operators.cancel_edits()
    for module in reversed(MODULES):
        module.unregister()

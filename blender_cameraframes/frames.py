# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared FRAME selection and numeric group transforms, without observers."""
from math import isfinite
from . import geometry


def selected_indices(settings):
    return [i for i, frame in enumerate(settings.frames) if frame.selected and frame.visible]


def active_index(settings):
    index = settings.get('frame_index', 0)
    if 0 <= index < len(settings.frames):
        frame = settings.frames[index]
        if frame.selected and frame.visible:
            return index
    return next((i for i, frame in enumerate(settings.frames) if frame.selected and frame.visible), -1)


def active(settings):
    index = active_index(settings)
    return settings.frames[index] if index >= 0 else None


def select(settings, indices, *, active=None):
    wanted = {i for i in indices if 0 <= i < len(settings.frames) and settings.frames[i].visible}
    for i, frame in enumerate(settings.frames):
        frame['selected'] = i in wanted
    settings['frame_index'] = active if active in wanted else min(wanted, default=-1)


def select_row(settings, index):
    # Native list rows select one FRAME. Checkboxes explicitly extend selection.
    if index < 0:
        select(settings, ())
    elif index < len(settings.frames) and settings.frames[index].visible:
        select(settings, (index,), active=index)


def get_selected(frame):
    return bool(frame.get('selected', False)) and frame.visible


def set_selected(frame, value):
    frame['selected'] = bool(value and frame.visible)
    settings = frame.id_data.cf_camera
    if frame['selected']:
        settings['frame_index'] = next((i for i, f in enumerate(settings.frames) if f == frame), -1)
    else:
        settings['frame_index'] = active_index(settings)


def value(settings, name):
    frame = active(settings)
    if frame is None:
        return (0.5, 0.5, 0.5) if name == 'color' else 0.0
    if name == 'height':
        return frame.width * settings.ratio_y / settings.ratio_x
    return getattr(frame, name)


def set_value(settings, name, target):
    """Use the active FRAME's value as the reference for one group transform."""
    indices = selected_indices(settings)
    frame = active(settings)
    if not indices or frame is None:
        return
    if name == 'color':
        if all(isfinite(v) for v in target):
            for i in indices:
                settings.frames[i].color = target
        return
    if not isfinite(target):
        return
    if name in {'x', 'y'}:
        operation = 'MOVE'
        change = target - getattr(frame, name)
        args = {'delta': (change, 0) if name == 'x' else (0, change)}
    elif name in {'width', 'height'}:
        operation = 'SCALE'
        args = {'factor': target / value(settings, name)}
    elif name == 'angle':
        operation = 'ROTATE'
        args = {'turn': target - frame.angle}
    else:
        return
    fields = ('x', 'y', 'width', 'angle', 'handle_in', 'handle_out')
    saved = [{k: getattr(settings.frames[i], k) for k in fields} for i in indices]
    pivot = geometry.pivot(frame, settings.ratio_x / settings.ratio_y)
    try:
        values = geometry.transform_frames(saved, operation, pivot, **args)
    except ValueError:
        # Native numeric fields retain the last valid value at a group limit.
        return
    for i, values_for_frame in zip(indices, values):
        for key, new_value in values_for_frame.items():
            setattr(settings.frames[i], key, new_value)

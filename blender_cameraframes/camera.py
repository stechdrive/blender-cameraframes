# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit transactions only: no dependency-graph, timer or render callbacks."""
import json
import math
import uuid
import bpy

PROJECTION = ('type', 'lens', 'sensor_fit', 'sensor_width', 'sensor_height', 'shift_x', 'shift_y', 'ortho_scale')
RENDER = ('resolution_x', 'resolution_y', 'resolution_percentage', 'pixel_aspect_x', 'pixel_aspect_y')


def dump(group):
    out = {}
    for prop in group.bl_rna.properties:
        name = prop.identifier
        if name == 'rna_type' or prop.is_skip_save:
            continue
        value = getattr(group, name)
        if prop.type == 'COLLECTION':
            out[name] = [dump(item) for item in value]
        elif prop.type != 'POINTER':
            out[name] = list(value) if getattr(prop, 'is_array', False) else value
    return out


def load(group, values):
    for name, value in values.items():
        prop = group.bl_rna.properties.get(name)
        if not prop or name == 'rna_type' or prop.is_skip_save:
            continue
        if prop.type == 'COLLECTION':
            items = getattr(group, name)
            items.clear()
            for data in value:
                load(items.add(), data)
        elif prop.type != 'POINTER':
            setattr(group, name, value)


def active(scene):
    state = scene.cf_scene
    if 0 <= state.active_index < len(state.cameras):
        obj = state.cameras[state.active_index].camera
        if obj and obj.type == 'CAMERA' and scene.objects.get(obj.name) == obj:
            return obj
    return None


def check_camera(obj):
    if not obj or obj.type != 'CAMERA':
        raise ValueError('カメラオブジェクトを選択してください')
    if obj.library or obj.override_library or obj.data.library or obj.data.override_library:
        raise ValueError('ローカルのカメラを使用してください（リンク・オーバーライドは未対応）')
    if len(obj.users_scene) > 1:
        raise ValueError('複数シーンで共有されたカメラです。先にオブジェクトを複製してください')
    if obj.data.type not in {'PERSP', 'ORTHO'}:
        raise ValueError('透視または正投影のカメラを使用してください')


def check_resize(scene, obj):
    check_camera(obj)
    if obj.data.users > 1:
        raise ValueError('Cameraデータが共有されています。登録解除して再登録すると独立します')
    if obj.data.animation_data:
        raise ValueError('Cameraデータにアニメーションがあります。用紙変更には静的な投影設定が必要です')
    if not synced(scene, obj):
        raise ValueError('シーンの出力設定が異なります。「保存設定を適用」を先に実行してください')


def synced(scene, obj):
    s, r = obj.cf_camera, scene.render
    return (scene.camera == obj and r.resolution_x == s.width and r.resolution_y == s.height and
            r.resolution_percentage == s.percentage and abs(r.pixel_aspect_x - s.aspect_x) < 1e-6 and
            abs(r.pixel_aspect_y - s.aspect_y) < 1e-6)


def apply(scene, obj):
    s, r = obj.cf_camera, scene.render
    scene.camera = obj
    r.resolution_x, r.resolution_y, r.resolution_percentage = s.width, s.height, s.percentage
    r.pixel_aspect_x, r.pixel_aspect_y = s.aspect_x, s.aspect_y


def projection(obj):
    return {key: getattr(obj.data, key) for key in PROJECTION}


def bounds(scene, obj):
    points = obj.data.view_frame(scene=scene)
    if obj.data.type == 'PERSP':
        points = [p / -p.z for p in points]
    return min(p.x for p in points), max(p.x for p in points), min(p.y for p in points), max(p.y for p in points)


def check_frame_focal(scene, obj):
    check_camera(obj)
    if obj.data.type != 'PERSP':
        raise ValueError('35mm換算の指定は透視投影のカメラで使用してください')
    if obj.data.users > 1:
        raise ValueError('Cameraデータが共有されています。登録解除して再登録すると独立します')
    if obj.data.animation_data:
        raise ValueError('Cameraデータにアニメーションまたはドライバーがあります。静的なカメラで適用してください')
    if not synced(scene, obj):
        raise ValueError('シーンの出力設定が異なります。「保存設定を適用」を先に実行してください')
    s = obj.cf_camera
    if not s.frames:
        raise ValueError('撮影FRAMEを追加してください')


def set_frame_focal(scene, obj, target, *, clamp=False):
    """Change the actual lens through the FRAME's width-based equivalent."""
    from . import geometry
    check_frame_focal(scene, obj)
    s = obj.cf_camera
    if not math.isfinite(target) or target <= 0:
        raise ValueError('目標35mm換算値には正の数を指定してください')
    frame = s.frames[min(s.frame_index, len(s.frames) - 1)]
    current = geometry.frame_lens(frame, s.ratio_x / s.ratio_y, bounds(scene, obj), s.width, s.height)[0]
    if not math.isfinite(current) or current <= 0:
        raise ValueError('FRAMEの画角を計算できません')
    old = obj.data.lens
    value = old * target / current
    prop = obj.data.bl_rna.properties['lens']
    if clamp and math.isfinite(value):
        value = max(prop.hard_min, min(prop.hard_max, value))
        target = current * value / old
    if not math.isfinite(value) or not prop.hard_min <= value <= prop.hard_max:
        raise ValueError('目標値はBlenderの焦点距離の対応範囲外です')
    if value == old:
        return old
    try:
        obj.data.lens = value
        actual = geometry.frame_lens(frame, s.ratio_x / s.ratio_y, bounds(scene, obj), s.width, s.height)[0]
        if not math.isfinite(actual) or abs(actual - target) > max(1e-4, target * 1e-5):
            raise ValueError('目標値に合わせられませんでした。カメラ設定を確認してください')
    except Exception:
        obj.data.lens = old
        raise
    return obj.data.lens


def snapshot(scene, obj):
    return {'settings': dump(obj.cf_camera), 'projection': projection(obj),
            'render': {key: getattr(scene.render, key) for key in RENDER}}


def restore(scene, obj, state):
    load(obj.cf_camera, state['settings'])
    for key, value in state['projection'].items():
        setattr(obj.data, key, value)
    for key, value in state['render'].items():
        setattr(scene.render, key, value)


def set_baseline(obj):
    s = obj.cf_camera
    s.origin = (0, 0)
    s.baseline = json.dumps({'projection': projection(obj), 'width': s.width, 'height': s.height,
                             'percentage': s.percentage, 'aspect_x': s.aspect_x, 'aspect_y': s.aspect_y})


def register_camera(scene, obj):
    check_camera(obj)
    entries = scene.cf_scene.cameras
    for i, entry in enumerate(entries):
        if entry.camera == obj:
            scene.cf_scene.active_index = i
            apply(scene, obj)
            return obj
    s, r = obj.cf_camera, scene.render
    if not s.uid and not (4 <= r.resolution_x <= 30000 and 4 <= r.resolution_y <= 30000 and 1 <= r.resolution_percentage <= 100):
        raise ValueError('用紙は4～30000px、出力倍率は1～100%に対応しています')
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    # Native duplicates retain settings, but must receive a fresh identity/baseline.
    copied = bool(s.uid) and any(o != obj and o.type == 'CAMERA' and o.cf_camera.uid == s.uid for o in bpy.data.objects)
    if not s.uid:
        s.width, s.height, s.percentage = r.resolution_x, r.resolution_y, r.resolution_percentage
        s.aspect_x, s.aspect_y = r.pixel_aspect_x, r.pixel_aspect_y
        f = s.frames.add()
        f.name, f.x, f.y = 'FRAME A', s.width / 2, s.height / 2
        f.width, f.selected = min(s.width * 0.8, s.height * 0.8 * 16 / 9), True
        set_baseline(obj)
    if not s.uid or copied:
        s.uid = uuid.uuid4().hex
    if copied:
        set_baseline(obj)
    entries.add().camera = obj
    scene.cf_scene.active_index = len(entries) - 1
    apply(scene, obj)
    return obj


def copy_actions(owner, created):
    anim = owner.animation_data
    if not anim:
        return
    copies = {}
    def independent(action):
        if action not in copies:
            copies[action] = action.copy()
            created.append(copies[action])
        return copies[action]
    if anim.action:
        slot = anim.action_slot.identifier if anim.action_slot else None
        anim.action = independent(anim.action)
        if slot:
            anim.action_slot = anim.action.slots.get(slot)
    def copy_strips(strips):
        for strip in strips:
            if strip.type == 'META':
                copy_strips(strip.strips)
            if strip.type == 'CLIP' and strip.action:
                slot = strip.action_slot.identifier if strip.action_slot else None
                strip.action = independent(strip.action)
                if slot:
                    strip.action_slot = strip.action.slots.get(slot)
    for track in anim.nla_tracks:
        copy_strips(track.strips)


def duplicate(scene, obj):
    check_camera(obj)
    previous_camera, previous_index = scene.camera, scene.cf_scene.active_index
    previous_render = {key: getattr(scene.render, key) for key in RENDER}
    copied = obj.copy()
    data, actions, entry_added = None, [], False
    try:
        data = obj.data.copy()
        copied.data = data
        copied.name = obj.name + ' Copy'
        load(copied.cf_camera, dump(obj.cf_camera))
        copied.cf_camera.uid = uuid.uuid4().hex
        copy_actions(copied, actions)
        copy_actions(data, actions)
        set_baseline(copied)
        scene.collection.objects.link(copied)
        scene.cf_scene.cameras.add().camera = copied
        entry_added = True
        scene.cf_scene.active_index = len(scene.cf_scene.cameras) - 1
        apply(scene, copied)
    except Exception:
        if entry_added:
            scene.cf_scene.cameras.remove(len(scene.cf_scene.cameras) - 1)
        scene.cf_scene.active_index, scene.camera = previous_index, previous_camera
        for key, value in previous_render.items():
            setattr(scene.render, key, value)
        bpy.data.objects.remove(copied, do_unlink=True)
        if data and data.users == 0:
            bpy.data.cameras.remove(data)
        for action in actions:
            if action.users == 0:
                bpy.data.actions.remove(action)
        raise
    return copied


def resize(scene, obj, width, height):
    """Translation in pixel space, preserving ray direction per original pixel."""
    check_resize(scene, obj)
    if not (4 <= width <= 30000 and 4 <= height <= 30000):
        raise ValueError('用紙の対応範囲は4～30000pxです')
    s, data = obj.cf_camera, obj.data
    l, r, b, t = bounds(scene, obj)
    ax, ay = int(s.paper_anchor) % 3 / 2, int(s.paper_anchor) // 3 / 2
    left, top = round((width - s.width) * ax), round((height - s.height) * ay)
    dx, dy = (r - l) / s.width, (t - b) / s.height
    l, r = l - left * dx, r + (width - s.width - left) * dx
    b, t = b - (height - s.height - top) * dy, t + top * dy
    span = r - l
    values = {'sensor_fit': 'HORIZONTAL', 'shift_x': (l + r) / (2 * span), 'shift_y': (b + t) / (2 * span)}
    values['sensor_width' if data.type == 'PERSP' else 'ortho_scale'] = data.lens * span if data.type == 'PERSP' else span
    for key, value in values.items():
        prop = data.bl_rna.properties[key]
        if isinstance(value, (int, float)) and (not math.isfinite(value) or not prop.hard_min <= value <= prop.hard_max):
            raise ValueError('カメラ投影設定の上限に達しました。用紙サイズを小さく変更してください')
    for frame in s.frames:
        if not (-1000000 <= frame.x + left <= 1000000 and -1000000 <= frame.y + top <= 1000000):
            raise ValueError('FRAMEの座標上限に達しました')
    for key, value in values.items():
        setattr(data, key, value)
    for frame in s.frames:
        frame.x += left
        frame.y += top
    s.origin = (s.origin[0] + left, s.origin[1] + top)
    s.width, s.height = width, height
    apply(scene, obj)
    return left, top


def reset_paper(scene, obj):
    check_resize(scene, obj)
    s = obj.cf_camera
    try:
        base = json.loads(s.baseline)
        if set(base['projection']) != set(PROJECTION):
            raise ValueError()
        for key in ('width', 'height', 'percentage', 'aspect_x', 'aspect_y'):
            prop = s.bl_rna.properties[key]
            value = base[key]
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not prop.hard_min <= value <= prop.hard_max:
                raise ValueError()
        for key, value in base['projection'].items():
            prop = obj.data.bl_rna.properties[key]
            if prop.type == 'ENUM':
                if value not in prop.enum_items:
                    raise ValueError()
            elif not isinstance(value, (float, int)) or not math.isfinite(value) or not prop.hard_min <= value <= prop.hard_max:
                raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError('復帰基準が読み取れません。カメラを再登録してください') from None
    if any(not (-1000000 <= f.x - s.origin[0] <= 1000000 and -1000000 <= f.y - s.origin[1] <= 1000000) for f in s.frames):
        raise ValueError('FRAMEの座標上限に達しました')
    for f in s.frames:
        f.x -= s.origin[0]
        f.y -= s.origin[1]
    for key, value in base['projection'].items():
        setattr(obj.data, key, value)
    for key in ('width', 'height', 'percentage', 'aspect_x', 'aspect_y'):
        setattr(s, key, base[key])
    s.origin = (0, 0)
    apply(scene, obj)


def horizon_normal(obj):
    """Horizontal-plane covector in Blender's scale-normalized camera basis.

    A quaternion would discard the shear inherited from a scaled parent.
    Dotting world up with each normalized basis column preserves that shear.
    Translation does not affect a vanishing line.
    """
    matrix = obj.matrix_world.to_3x3().normalized()
    determinant = matrix.determinant()
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        return None
    return tuple(matrix[2])


def auxiliary(scene, obj, *, include_axis=True, include_horizon=True):
    s = obj.cf_camera
    if not (include_axis or include_horizon):
        return None, None
    l, r, b, t = bounds(scene, obj)
    axis = (-l / (r - l) * s.width, t / (t - b) * s.height) if include_axis else None
    horizon = None
    up = horizon_normal(obj) if include_horizon and obj.data.type == 'PERSP' else None
    if up is not None:
        # dot(world_up, camera_basis @ ray) == 0 at infinity.
        nx, ny, nz = up
        aa, bb, cc = nx * (r - l) / s.width, -ny * (t - b) / s.height, nx * l + ny * t - nz
        norm = math.hypot(aa, bb)
        if norm < 1e-15:
            return axis, None
        aa, bb, cc = aa / norm, bb / norm, cc / norm
        hits = []
        if abs(bb) > 1e-10:
            hits += [(x, -(aa * x + cc) / bb) for x in (0, s.width)]
        if abs(aa) > 1e-10:
            hits += [(-(bb * y + cc) / aa, y) for y in (0, s.height)]
        hits = [p for p in hits if -1e-5 <= p[0] <= s.width + 1e-5 and -1e-5 <= p[1] <= s.height + 1e-5]
        if len(hits) >= 2:
            horizon = max(((a, b) for a in hits for b in hits), key=lambda pair: math.dist(*pair))
    return axis, horizon


def export_auxiliary(scene, obj):
    """Evaluate requested auxiliary guides and explain any omitted layer.

    Display toggles deliberately do not participate in export selection.
    """
    s = obj.cf_camera
    if not (s.export_axis or s.export_horizon):
        return None, None, []
    axis, horizon = auxiliary(scene, obj, include_axis=s.export_axis, include_horizon=s.export_horizon)
    omitted = []
    if s.export_axis and not (0 <= axis[0] <= s.width and 0 <= axis[1] <= s.height):
        omitted.append('光軸: 用紙外のため出力なし')
    if s.export_horizon:
        if obj.data.type != 'PERSP':
            omitted.append('アイレベル: 正投影では出力なし')
        elif horizon_normal(obj) is None:
            omitted.append('アイレベル: カメラ変換が退化しているため出力なし')
        elif horizon is None:
            omitted.append('アイレベル: 用紙内にないため出力なし')
    return axis, horizon, omitted

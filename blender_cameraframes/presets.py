# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, validated data presets in Blender's persistent user directory."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
import bpy


BUILTINS = (
    ('A4_150', 'A4横・150dpi', dict(width=1754, height=1240, frame_width=1536,
                                 ratio_x=16, ratio_y=9, ppi=150, offset_x=0, offset_y=0)),
    ('A4_200', 'A4横・200dpi', dict(width=2338, height=1654, frame_width=2048,
                                 ratio_x=16, ratio_y=9, ppi=200, offset_x=0, offset_y=0)),
)
LIMITS = dict(width=(4, 30000), height=(4, 30000), frame_width=(1, 1e6),
              ratio_x=(.01, 1000), ratio_y=(.01, 1000), ppi=(1, 10000),
              offset_x=(-1e6, 1e6), offset_y=(-1e6, 1e6))
_state = None
_error = ''
_items = []
_user_items = []


def location():
    if __package__.startswith('bl_ext.'):
        directory = bpy.utils.extension_path_user(__package__)
    else:
        directory = bpy.utils.user_resource('CONFIG', path='camera_frames')
    if not directory:
        raise OSError('ユーザー設定の保存先を取得できません')
    return Path(directory) / 'camera-presets.json'


def validate(values):
    if not isinstance(values, dict) or set(values) != set(LIMITS):
        raise ValueError('プリセットの項目が正しくありません')
    result = {}
    for key, (low, high) in LIMITS.items():
        v = values[key]
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('プリセットの数値が対応範囲外です')
        if key in {'width', 'height'} and int(v) != v:
            raise ValueError('用紙サイズは整数で指定してください')
        result[key] = int(v) if key in {'width', 'height'} else float(v)
    if any(abs(result[axis] / 2 + result[offset]) > 1e6
           for axis, offset in (('width', 'offset_x'), ('height', 'offset_y'))):
        raise ValueError('FRAMEの中心が対応範囲外です')
    return result


def rebuild_items():
    global _items, _user_items
    _items = [(key, name, '', i) for i, (key, name, _) in enumerate(BUILTINS)]
    _user_items = [(p['id'], p['name'], '', i + len(BUILTINS))
                   for i, p in enumerate(_state['presets'])]
    _items += _user_items


def register():
    global _state, _error
    _state = dict(version=1, presets=[], last=BUILTINS[0][0], values=dict(BUILTINS[0][2]))
    _error = ''
    try:
        path = location()
        if path.exists():
            if path.stat().st_size > 512 * 1024:
                raise ValueError('プリセットファイルが大きすぎます')
            data = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or not isinstance(data.get('presets'), list) or len(data['presets']) > 128):
                raise ValueError('プリセットファイルの形式に対応していません')
            ids = {v[0] for v in BUILTINS}
            names = set()
            for p in data['presets']:
                if (not isinstance(p, dict) or set(p) != {'id', 'name', 'values'}
                        or not isinstance(p['id'], str) or not p['id'].startswith('USER_')
                        or len(p['id']) != 37 or p['id'] in ids
                        or not isinstance(p['name'], str) or not 1 <= len(p['name']) <= 64
                        or any(ord(c) < 32 for c in p['name']) or p['name'] in names):
                    raise ValueError('ユーザープリセットが正しくありません')
                uuid.UUID(p['id'][5:])
                p['values'] = validate(p['values'])
                ids.add(p['id']); names.add(p['name'])
            data['values'] = validate(data.get('values'))
            if not isinstance(data.get('last'), str) or data['last'] not in ids:
                raise ValueError('前回のプリセットを読み込めません')
            _state = {key: data[key] for key in ('version', 'presets', 'last', 'values')}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _error = 'プリセットを読み込めませんでした。保存済みファイルは変更しません'
    rebuild_items()


def unregister():
    global _state, _error
    _state = None
    _error = ''
    _items.clear(); _user_items.clear()


def items(_self, _context):
    return _items


def user_items(_self, _context):
    return _user_items


def get(key):
    for identifier, _name, values in BUILTINS:
        if key == identifier:
            return dict(values)
    return dict(next(p['values'] for p in _state['presets'] if p['id'] == key))


def write(state):
    global _state
    if _error:
        raise ValueError(_error)
    path = location()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.camera-presets-', suffix='.json', delete=False) as f:
            temporary = Path(f.name)
            json.dump(state, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    _state = state
    rebuild_items()


def remember(key, values):
    values = validate(values)
    if key == _state['last'] and values == _state['values']:
        return
    state = copy.deepcopy(_state)
    state.update(last=key, values=values)
    write(state)


def save(name, values):
    name = name.strip()
    if not name or len(name) > 64 or any(ord(c) < 32 for c in name):
        raise ValueError('名前を1～64文字で指定してください')
    if any(name == p['name'] for p in _state['presets']) or any(name == p[1] for p in BUILTINS):
        raise ValueError('同じ名前のプリセットがあります。別の名前を指定してください')
    if len(_state['presets']) >= 128:
        raise ValueError('ユーザープリセットは128件まで保存できます')
    state = copy.deepcopy(_state)
    key = 'USER_' + uuid.uuid4().hex
    values = validate(values)
    state['presets'].append(dict(id=key, name=name, values=values))
    state.update(last=key, values=values)
    write(state)
    return key


def remove(key):
    state = copy.deepcopy(_state)
    if not any(p['id'] == key for p in state['presets']):
        raise ValueError('削除するユーザープリセットがありません')
    state['presets'] = [p for p in state['presets'] if p['id'] != key]
    if state['last'] == key:
        state.update(last=BUILTINS[0][0], values=dict(BUILTINS[0][2]))
    write(state)

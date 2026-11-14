# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def redraw(self, context):
    if context and context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class CFFrame(bpy.types.PropertyGroup):
    name: StringProperty(name='名前', default='FRAME')
    x: FloatProperty(name='X', default=0, min=-1000000, max=1000000, update=redraw)
    y: FloatProperty(name='Y', default=0, min=-1000000, max=1000000, update=redraw)
    width: FloatProperty(name='幅 px', default=1536, min=1, max=1000000, update=redraw)
    angle: FloatProperty(name='回転', subtype='ANGLE', update=redraw)
    anchor: FloatVectorProperty(name='基点', size=2, min=-1, max=1, default=(0, 0), update=redraw)
    selected: BoolProperty(name='選択', default=False, update=redraw)
    visible: BoolProperty(name='表示', default=True, update=redraw)
    export: BoolProperty(name='PSD', default=True)
    color: FloatVectorProperty(name='色', size=3, subtype='COLOR', min=0, max=1, default=(0.1, 0.65, 1), update=redraw)
    handle_mode: EnumProperty(name='ハンドル', items=[(s, n, '') for s, n in
        [('AUTO', '自動'), ('CORNER', '角'), ('MIRRORED', '対称'), ('FREE', '自由')]], default='AUTO', update=redraw)
    handle_in: FloatVectorProperty(name='入るハンドル', size=2, default=(-100, 0), update=redraw)
    handle_out: FloatVectorProperty(name='出るハンドル', size=2, default=(100, 0), update=redraw)


class CFCamera(bpy.types.PropertyGroup):
    uid: StringProperty()
    width: IntProperty(name='用紙幅', min=4, max=30000, default=1920)
    height: IntProperty(name='用紙高さ', min=4, max=30000, default=1080)
    percentage: IntProperty(default=100, min=1, max=100)
    aspect_x: FloatProperty(default=1, min=1, max=200)
    aspect_y: FloatProperty(default=1, min=1, max=200)
    ppi: FloatProperty(name='PPI', default=150, min=1, max=10000)
    paper_anchor: EnumProperty(name='用紙の固定基点', items=[
        (str(i), n, '') for i, n in enumerate(('左上', '上', '右上', '左', '中央', '右', '左下', '下', '右下'))], default='4')
    aspect_lock: BoolProperty(name='用紙の縦横比を固定', default=False)
    origin: FloatVectorProperty(size=2)
    baseline: StringProperty()
    frames: CollectionProperty(type=CFFrame)
    frame_index: IntProperty(default=0, min=0, update=redraw)
    ratio_x: FloatProperty(name='FRAME 横', default=16, min=0.01, max=1000, update=redraw)
    ratio_y: FloatProperty(name='縦', default=9, min=0.01, max=1000, update=redraw)
    path_type: EnumProperty(name='軌道', items=[('LINE', '直線', ''), ('BEZIER', 'ベジェ', '')], default='BEZIER', update=redraw)
    corner_paths: BoolProperty(name='四隅の軌道', default=False, update=redraw)
    show_path: BoolProperty(name='軌道を表示', default=True, update=redraw)
    export_path: BoolProperty(name='軌道をPSDへ', default=True)
    path_color: FloatVectorProperty(name='軌道色', size=3, subtype='COLOR', min=0, max=1, default=(1, 0.35, 0.08), update=redraw)
    line_width: FloatProperty(name='線幅 px', default=2, min=0.5, max=32, update=redraw)
    show_guides: BoolProperty(name='ガイドを表示', default=True, update=redraw)
    show_axis: BoolProperty(name='光軸', default=True, update=redraw)
    show_horizon: BoolProperty(name='アイレベル', default=True, update=redraw)


class CFCameraEntry(bpy.types.PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object)


class CFScene(bpy.types.PropertyGroup):
    cameras: CollectionProperty(type=CFCameraEntry)
    active_index: IntProperty(default=0, min=0)


CLASSES = (CFFrame, CFCamera, CFCameraEntry, CFScene)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.cf_camera = PointerProperty(type=CFCamera)
    bpy.types.Scene.cf_scene = PointerProperty(type=CFScene)


def unregister():
    del bpy.types.Scene.cf_scene
    del bpy.types.Object.cf_camera
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

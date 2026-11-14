# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import geometry
from math import atan, degrees, radians, tan
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def redraw(self, context):
    if context and context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def get_view_scale(self):
    from . import overlay
    return overlay.display_scale(bpy.context)


def set_view_scale(self, value):
    from . import overlay
    overlay.set_display_scale(bpy.context, value)


def lens_values(self):
    from . import camera, geometry
    obj, scene = self.id_data, bpy.context.scene
    if not scene or not self.frames or obj.type != 'CAMERA' or obj.data.type != 'PERSP' or not camera.synced(scene, obj):
        return 0.0, 0.0
    frame = self.frames[min(self.frame_index, len(self.frames) - 1)]
    return geometry.frame_lens(frame, self.ratio_x / self.ratio_y, camera.bounds(scene, obj), self.width, self.height)


def get_frame_focal(self):
    return lens_values(self)[0]


def set_frame_focal(self, value):
    from . import camera
    camera.set_frame_focal(bpy.context.scene, self.id_data, value, clamp=True)


def get_frame_angle(self):
    return lens_values(self)[1]


def set_frame_angle(self, value):
    from . import camera
    camera.set_frame_focal(bpy.context.scene, self.id_data, 18 / tan(radians(value) / 2), clamp=True)


def get_frame_color(self):
    stored = self.get('color')
    if stored is not None:
        return stored
    s = self.id_data.cf_camera
    # Old files may rely on the implicit blue. Reading it must not mutate the
    # scene or access bpy.data during Blender's restricted add-on registration.
    return (0.1, 0.65, 1.0) if s.uid and s.next_frame_color < 0 else geometry.FRAME_COLORS[0]


def set_frame_color(self, value):
    self['color'] = value


def initialize_frame_colors(settings):
    """Freeze old implicit colors only before the first new FRAME is created."""
    if settings.next_frame_color < 0:
        for frame in settings.frames:
            if 'color' not in frame:
                frame.color = tuple(frame.color)
        settings.next_frame_color = 0


class CFFrame(bpy.types.PropertyGroup):
    name: StringProperty(name='名前', default='FRAME')
    x: FloatProperty(name='X', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw)
    y: FloatProperty(name='Y', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw)
    width: FloatProperty(name='幅 px', default=1536, min=geometry.FRAME_MIN_WIDTH, max=geometry.FRAME_MAX_WIDTH, update=redraw)
    angle: FloatProperty(name='回転', subtype='ANGLE', update=redraw)
    anchor: FloatVectorProperty(name='基点', size=2, default=(0, 0), update=redraw)
    selected: BoolProperty(name='選択', default=False, update=redraw)
    visible: BoolProperty(name='ビューに表示', default=True, update=redraw,
                          description='このFRAMEの枠線をビューに表示します。PSDの出力設定は変えません')
    export: BoolProperty(name='PSDに書き出す', default=True,
                         description='このFRAMEの枠線をPSDの独立レイヤーに書き出します。ビューの表示とは別に設定できます')
    color: FloatVectorProperty(name='色', size=3, subtype='COLOR', min=0, max=1, default=geometry.FRAME_COLORS[0],
                              get=get_frame_color, set=set_frame_color, update=redraw)
    handle_mode: EnumProperty(name='ハンドル', items=[(s, n, '') for s, n in
        [('AUTO', '自動'), ('CORNER', '角'), ('MIRRORED', '対称'), ('FREE', '自由')]], default='AUTO', update=redraw)
    handle_in: FloatVectorProperty(name='入るハンドル', size=2, default=(-100, 0), update=redraw)
    handle_out: FloatVectorProperty(name='出るハンドル', size=2, default=(100, 0), update=redraw)


class CFCamera(bpy.types.PropertyGroup):
    uid: StringProperty()
    width: IntProperty(name='用紙幅', min=4, max=30000, default=1920)
    height: IntProperty(name='用紙高さ', min=4, max=30000, default=1080)
    aspect_x: FloatProperty(default=1, min=1, max=200)
    aspect_y: FloatProperty(default=1, min=1, max=200)
    ppi: FloatProperty(name='PPI', default=150, min=1, max=10000,
                       description='PSDに記録する解像度です。画像のピクセル数は変えません')
    paper_anchor: EnumProperty(name='用紙の固定基点', description='用紙サイズを変更するとき、構図を固定する位置を選びます', items=[
        (str(i), n, '') for i, n in enumerate(('左上', '上', '右上', '左', '中央', '右', '左下', '下', '右下'))], default='4')
    aspect_lock: BoolProperty(name='用紙の縦横比を固定', default=False)
    origin: FloatVectorProperty(size=2)
    frames: CollectionProperty(type=CFFrame)
    next_frame_color: IntProperty(default=-1, min=-1, max=len(geometry.FRAME_COLORS)-1, options={'HIDDEN'})
    show_frame_names: BoolProperty(name='名前', default=True, update=redraw,
                                  description='FRAME編集・操作中に、選択または操作対象の名前を表示します。オフにすると常に非表示です')
    frame_index: IntProperty(default=0, min=0, update=redraw)
    # Computed UI properties belong to the Object ID so native numeric editing
    # is undoable. They never store a second lens value or participate in copies.
    frame_focal: FloatProperty(name='幅基準35mm換算 mm', default=50, min=0.000001, max=1e12,
                              step=10, precision=2, options={'SKIP_SAVE'},
                              get=get_frame_focal, set=set_frame_focal, update=redraw,
                              description='現在のアクティブFRAMEの換算焦点距離。ドラッグや数値入力でカメラの焦点距離を直接変更します')
    frame_angle: FloatProperty(name='基準画角 °', default=degrees(2 * atan(18 / 50)), min=0.000001, max=179.9999,
                              step=10, precision=2, options={'SKIP_SAVE'},
                              get=get_frame_angle, set=set_frame_angle, update=redraw,
                              description='現在のアクティブFRAMEの幅基準画角。ドラッグや数値入力でカメラの焦点距離を直接変更します')
    show_mask: BoolProperty(name='FRAME外をマスク', default=False, update=redraw)
    mask_source: EnumProperty(name='対象', items=[
        ('ACTIVE', 'アクティブFRAME', '一覧で編集中の1枚を使用します'),
        ('ALL', '全FRAMEの通過範囲', '一覧順の全FRAMEを使用します。枠線の表示・PSD出力のチェックとは独立です')],
        default='ALL', update=redraw)
    mask_opacity: FloatProperty(name='不透明度 %', default=80, min=0, max=100, precision=1, update=redraw)
    export_mask: BoolProperty(name='マスクをPSDへ', default=False,
                             description='画面のマスク表示とは独立して、同じ対象・不透明度で別レイヤーへ出力します')
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
    export_axis: BoolProperty(name='光軸をPSDへ', default=True,
                             description='画面表示とは独立して光軸を出力します。用紙外にある場合は出力しません')
    export_horizon: BoolProperty(name='アイレベルをPSDへ', default=True,
                                description='画面表示とは独立してアイレベルを出力します。用紙外にある場合や正投影では出力しません')


class CFCameraEntry(bpy.types.PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object)


class CFScene(bpy.types.PropertyGroup):
    cameras: CollectionProperty(type=CFCameraEntry)
    active_index: IntProperty(default=-1, min=-1)


CLASSES = (CFFrame, CFCamera, CFCameraEntry, CFScene)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.cf_camera = PointerProperty(type=CFCamera)
    bpy.types.Scene.cf_scene = PointerProperty(type=CFScene)
    # A UI proxy for the current RegionView3D; no duplicate saved zoom state.
    bpy.types.WindowManager.cf_view_scale = FloatProperty(
        name='表示倍率 %', min=0.01, max=100000, soft_min=5, soft_max=200, precision=1, step=100,
        description='用紙の表示倍率。100%は用紙幅と表示幅が同じピクセル数。カメラの構図や出力サイズは変更しません',
        get=get_view_scale, set=set_view_scale, options={'SKIP_SAVE'})


def unregister():
    del bpy.types.WindowManager.cf_view_scale
    del bpy.types.Scene.cf_scene
    del bpy.types.Object.cf_camera
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

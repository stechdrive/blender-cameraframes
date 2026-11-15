# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import geometry, frames
from math import atan, degrees, radians
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
    from . import camera
    obj, scene = self.id_data, bpy.context.scene
    if not scene or frames.active(self) is None or obj.type != 'CAMERA' or obj.data.type != 'PERSP' or not camera.synced(scene, obj):
        return 0.0, 0.0
    return camera.frame_lens_values(scene, obj)


def get_frame_focal(self):
    return lens_values(self)[0]


def set_frame_focal(self, value):
    from . import camera
    camera.set_frame_focal(bpy.context.scene, self.id_data, value, clamp=True)


def get_frame_angle(self):
    return lens_values(self)[1]


def set_frame_angle(self, value):
    from . import camera
    camera.set_frame_angle(bpy.context.scene, self.id_data, value, clamp=True)


def visibility_changed(self, context):
    if not self.visible:
        frames.set_selected(self, False)
    redraw(self, context)


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


def get_handle_mode(self):
    return self.get('handle_mode', 0)


def set_handle_mode(self, value):
    if value == get_handle_mode(self):
        return
    if value in {2, 3}:
        frames = self.id_data.cf_camera.frames
        index = next((i for i, f in enumerate(frames) if f == self), None)
        if index is not None:
            incoming, outgoing = geometry.handles(frames, index)
            if value == 2:
                incoming = incoming if index > 0 and any(incoming) else tuple(-v for v in outgoing)
                outgoing = tuple(-v for v in incoming)
            self.handle_in, self.handle_out = incoming, outgoing
    self['handle_mode'] = value


def get_path_points(self):
    # Old files used a checkbox that added four corners to the centre path.
    return self.get('path_points', 6 if self.corner_paths else 0)


def set_path_points(self, value):
    self['path_points'] = value


class CFFrame(bpy.types.PropertyGroup):
    name: StringProperty(name='名前', default='FRAME')
    x: FloatProperty(name='X', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw)
    y: FloatProperty(name='Y', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw)
    width: FloatProperty(name='幅 px', default=1536, min=geometry.FRAME_MIN_WIDTH, max=geometry.FRAME_MAX_WIDTH, update=redraw)
    angle: FloatProperty(name='回転', subtype='ANGLE', update=redraw)
    anchor: FloatVectorProperty(name='基点', size=2, default=(0, 0), update=redraw)
    selected: BoolProperty(name='選択', default=False, get=frames.get_selected, set=frames.set_selected, update=redraw,
                          description='操作するFRAMEを選びます。チェックで複数選択、行のクリックで1枚選択します')
    visible: BoolProperty(name='ビューに表示', default=True, update=visibility_changed,
                          description='このFRAMEの枠線をビューに表示します。PSDの出力設定は変えません')
    export: BoolProperty(name='PSDに書き出す', default=True,
                         description='このFRAMEの枠線をPSDの独立レイヤーに書き出します。ビューの表示とは別に設定できます')
    color: FloatVectorProperty(name='色', size=3, subtype='COLOR', min=0, max=1, default=geometry.FRAME_COLORS[0],
                              get=get_frame_color, set=set_frame_color, update=redraw)
    handle_mode: EnumProperty(name='ハンドル', items=[
        ('AUTO', '自動', '前後のFRAMEから滑らかな接線を計算します', 0),
        ('CORNER', '折れ', 'このFRAMEで軌道を折り、前後のハンドルを中心に縮めます', 1),
        ('MIRRORED', '対称', '前後のハンドルを同じ長さ・反対向きに連動させます', 2),
        ('FREE', '自由', '前後のハンドルを別々に操作します', 3)],
        default='AUTO', get=get_handle_mode, set=set_handle_mode, update=redraw)
    handle_in: FloatVectorProperty(name='前のFRAME側', size=2, default=(-100, 0), update=redraw)
    handle_out: FloatVectorProperty(name='次のFRAME側', size=2, default=(100, 0), update=redraw)


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
    frame_index: IntProperty(default=-1, min=-1, get=frames.active_index, set=frames.select_row, update=redraw)
    edit_x: FloatProperty(name='中心X', min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT,
        get=lambda s: frames.value(s, 'x'), set=lambda s, v: frames.set_value(s, 'x', v), update=redraw, options={'SKIP_SAVE'},
        description='用紙左上からの中心X座標（px）。複数選択では位置関係を保って全体を移動します')
    edit_y: FloatProperty(name='中心Y', min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT,
        get=lambda s: frames.value(s, 'y'), set=lambda s, v: frames.set_value(s, 'y', v), update=redraw, options={'SKIP_SAVE'},
        description='用紙左上からの中心Y座標（px）。下向きが正。複数選択では位置関係を保って全体を移動します')
    edit_width: FloatProperty(name='幅', min=geometry.FRAME_MIN_WIDTH, max=geometry.FRAME_MAX_WIDTH,
        get=lambda s: frames.value(s, 'width'), set=lambda s, v: frames.set_value(s, 'width', v), update=redraw, options={'SKIP_SAVE'},
        description='FRAMEの幅（px）。縦横比を保って拡縮します。複数選択では青い行のFRAMEの基点から全体を同じ倍率で拡縮します')
    edit_height: FloatProperty(name='高さ', min=0.00001, max=1e11,
        get=lambda s: frames.value(s, 'height'), set=lambda s, v: frames.set_value(s, 'height', v), update=redraw, options={'SKIP_SAVE'},
        description='FRAMEの高さ（px）。縦横比を保って拡縮します。複数選択では青い行のFRAMEの基点から全体を同じ倍率で拡縮します')
    edit_angle: FloatProperty(name='回転', subtype='ANGLE',
        get=lambda s: frames.value(s, 'angle'), set=lambda s, v: frames.set_value(s, 'angle', v), update=redraw, options={'SKIP_SAVE'},
        description='FRAMEの回転角度。複数選択では青い行のFRAMEの基点を中心に全体を同じ角度だけ回転します')
    edit_color: FloatVectorProperty(name='色', size=3, subtype='COLOR', min=0, max=1,
        get=lambda s: frames.value(s, 'color'), set=lambda s, v: frames.set_value(s, 'color', v), update=redraw, options={'SKIP_SAVE'},
        description='選択したFRAMEの枠線の色を変更します')
    lens_reference: EnumProperty(name='換算mmの基準', default='LONG_EDGE', options=set(), update=redraw,
        description='FRAMEと出力に共通のmm換算基準。表示だけを変更し、レンズ・構図・画角は変えません',
        items=[('LONG_EDGE', '長辺基準', 'FRAME・出力それぞれの長辺をフルサイズの長辺36mmに合わせて換算します'),
               ('DIAGONAL', '対角基準', 'FRAME・出力それぞれの対角を36×24mmフルサイズの対角に合わせて換算します')])
    # Keep the saved enum identifiers/order from 0.1.10; they now select FOV only.
    frame_lens_axis: EnumProperty(name='FRAMEの画角方向', default='HORIZONTAL', options=set(), update=redraw,
        description='FRAMEの基準画角を確認する方向。切替は換算mm・レンズ・構図を変えません',
        items=[('HORIZONTAL', 'FRAME 水平', 'FRAMEの横方向。FRAMEの回転に追従します'),
               ('VERTICAL', 'FRAME 垂直', 'FRAMEの縦方向。FRAMEの回転に追従します'),
               ('DIAGONAL', 'FRAME 対角', 'FRAMEの左下から右上への対角。FRAMEの回転に追従します')])
    output_lens_axis: EnumProperty(name='出力の画角方向', default='HORIZONTAL', options=set(), update=redraw,
        description='出力全体の基準画角を確認する方向。切替は換算mm・レンズ・構図を変えません。出力倍率・領域クロップは含みません',
        items=[('HORIZONTAL', '出力 水平', '出力全体の横方向'),
               ('VERTICAL', '出力 垂直', '出力全体の縦方向'),
               ('DIAGONAL', '出力 対角', '出力全体の対角')])
    # Computed UI properties belong to the Object ID so native numeric editing
    # is undoable. They never store a second lens value or participate in copies.
    frame_focal: FloatProperty(name='換算焦点距離 mm', default=50, min=0.000001, max=1e12,
                              step=10, precision=2, subtype='DISTANCE_CAMERA', unit='CAMERA', options={'SKIP_SAVE'},
                              get=get_frame_focal, set=set_frame_focal, update=redraw,
                              description='カメラの焦点距離を変更します。選択した1枚のFRAMEを基準にフルサイズ換算で指定できます')
    frame_angle: FloatProperty(name='基準画角 °', default=degrees(2 * atan(18 / 50)), min=0.000001, max=179.9999,
                              step=10, precision=2, options={'SKIP_SAVE'},
                              get=get_frame_angle, set=set_frame_angle, update=redraw,
                              description='カメラの焦点距離を変更します。選択した1枚のFRAMEを基準に、指定方向の画角を調整します。数値は中心配置に換算した画角です')
    frame_angle_ui: FloatProperty(name='基準画角', subtype='ANGLE', unit='ROTATION',
        min=radians(0.000001), max=radians(179.9999), step=10, precision=4, options={'SKIP_SAVE'},
        get=lambda s: radians(get_frame_angle(s)), set=lambda s, v: set_frame_angle(s, degrees(v)), update=redraw,
        description='カメラの焦点距離を変更します。選択した1枚のFRAMEを基準に、指定方向の画角を調整します。数値は中心配置に換算した画角です')
    show_mask: BoolProperty(name='FRAME外をマスク', default=False, update=redraw)
    mask_source: EnumProperty(name='対象', items=[
        ('ACTIVE', '選択FRAME', '青い行のFRAMEを使用します。選択がないときはマスクしません'),
        ('ALL', '全FRAMEの通過範囲', '一覧順の全FRAMEを使用します。枠線の表示・PSD出力のチェックとは独立です')],
        default='ALL', update=redraw)
    mask_opacity: FloatProperty(name='不透明度 %', default=80, min=0, max=100, precision=1, update=redraw)
    export_mask: BoolProperty(name='マスクをPSDへ', default=False,
                             description='画面のマスク表示とは独立して、同じ対象・不透明度で別レイヤーへ出力します')
    ratio_x: FloatProperty(name='縦横比（共通）', default=16, min=0.01, max=1000, update=redraw,
                          description='このカメラの全FRAMEに共通する縦横比の横の値です')
    ratio_y: FloatProperty(name='縦', default=9, min=0.01, max=1000, update=redraw,
                          description='このカメラの全FRAMEに共通する縦横比の縦の値です')
    path_type: EnumProperty(name='軌道', items=[
        ('LINE', '直線', 'FRAMEの中心を直線で結びます。接線ハンドルの設定は保持します'),
        ('BEZIER', '曲線', 'ベジェの接線ハンドルでFRAMEの中心の移動を曲線にします')], default='BEZIER', update=redraw)
    corner_paths: BoolProperty(default=False, options={'HIDDEN'}, update=redraw)
    path_points: EnumProperty(name='軌道位置', items=[
        ('CENTER', '中央', 'FRAME中心の軌道を表示・出力します', 0),
        ('CORNERS', '四隅', '四隅の軌道だけを表示・出力します', 1),
        ('TOP_LEFT', '左上', '回転前の左上の角を追跡します', 2),
        ('TOP_RIGHT', '右上', '回転前の右上の角を追跡します', 3),
        ('BOTTOM_RIGHT', '右下', '回転前の右下の角を追跡します', 4),
        ('BOTTOM_LEFT', '左下', '回転前の左下の角を追跡します', 5),
        ('ALL', '中央＋四隅', '中央と四隅の5本を表示・出力します', 6)],
        default='CENTER', get=get_path_points, set=set_path_points, update=redraw,
        description='ビューとPSDに描く軌道の位置です。FRAMEの動きや通過範囲のマスクは変えません')
    show_path: BoolProperty(name='軌道を表示', default=True, update=redraw)
    export_path: BoolProperty(name='軌道をPSDへ', default=True)
    path_color: FloatVectorProperty(name='軌道色', size=3, subtype='COLOR', min=0, max=1, default=(1, 0.35, 0.08), update=redraw)
    line_width: FloatProperty(name='線幅 px', default=2, min=0.5, max=32, update=redraw,
                             description='ビューポートとPSDに共通のガイド線幅です。出力倍率100%でのピクセル数を指定します')
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

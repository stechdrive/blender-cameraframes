# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from .translations import CONTEXT
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
    name: StringProperty(name='Name', default='FRAME', translation_context=CONTEXT)
    x: FloatProperty(name='X', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw, translation_context=CONTEXT)
    y: FloatProperty(name='Y', default=0, min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT, update=redraw, translation_context=CONTEXT)
    width: FloatProperty(name='Width (px)', default=1536, min=geometry.FRAME_MIN_WIDTH, max=geometry.FRAME_MAX_WIDTH, update=redraw, translation_context=CONTEXT)
    angle: FloatProperty(name='Rotate', subtype='ANGLE', update=redraw, translation_context=CONTEXT)
    anchor: FloatVectorProperty(name='Pivot', size=2, default=(0, 0), update=redraw, translation_context=CONTEXT)
    selected: BoolProperty(name='Select', default=False, get=frames.get_selected, set=frames.set_selected, update=redraw,
                          description='Select FRAMEs to edit. Use checkboxes for multiple selection, or click a row to select one', translation_context=CONTEXT)
    visible: BoolProperty(name='Show in View', default=True, update=visibility_changed,
                          description='Show this FRAME outline in the viewport, independently of PSD export', translation_context=CONTEXT)
    export: BoolProperty(name='Export to PSD', default=True,
                         description='Export this FRAME outline as a separate PSD layer, independently of viewport visibility', translation_context=CONTEXT)
    color: FloatVectorProperty(name='Color', size=3, subtype='COLOR', min=0, max=1, default=geometry.FRAME_COLORS[0],
                              get=get_frame_color, set=set_frame_color, update=redraw, translation_context=CONTEXT)
    handle_mode: EnumProperty(name='Handles', items=[
        ('AUTO', 'Auto', 'Calculate smooth tangents from the neighboring FRAMEs', 0),
        ('CORNER', 'Corner', 'Make a sharp corner at this FRAME by collapsing both handles to its center', 1),
        ('MIRRORED', 'Mirrored', 'Keep both handles at equal lengths in opposite directions', 2),
        ('FREE', 'Free', 'Edit the incoming and outgoing handles independently', 3)],
        default='AUTO', get=get_handle_mode, set=set_handle_mode, update=redraw, translation_context=CONTEXT)
    handle_in: FloatVectorProperty(name='Incoming', size=2, default=(-100, 0), update=redraw, translation_context=CONTEXT)
    handle_out: FloatVectorProperty(name='Outgoing', size=2, default=(100, 0), update=redraw, translation_context=CONTEXT)


class CFCamera(bpy.types.PropertyGroup):
    uid: StringProperty()
    width: IntProperty(name='Paper Width', min=4, max=30000, default=1920, translation_context=CONTEXT)
    height: IntProperty(name='Paper Height', min=4, max=30000, default=1080, translation_context=CONTEXT)
    aspect_x: FloatProperty(default=1, min=1, max=200)
    aspect_y: FloatProperty(default=1, min=1, max=200)
    ppi: FloatProperty(name='PPI', default=150, min=1, max=10000,
                       description='Resolution stored in the PSD. Does not change the pixel dimensions', translation_context=CONTEXT)
    paper_anchor: EnumProperty(name='Paper Anchor', description='Choose the point that stays fixed when resizing the paper', items=[
        (str(i), n, '') for i, n in enumerate(('Top Left', 'Top', 'Top Right', 'Left', 'Center', 'Right', 'Bottom Left', 'Bottom', 'Bottom Right'))], default='4', translation_context=CONTEXT)
    aspect_lock: BoolProperty(name='Lock Paper Aspect', default=False, translation_context=CONTEXT)
    origin: FloatVectorProperty(size=2)
    frames: CollectionProperty(type=CFFrame)
    next_frame_color: IntProperty(default=-1, min=-1, max=len(geometry.FRAME_COLORS)-1, options={'HIDDEN'})
    show_frame_names: BoolProperty(name='Name', default=True, update=redraw,
                                  description='Show names for selected FRAMEs while editing. Disable to always hide names', translation_context=CONTEXT)
    frame_index: IntProperty(default=-1, min=-1, get=frames.active_index, set=frames.select_row, update=redraw)
    edit_x: FloatProperty(name='Center X', min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT,
        get=lambda s: frames.value(s, 'x'), set=lambda s, v: frames.set_value(s, 'x', v), update=redraw, options={'SKIP_SAVE'},
        description="Center X in pixels from the paper's top left. Multiple FRAMEs move together, preserving their relative positions", translation_context=CONTEXT)
    edit_y: FloatProperty(name='Center Y', min=-geometry.FRAME_COORD_LIMIT, max=geometry.FRAME_COORD_LIMIT,
        get=lambda s: frames.value(s, 'y'), set=lambda s, v: frames.set_value(s, 'y', v), update=redraw, options={'SKIP_SAVE'},
        description="Center Y in pixels from the paper's top left, positive downward. Multiple FRAMEs move together, preserving their relative positions", translation_context=CONTEXT)
    edit_width: FloatProperty(name='Width', min=geometry.FRAME_MIN_WIDTH, max=geometry.FRAME_MAX_WIDTH,
        get=lambda s: frames.value(s, 'width'), set=lambda s, v: frames.set_value(s, 'width', v), update=redraw, options={'SKIP_SAVE'},
        description="Inner width in pixels, excluding the outline. Scales at a fixed aspect ratio. Multiple FRAMEs scale together around the active row's pivot", translation_context=CONTEXT)
    edit_height: FloatProperty(name='Height', min=0.00001, max=1e11,
        get=lambda s: frames.value(s, 'height'), set=lambda s, v: frames.set_value(s, 'height', v), update=redraw, options={'SKIP_SAVE'},
        description="Inner height in pixels, excluding the outline. Scales at a fixed aspect ratio. Multiple FRAMEs scale together around the active row's pivot", translation_context=CONTEXT)
    edit_angle: FloatProperty(name='Rotate', subtype='ANGLE',
        get=lambda s: frames.value(s, 'angle'), set=lambda s, v: frames.set_value(s, 'angle', v), update=redraw, options={'SKIP_SAVE'},
        description="FRAME rotation. Multiple FRAMEs rotate together around the active row's pivot", translation_context=CONTEXT)
    edit_color: FloatVectorProperty(name='Color', size=3, subtype='COLOR', min=0, max=1,
        get=lambda s: frames.value(s, 'color'), set=lambda s, v: frames.set_value(s, 'color', v), update=redraw, options={'SKIP_SAVE'},
        description='Change the outline color of the selected FRAMEs', translation_context=CONTEXT)
    lens_reference: EnumProperty(name='Equivalent Lens Basis', default='LONG_EDGE', options=set(), update=redraw,
        description='Shared basis for FRAME and output equivalent focal lengths. Changes the displayed millimeters only, keeping the lens, composition and field of view',
        items=[('LONG_EDGE', 'Long Edge (36 mm)', 'Match the longer edge of each FRAME or output to the 36 mm edge of a full-frame sensor'),
               ('DIAGONAL', 'Sensor Diagonal', 'Match the diagonal of each FRAME or output to the diagonal of a 36 by 24 mm full-frame sensor')], translation_context=CONTEXT)
    # Keep the saved enum identifiers/order from 0.1.10; they now select FOV only.
    frame_lens_axis: EnumProperty(name='FRAME FOV Axis', default='HORIZONTAL', options=set(), update=redraw,
        description='Direction used to measure FRAME field of view. Switching keeps the equivalent focal length, lens and composition',
        items=[('HORIZONTAL', 'Horizontal', 'Across the FRAME width, following its rotation'),
               ('VERTICAL', 'Vertical', 'Across the FRAME height, following its rotation'),
               ('DIAGONAL', 'Diagonal', "Along the FRAME's bottom-left to top-right diagonal, following its rotation")], translation_context=CONTEXT)
    output_lens_axis: EnumProperty(name='Output FOV Axis', default='HORIZONTAL', options=set(), update=redraw,
        description="Direction used to measure the full output's field of view. Switching keeps the equivalent focal length, lens and composition. Excludes resolution percentage and render-region cropping",
        items=[('HORIZONTAL', 'Horizontal', 'Across the full output width'),
               ('VERTICAL', 'Vertical', 'Across the full output height'),
               ('DIAGONAL', 'Diagonal', 'Along the full output diagonal')], translation_context=CONTEXT)
    # Computed UI properties belong to the Object ID so native numeric editing
    # is undoable. They never store a second lens value or participate in copies.
    frame_focal: FloatProperty(name='Equivalent Focal Length (mm)', default=50, min=0.000001, max=1e12,
                              step=10, precision=2, subtype='DISTANCE_CAMERA', unit='CAMERA', options={'SKIP_SAVE'},
                              get=get_frame_focal, set=set_frame_focal, update=redraw,
                              description='Change the camera lens using the full-frame equivalent focal length of the single selected FRAME', translation_context=CONTEXT)
    frame_angle: FloatProperty(name='Field of View (degrees)', default=degrees(2 * atan(18 / 50)), min=0.000001, max=179.9999,
                              step=10, precision=2, options={'SKIP_SAVE'},
                              get=get_frame_angle, set=set_frame_angle, update=redraw,
                              description="Change the camera lens using the single selected FRAME's field of view along the chosen axis. The angle is measured as if the FRAME were centered on the optical axis", translation_context=CONTEXT)
    frame_angle_ui: FloatProperty(name='Field of View', subtype='ANGLE', unit='ROTATION',
        min=radians(0.000001), max=radians(179.9999), step=10, precision=4, options={'SKIP_SAVE'},
        get=lambda s: radians(get_frame_angle(s)), set=lambda s, v: set_frame_angle(s, degrees(v)), update=redraw,
        description="Change the camera lens using the single selected FRAME's field of view along the chosen axis. The angle is measured as if the FRAME were centered on the optical axis", translation_context=CONTEXT)
    show_mask: BoolProperty(name='Show Mask', default=False, update=redraw, translation_context=CONTEXT)
    mask_source: EnumProperty(name='Source', items=[
        ('ACTIVE', 'Selected FRAME', 'Use the selected FRAME in the active row. No mask is shown when nothing is selected'),
        ('ALL', "All FRAMEs' Sweep", 'Use all FRAMEs in list order, independently of outline visibility and PSD export checkboxes')],
        default='ALL', update=redraw, translation_context=CONTEXT)
    mask_opacity: FloatProperty(name='Opacity %', default=80, min=0, max=100, precision=1, update=redraw, translation_context=CONTEXT)
    export_mask: BoolProperty(name='Mask to PSD', default=False,
                             description='Export the same mask source and opacity as a separate layer, independently of viewport mask visibility', translation_context=CONTEXT)
    ratio_x: FloatProperty(name='Shared Aspect', default=16, min=0.01, max=1000, update=redraw,
                          description='Width component of the aspect ratio shared by all FRAMEs in this camera', translation_context=CONTEXT)
    ratio_y: FloatProperty(name='Y', default=9, min=0.01, max=1000, update=redraw,
                          description='Height component of the aspect ratio shared by all FRAMEs in this camera', translation_context=CONTEXT)
    path_type: EnumProperty(name='Motion Path', items=[
        ('LINE', 'Straight', 'Connect FRAME centers with straight lines, retaining the tangent handle settings'),
        ('BEZIER', 'Bezier', 'Use Bezier tangent handles to curve the motion between FRAME centers')], default='BEZIER', update=redraw, translation_context=CONTEXT)
    corner_paths: BoolProperty(default=False, options={'HIDDEN'}, update=redraw)
    path_points: EnumProperty(name='Path Position', items=[
        ('CENTER', 'Center', 'Show and export the path of the FRAME center', 0),
        ('CORNERS', 'Four Corners', 'Show and export only the four corner paths', 1),
        ('TOP_LEFT', 'Top Left', 'Track the corner that is at the top left before rotation', 2),
        ('TOP_RIGHT', 'Top Right', 'Track the corner that is at the top right before rotation', 3),
        ('BOTTOM_RIGHT', 'Bottom Right', 'Track the corner that is at the bottom right before rotation', 4),
        ('BOTTOM_LEFT', 'Bottom Left', 'Track the corner that is at the bottom left before rotation', 5),
        ('ALL', 'Center + Corners', 'Show and export five paths: the center and all four corners', 6)],
        default='CENTER', get=get_path_points, set=set_path_points, update=redraw,
        description='Path points shown in the viewport and PSD. Does not change FRAME motion or the swept-area mask', translation_context=CONTEXT)
    show_path: BoolProperty(name='Show Path', default=True, update=redraw, translation_context=CONTEXT)
    export_path: BoolProperty(name='Path to PSD', default=True, translation_context=CONTEXT)
    path_color: FloatVectorProperty(name='Path Color', size=3, subtype='COLOR', min=0, max=1, default=(1, 0.35, 0.08), update=redraw, translation_context=CONTEXT)
    line_width: FloatProperty(name='Line Width (px)', default=2, min=0.5, max=32, update=redraw,
                             description='Guide line width shared by the viewport and PSD, in pixels at 100% output resolution', translation_context=CONTEXT)
    show_guides: BoolProperty(name='Show Guides', default=True, update=redraw, translation_context=CONTEXT)
    show_axis: BoolProperty(name='Optical Axis', default=True, update=redraw, translation_context=CONTEXT)
    show_horizon: BoolProperty(name='Eye Level', default=True, update=redraw, translation_context=CONTEXT)
    export_axis: BoolProperty(name='Optical Axis to PSD', default=True,
                             description='Export the optical axis independently of viewport visibility, when it falls within the paper', translation_context=CONTEXT)
    export_horizon: BoolProperty(name='Eye Level to PSD', default=True,
                                description='Export the eye level independently of viewport visibility, when it crosses the paper in a perspective camera', translation_context=CONTEXT)


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
        name='View Zoom %', min=0.01, max=100000, soft_min=5, soft_max=200, precision=1, step=100,
        description='Paper zoom in the viewport. At 100%, paper and display widths have the same pixel count. Keeps camera composition and output dimensions',
        get=get_view_scale, set=set_view_scale, options={'SKIP_SAVE'}, translation_context=CONTEXT)


def unregister():
    del bpy.types.WindowManager.cf_view_scale
    del bpy.types.Scene.cf_scene
    del bpy.types.Object.cf_camera
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

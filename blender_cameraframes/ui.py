# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from .translations import CONTEXT, iface
from . import camera, geometry, overlay, frames


def lens_row(layout, settings, axis, label):
    english = iface('Horizontal') == 'Horizontal'
    widths = (2.3, 3.3, 2.9, 4.2) if english else (3.3, 3.0, 2.5, 2.3)
    row = layout.row(align=True)
    target, focal, angle, direction = (row.row(align=True) for _ in range(4))
    for column, width in zip((target, focal, angle, direction), widths):
        column.ui_units_x = width
    target.label(text=label, text_ctxt=CONTEXT)
    if english:
        direction.prop_menu_enum(settings, axis, text={
            'HORIZONTAL': 'Horiz.', 'VERTICAL': 'Vert.', 'DIAGONAL': 'Diag.'}[getattr(settings, axis)],
            text_ctxt=CONTEXT)
    else:
        direction.prop(settings, axis, text='')
    return focal, angle


def draw_lens_values(layout, scene, obj):
    if obj.data.type != 'PERSP' or not camera.synced(scene, obj):
        return
    s = obj.cf_camera
    error = None
    header = layout.split(factor=0.62 if iface('Horizontal') == 'Horizontal' else 0.78, align=True)
    header.label(text='35mm Equivalent / FOV', text_ctxt=CONTEXT)
    header.popover(panel='CF_PT_lens_details', text='Long Edge' if s.lens_reference == 'LONG_EDGE' else 'Diagonal', text_ctxt=CONTEXT)
    if s.frames:
        focal, angle = lens_row(layout, s, 'frame_lens_axis', 'FRAME')
        single = len(frames.selected_indices(s)) == 1
        if single:
            try:
                camera.check_frame_focal(scene, obj)
            except ValueError as failure:
                error = str(failure)
        focal.enabled = angle.enabled = single and error is None
        if frames.active(s) is not None:
            focal.prop(s, 'frame_focal', text='')
            angle.prop(s, 'frame_angle_ui', text='')
        else:
            focal.label(text='— mm', text_ctxt=CONTEXT)
            angle.label(text='—°', text_ctxt=CONTEXT)
    focal, angle = lens_row(layout, s, 'output_lens_axis', 'Output')
    mm, degrees = geometry.output_lens(camera.bounds(scene, obj), s.output_lens_axis, s.lens_reference)
    number = f'{mm:.2f}' if mm < 1e6 else f'{mm:.4g}'
    focal.alignment = angle.alignment = 'RIGHT'
    focal.label(text=f'{number} mm', translate=False)
    angle.label(text=f'{degrees:.2f}°', translate=False)
    if error:
        layout.label(text=error, icon='INFO', text_ctxt=CONTEXT)


class CF_UL_frames(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        selection = row.row(align=True)
        selection.enabled = item.visible
        selection.prop(item, 'selected', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.prop(item, 'visible', text='', icon='HIDE_OFF' if item.visible else 'HIDE_ON', emboss=False)
        row.prop(item, 'export', text='PSD', text_ctxt=CONTEXT)


class CF_PT_main(bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Camera Frames'
    bl_idname = 'CF_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'

    def draw_header_preset(self, context):
        if camera.active(context.scene):
            self.layout.popover(panel='CF_PT_guide_style', text='', icon='PREFERENCES')

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.operator('cf.register_camera', text='Add Selected', text_ctxt=CONTEXT)
        row.operator('cf.create_camera', text='New Camera', icon='ADD', text_ctxt=CONTEXT)
        row.menu('CF_MT_camera_presets', text='', icon='PRESET')
        for i, entry in enumerate(scene.cf_scene.cameras):
            row = layout.row(align=True)
            obj = entry.camera
            selected = obj is not None and i == scene.cf_scene.active_index
            row.operator('cf.activate_camera', text='' if selected else obj.name if obj else iface('(Deleted)'),
                         translate=False, icon='CAMERA_DATA', depress=selected).index = i
            if selected:
                row.prop(obj, 'name', text='')
            row.operator('cf.unregister_camera', text='', icon='X').index = i
        obj = camera.active(scene)
        if not obj:
            selected = context.active_object
            if ((selected and selected.type == 'CAMERA' and camera.registered_index(scene, selected) < 0)
                    or (scene.camera and camera.registered_index(scene, scene.camera) < 0)):
                layout.label(text='This camera is not registered', icon='INFO', text_ctxt=CONTEXT)
                layout.label(text='Use Add Selected to register it', text_ctxt=CONTEXT)
            return
        s = obj.cf_camera
        layout.operator('cf.duplicate_camera', icon='DUPLICATE')
        if not camera.synced(scene, obj):
            box = layout.box()
            box.alert = True
            if scene.camera != obj:
                box.label(text='The active camera has changed', icon='INFO', text_ctxt=CONTEXT)
            else:
                box.label(text='Paper and output settings differ', icon='ERROR', text_ctxt=CONTEXT)
                box.operator('cf.sync_output')
        if iface('Horizontal') == 'Horizontal':
            row = layout.row(align=True)
            row.prop(s, 'show_guides', text='Guides', text_ctxt=CONTEXT)
            row.prop(s, 'show_axis', text='Lens Axis', text_ctxt=CONTEXT)
            row.prop(s, 'show_horizon')
        else:
            row = layout.split(factor=0.27, align=True)
            row.prop(s, 'show_guides', text='Guides', text_ctxt=CONTEXT)
            rest = row.split(factor=0.37, align=True)
            rest.prop(s, 'show_axis')
            rest.prop(s, 'show_horizon')


class CameraPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'
    bl_parent_id = 'CF_PT_main'

    @classmethod
    def poll(cls, context):
        return camera.active(context.scene) is not None


class CF_PT_guide_style(bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Guide Line Width'
    bl_idname = 'CF_PT_guide_style'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        obj = camera.active(context.scene)
        if obj:
            self.layout.prop(obj.cf_camera, 'line_width')


class CF_PT_lens_details(bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Lens Display Details'
    bl_idname = 'CF_PT_lens_details'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 22

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return obj is not None and obj.data.type == 'PERSP'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.label(text='Shared 35mm Equivalent Basis', text_ctxt=CONTEXT)
        layout.prop(s, 'lens_reference', text='')
        layout.separator()
        layout.label(text='FOV is measured with the optical axis centered', text_ctxt=CONTEXT)
        if not camera.synced(context.scene, obj):
            return
        axis, _ = camera.auxiliary(context.scene, obj, include_horizon=False)
        layout.label(text='Optical axis: top left 0%, bottom right 100%', text_ctxt=CONTEXT)
        frame = frames.active(s)
        if frame is not None:
            x, y = geometry.frame_coordinates(axis, frame, s.ratio_x / s.ratio_y)
            layout.label(text=f'FRAME  X {x * 100:.2f}%  /  Y {y * 100:.2f}%', translate=False)
        layout.label(text=iface('Output  X {x:.2f}%  /  Y {y:.2f}%',
                               x=axis[0] / s.width * 100, y=axis[1] / s.height * 100), translate=False)


class CF_PT_view(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'View'
    bl_idname = 'CF_PT_view'

    def draw(self, context):
        layout = self.layout
        layout.prop(context.space_data, 'lock_camera', text='Lock Camera to View', text_ctxt=CONTEXT)
        col = layout.column()
        col.enabled = overlay.view_target(context, camera_view=False) is not None
        col.operator('cf.fit_view', icon='VIEW_CAMERA')
        row = col.row(align=True)
        row.enabled = overlay.view_target(context) is not None
        row.operator('cf.zoom_view', text='', icon='REMOVE').factor = 1 / 1.2
        row.prop(context.window_manager, 'cf_view_scale')
        row.operator('cf.zoom_view', text='', icon='ADD').factor = 1.2


class CF_PT_paper(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Output (Paper)'
    bl_idname = 'CF_PT_paper'

    def draw(self, context):
        s = camera.active(context.scene).cf_camera
        layout = self.layout
        layout.label(text=iface('Paper {width} × {height}px', width=s.width, height=s.height), translate=False)
        layout.prop(s, 'paper_anchor', text='Anchor', text_ctxt=CONTEXT)
        layout.prop(s, 'aspect_lock')
        row = layout.row(align=True)
        row.operator('cf.paper_size', text='Set Size', text_ctxt=CONTEXT)
        row.operator('cf.drag_paper', text='Drag Size', text_ctxt=CONTEXT)


class CF_PT_frames(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'FRAMEs'
    bl_idname = 'CF_PT_frames'

    def draw_header_preset(self, context):
        obj = camera.active(context.scene)
        row = self.layout.row(align=True)
        count = len(frames.selected_indices(obj.cf_camera))
        if count != 1:
            row.label(text=iface('{count} selected', count=count) if count else iface('None Selected'), translate=False)
        row.prop(obj.cf_camera, 'show_frame_names', text='Name', text_ctxt=CONTEXT)

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        selected = frames.selected_indices(s)
        layout.template_list('CF_UL_frames', '', s, 'frames', s, 'frame_index', rows=3)
        row = layout.row(align=True)
        for action, icon in (('ADD', 'ADD'), ('DUPLICATE', 'DUPLICATE'), ('REMOVE', 'REMOVE'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
            button = row.row(align=True)
            button.enabled = action == 'ADD' or bool(selected)
            button.operator('cf.frame', text='', icon=icon).action = action
        row.separator()
        row.operator('cf.frame', text='', icon='SELECT_EXTEND').action = 'SELECT_ALL'
        row.operator('cf.frame', text='', icon='SELECT_SET').action = 'DESELECT'
        row.separator()
        row.operator('cf.align_pixels', text='', icon='SNAP_INCREMENT')
        if not s.frames:
            draw_lens_values(layout, context.scene, obj)
            return
        editing = overlay.frame_tool_active(context)
        layout.operator('cf.edit_tool', text='Finish Editing in View' if editing else 'Edit in View',
                        icon='RESTRICT_SELECT_OFF', depress=editing, text_ctxt=CONTEXT)
        row = layout.row(align=True)
        row.enabled = bool(selected)
        for mode, label in (('MOVE', 'Move'), ('SCALE', 'Scale'), ('ROTATE', 'Rotate'), ('PIVOT', 'Pivot')):
            row.operator('cf.drag_frame', text=label, text_ctxt=CONTEXT).mode = mode
        col = layout.column(align=True)
        col.enabled = bool(selected)
        row = col.row(align=True)
        row.prop(s, 'edit_x', text='X', text_ctxt='Camera Frames Position')
        row.prop(s, 'edit_y', text='Y', text_ctxt='Camera Frames Position')
        row = col.row(align=True)
        row.prop(s, 'edit_width')
        row.prop(s, 'edit_height')
        row = col.split(factor=0.72, align=True)
        row.prop(s, 'edit_angle')
        row.prop(s, 'edit_color', text='')
        row = layout.split(factor=0.46, align=True)
        row.label(text='Shared Aspect', text_ctxt=CONTEXT)
        values = row.row(align=True)
        values.prop(s, 'ratio_x', text='')
        values.prop(s, 'ratio_y', text=':', text_ctxt=CONTEXT)
        if camera.synced(context.scene, obj):
            if obj.data.type == 'PERSP':
                draw_lens_values(layout, context.scene, obj)
            elif obj.data.type == 'ORTHO':
                l, r, b, t = camera.bounds(context.scene, obj)
                f = frames.active(s)
                layout.label(text=iface('Ortho Width: {width:.4g}', width=f.width / s.width * (r - l))
                             if f else iface('Ortho Width: —'), translate=False)


class CF_PT_mask(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Outside Mask'
    bl_idname = 'CF_PT_mask'

    def draw(self, context):
        s, layout = camera.active(context.scene).cf_camera, self.layout
        layout.prop(s, 'show_mask')
        col = layout.column()
        col.enabled = bool(s.frames)
        col.prop(s, 'mask_source')
        col.prop(s, 'mask_opacity')


class CF_PT_path(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Motion Path'
    bl_idname = 'CF_PT_path'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s, layout = camera.active(context.scene).cf_camera, self.layout
        layout.prop(s, 'path_type', text='Type', text_ctxt=CONTEXT)
        layout.prop(s, 'show_path')
        layout.prop(s, 'path_points', text='Points', text_ctxt=CONTEXT)
        layout.prop(s, 'path_color')
        f = frames.active(s)
        if len(s.frames) > 1 and s.path_type == 'BEZIER' and f is not None:
            col = layout.column(align=True)
            col.enabled = len(frames.selected_indices(s)) == 1
            col.prop(f, 'handle_mode')
            if f.handle_mode in {'MIRRORED', 'FREE'}:
                if s.frame_index > 0 or f.handle_mode == 'MIRRORED':
                    col.prop(f, 'handle_in', text='Handles' if f.handle_mode == 'MIRRORED' else 'Incoming', text_ctxt=CONTEXT)
                if f.handle_mode == 'FREE' and s.frame_index < len(s.frames) - 1:
                    col.prop(f, 'handle_out')


class CF_PT_export(CameraPanel, bpy.types.Panel):
    bl_translation_context = CONTEXT
    bl_label = 'Guide PSD'
    bl_idname = 'CF_PT_export'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.prop(s, 'ppi', text='PSD PPI', text_ctxt=CONTEXT)
        layout.prop(s, 'export_path')
        layout.prop(s, 'export_axis')
        layout.prop(s, 'export_horizon')
        layout.prop(s, 'export_mask')
        layout.operator('cf.export_psd', icon='EXPORT')


CLASSES = (CF_UL_frames, CF_PT_main, CF_PT_guide_style, CF_PT_lens_details, CF_PT_view, CF_PT_paper, CF_PT_frames, CF_PT_mask, CF_PT_path, CF_PT_export)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

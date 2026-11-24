"""Garment pattern, rigging, UV and Unity export tools for Blender."""
bl_info = {
    "name": "Avatar Clothing",
    "author": "Avatar Clothing contributors",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > Avatar Clothing",
    "category": "Mesh",
}

from collections import defaultdict
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform


def activate(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def unwrap(garment):
    """Use marked seams where present and an automatic island layout otherwise."""
    activate(garment)
    mesh = garment.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="Fabric UV")
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        if any(edge.use_seam for edge in mesh.edges):
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.018)
        else:
            bpy.ops.uv.smart_project(island_margin=0.018)
        bpy.ops.uv.average_islands_scale()
        bpy.ops.uv.pack_islands(rotate=True, margin=0.018)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def edge_key(a, b):
    return (a, b) if a < b else (b, a)


def rim_cuts(boundary):
    """Split each turned hem at one vertex so the narrow side wall unwraps."""
    neighbours = defaultdict(set)
    for a, b in boundary:
        neighbours[a].add(b)
        neighbours[b].add(a)
    remaining = set(neighbours)
    cuts = set()
    while remaining:
        first = min(remaining)
        cuts.add(first)
        todo = [first]
        while todo:
            current = todo.pop()
            if current in remaining:
                remaining.remove(current)
                todo.extend(neighbours[current] & remaining)
    return cuts


def armature_modifier(garment, rig):
    existing = next((mod for mod in garment.modifiers if mod.type == "ARMATURE" and mod.object == rig), None)
    if existing:
        return existing
    mod = garment.modifiers.new("Outfit rig", "ARMATURE")
    mod.object = rig
    return mod


def copy_shape_keys(body, cloth, vertex_sources, offsets):
    keys = body.data.shape_keys
    if not keys:
        return
    copies = {}
    for key in keys.key_blocks:
        new = cloth.shape_key_add(name=key.name, from_mix=False)
        copies[key.name] = new
        for i, original in enumerate(vertex_sources):
            new.data[i].co = key.data[original].co + offsets[i]
        new.slider_min, new.slider_max = key.slider_min, key.slider_max
        new.vertex_group = key.vertex_group
        if key == keys.reference_key:
            continue
        follow_shape(key, new)
    for key in keys.key_blocks:
        if key.relative_key:
            copies[key.name].relative_key = copies[key.relative_key.name]


def follow_shape(source, target):
    target.value = source.value
    driver = target.driver_add("value").driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.name = "body_shape"
    ref = variable.targets[0]
    ref.id_type = "KEY"
    ref.id = source.id_data
    ref.data_path = source.path_from_id("value")


def fitted_garment(body, face_indices, name="Fitted garment", clearance_mm=3.0, hem_mm=1.0):
    """Make an independent fabric surface with body weights and relative shape keys."""
    if body.type != "MESH" or not face_indices:
        raise ValueError("Select the body faces that the garment should cover.")
    if not name.strip():
        raise ValueError("Give the garment a name.")
    bpy.context.view_layer.update()
    if clearance_mm < 0 or not 0 <= hem_mm <= clearance_mm:
        raise ValueError("Hem depth must be between zero and the fabric clearance.")
    source = body.data
    keys = source.shape_keys
    if keys and not keys.use_relative:
        raise ValueError("This starter supports relative shape keys. Convert absolute keys on a working copy first.")
    if abs(body.matrix_world.to_3x3().determinant()) < 1e-12:
        raise ValueError("The body has a zero scale axis.")
    faces = [tuple(source.polygons[i].vertices) for i in sorted(set(face_indices))]
    original_ids = sorted({i for face in faces for i in face})
    new_ids = {original: i for i, original in enumerate(original_ids)}
    indices = list(original_ids)
    face_data = [tuple(new_ids[v] for v in face) for face in faces]
    edges = defaultdict(list)
    for face in face_data:
        for a, b in zip(face, face[1:] + face[:1]):
            edges[edge_key(a, b)].append((a, b))
    boundary = [uses[0] for uses in edges.values() if len(uses) == 1]
    seams = {edge_key(new_ids[e.vertices[0]], new_ids[e.vertices[1]]) for e in source.edges
             if e.use_seam and all(v in new_ids for v in e.vertices)}
    depths = [clearance_mm] * len(indices)
    if hem_mm and boundary:
        inner = {}
        for v in sorted({v for edge in boundary for v in edge}):
            inner[v] = len(indices)
            indices.append(original_ids[v])
            depths.append(clearance_mm - hem_mm)
        for a, b in boundary:
            face_data.append((b, a, inner[a], inner[b]))
            seams.add(edge_key(a, b))
        for v in rim_cuts(boundary):
            seams.add(edge_key(v, inner[v]))

    to_local = body.matrix_world.to_3x3().inverted()
    normal_matrix = to_local.transposed()
    units = 1000 * (bpy.context.scene.unit_settings.scale_length or 1.0)
    directions = {v: to_local @ (normal_matrix @ source.vertices[v].normal).normalized() for v in original_ids}
    offsets = [directions[v] * (mm / units) for v, mm in zip(indices, depths)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([source.vertices[v].co + offset for v, offset in zip(indices, offsets)], [], face_data)
    mesh.update()
    cloth = bpy.data.objects.new(name, mesh)
    (body.users_collection[0] if body.users_collection else bpy.context.scene.collection).objects.link(cloth)
    cloth.parent = body.parent
    if body.parent:
        cloth.parent_type = body.parent_type
        cloth.parent_bone = body.parent_bone
    cloth.matrix_world = body.matrix_world.copy()
    for edge in mesh.edges:
        edge.use_seam = edge_key(*edge.vertices) in seams
    for poly in mesh.polygons:
        poly.use_smooth = True
    material = bpy.data.materials.get("Outfit fabric") or bpy.data.materials.new("Outfit fabric")
    mesh.materials.append(material)
    groups = [cloth.vertex_groups.new(name=g.name) for g in body.vertex_groups]
    for i, original in enumerate(indices):
        for weight in source.vertices[original].groups:
            if weight.weight > 0:
                groups[weight.group].add([i], weight.weight, "REPLACE")
    rig = body.find_armature()
    if rig:
        target = armature_modifier(cloth, rig)
        old = next((m for m in body.modifiers if m.type == "ARMATURE" and m.object == rig), None)
        if old:
            for prop in ("use_deform_preserve_volume", "use_vertex_groups", "use_bone_envelopes", "vertex_group", "invert_vertex_group"):
                setattr(target, prop, getattr(old, prop))
    copy_shape_keys(body, cloth, indices, offsets)
    unwrap(cloth)
    return cloth


def shape_order(body, cloth):
    keys = body.data.shape_keys
    if not keys:
        return []
    existing = cloth.data.shape_keys
    if not keys.use_relative or (existing and not existing.use_relative):
        raise ValueError("Matching body shapes requires relative shape keys.")
    pending = [k for k in keys.key_blocks if k != keys.reference_key]
    if existing and any(k.name in existing.key_blocks for k in pending):
        raise ValueError("Matching shape keys already exist on the garment. Turn off Match Body Shapes to keep them.")
    names = {keys.reference_key.name}
    ordered = []
    while pending:
        ready = [k for k in pending if k.relative_key and k.relative_key.name in names]
        if not ready:
            raise ValueError("The body's shape keys form a cycle.")
        ordered.extend(ready)
        names.update(k.name for k in ready)
        pending = [k for k in pending if k not in ready]
    return ordered


def project_shapes(body, cloth, keys, samples, body_to_cloth):
    if not keys:
        return
    basis = cloth.data.shape_keys.reference_key if cloth.data.shape_keys else cloth.shape_key_add(name="Basis")
    copied = {body.data.shape_keys.reference_key.name: basis}
    for key in keys:
        parent = key.relative_key
        target = cloth.shape_key_add(name=key.name, from_mix=False)
        target.relative_key = copied[parent.name]
        target.slider_min, target.slider_max = key.slider_min, key.slider_max
        mask = body.vertex_groups.get(key.vertex_group) if key.vertex_group else None
        delta = [p.co - q.co for p, q in zip(key.data, parent.data)]
        if mask:
            for i, v in enumerate(body.data.vertices):
                delta[i] *= next((w.weight for w in v.groups if w.group == mask.index), 0.0)
        for i, ((a,b,c), (u,v,w)) in enumerate(samples):
            target.data[i].co = target.relative_key.data[i].co + body_to_cloth @ (u*delta[a] + v*delta[b] + w*delta[c])
        follow_shape(key, target)
        copied[key.name] = target


def bind_garment(body, cloth, match_shapes=False):
    """Interpolate deform weights from the nearest body triangle in world space."""
    if not body or not cloth or body == cloth or body.type != "MESH" or cloth.type != "MESH":
        raise ValueError("Choose a rigged body in the panel and an independent garment as the active mesh.")
    rig = body.find_armature()
    if not rig:
        raise ValueError("The source body must be attached to an armature.")
    if cloth.find_armature() not in (None, rig):
        raise ValueError("The garment already uses a different armature.")
    bpy.context.view_layer.update()
    keys = shape_order(body, cloth) if match_shapes else []
    if keys and abs(cloth.matrix_world.to_3x3().determinant()) < 1e-12:
        raise ValueError("The garment has a zero scale axis.")
    body_to_cloth = cloth.matrix_world.to_3x3().inverted() @ body.matrix_world.to_3x3() if keys else None
    source = body.data
    source.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in source.loop_triangles if t.area > 1e-12]
    if not triangles or not cloth.data.vertices:
        raise ValueError("Both meshes need usable geometry.")
    positions = [body.matrix_world @ v.co for v in source.vertices]
    tree = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    bones = {b.name for b in rig.data.bones if b.use_deform}
    names = {g.index: g.name for g in body.vertex_groups if g.name in bones}
    weights, samples = [], []
    basis = (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    for vertex in cloth.data.vertices:
        point, _, tri_index, _ = tree.find_nearest(cloth.matrix_world @ vertex.co)
        if point is None:
            raise ValueError("No nearby body surface was found.")
        corners = triangles[tri_index]
        blend = barycentric_transform(point, *(positions[i] for i in corners), *basis)
        if keys:
            samples.append((corners, tuple(blend)))
        result = defaultdict(float)
        for index, factor in zip(corners, blend):
            for weight in source.vertices[index].groups:
                if weight.group in names:
                    result[names[weight.group]] += max(0.0, factor) * weight.weight
        strongest = sorted(((n, w) for n, w in result.items() if w > 1e-8), key=lambda pair: -pair[1])[:4]
        total = sum(w for _, w in strongest)
        if not total:
            raise ValueError(f"Body surface nearest garment vertex {vertex.index} has no deform weights.")
        weights.append([(n, w / total) for n, w in strongest])
    # Make changes only after every garment vertex has a valid result.
    all_vertices = list(range(len(weights)))
    for group in cloth.vertex_groups:
        if group.name in bones:
            group.remove(all_vertices)
    group_map = {}
    for i, row in enumerate(weights):
        for name, value in row:
            if name not in group_map:
                group_map[name] = cloth.vertex_groups.get(name) or cloth.vertex_groups.new(name=name)
            group_map[name].add([i], value, "REPLACE")
    if not cloth.parent:
        world = cloth.matrix_world.copy()
        cloth.parent = rig
        cloth.matrix_world = world
    armature_modifier(cloth, rig)
    project_shapes(body, cloth, keys, samples, body_to_cloth)


def uv_guide(obj, path):
    mesh = obj.data
    layer = mesh.uv_layers.active
    if not layer:
        raise ValueError(f"{obj.name} needs a UV map.")
    segments = set()
    for poly in mesh.polygons:
        points = [tuple(round(float(x), 6) for x in layer.data[i].uv) for i in poly.loop_indices]
        for a, b in zip(points, points[1:] + points[:1]):
            segments.add(tuple(sorted((a, b))))
    lines = [f"M{a[0]*1024:.3f} {(1-a[1])*1024:.3f}L{b[0]*1024:.3f} {(1-b[1])*1024:.3f}" for a, b in sorted(segments)]
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">' +
                    '<path fill="none" stroke="#666" stroke-width="1" d="' + " ".join(lines) + '"/></svg>\n', encoding="utf-8")


def export_outfit(objects, filepath):
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes:
        raise ValueError("Select one or more garment meshes.")
    rigs = {obj.find_armature() for obj in meshes}
    if len(rigs) != 1 or None in rigs:
        raise ValueError("All selected clothing pieces must use the same armature.")
    rig = rigs.pop()
    shape_keys = any(obj.data.shape_keys for obj in meshes)
    for obj in meshes:
        if not obj.data.uv_layers.active:
            raise ValueError(f"{obj.name} needs a UV map before export.")
        if shape_keys and any(m.show_viewport and m.type != "ARMATURE" for m in obj.modifiers):
            raise ValueError(f"Bake the modeling modifiers on a working copy of {obj.name} to preserve the outfit's shape keys.")
    selected_before = list(bpy.context.selected_objects)
    active_before = bpy.context.view_layer.objects.active
    path = Path(filepath).resolve().with_suffix(".fbx")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        activate(meshes[0])
        for obj in [rig, *meshes]:
            obj.select_set(True)
        if not all(obj in bpy.context.selected_objects for obj in [rig, *meshes]):
            raise ValueError("Make the rig and garment meshes visible and selectable before export.")
        result = bpy.ops.export_scene.fbx(
            filepath=str(path), use_selection=True, object_types={"MESH", "ARMATURE"},
            add_leaf_bones=False, bake_anim=False, use_mesh_modifiers=not shape_keys,
            path_mode="COPY", embed_textures=True, axis_forward="-Z", axis_up="Y",
        )
        if "FINISHED" not in result:
            raise ValueError("Blender could not export this outfit.")
        for i, obj in enumerate(meshes, start=1):
            uv_guide(obj, path.parent / f"{path.stem}_{i}_{bpy.path.clean_name(obj.name)}_UV.svg")
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected_before:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active_before
    return path


class ClothingSettings(bpy.types.PropertyGroup):
    body: PointerProperty(name="Body", type=bpy.types.Object, poll=lambda self, obj: obj.type == "MESH")
    garment_name: StringProperty(name="Name", default="Fitted garment")
    clearance: FloatProperty(name="Clearance (mm)", default=3.0, min=0.0, precision=1)
    hem: FloatProperty(name="Edge depth (mm)", default=1.0, min=0.0, precision=1)
    match_shapes: BoolProperty(name="Match Body Shapes", default=False)


class CLOTHING_OT_pattern(bpy.types.Operator):
    bl_idname = "avatar_clothing.pattern"
    bl_label = "Make Garment from Selected Faces"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = context.active_object
        if not body or body.type != "MESH" or context.mode != "EDIT_MESH":
            self.report({"ERROR"}, "In Edit Mode on the body, select the faces for your garment.")
            return {"CANCELLED"}
        if len(context.objects_in_mode) != 1:
            self.report({"ERROR"}, "Edit one body mesh at a time.")
            return {"CANCELLED"}
        body.update_from_editmode()
        selection = [p.index for p in body.data.polygons if p.select]
        opts = context.scene.avatar_clothing
        try:
            fitted_garment(body, selection, opts.garment_name, opts.clearance, opts.hem)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        opts.body = body
        return {"FINISHED"}


class CLOTHING_OT_bind(bpy.types.Operator):
    bl_idname = "avatar_clothing.bind"
    bl_label = "Transfer Body Weights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            opts = context.scene.avatar_clothing
            bind_garment(opts.body, context.active_object, opts.match_shapes)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHING_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "avatar_clothing.export"
    bl_label = "Export Outfit FBX + UV Guides"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    def execute(self, context):
        try:
            export_outfit(context.selected_objects, self.filepath)
        except (ValueError, OSError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VIEW3D_PT_avatar_clothing(bpy.types.Panel):
    bl_label = "Avatar Clothing"
    bl_idname = "VIEW3D_PT_avatar_clothing"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Avatar Clothing"

    def draw(self, context):
        layout = self.layout
        opts = context.scene.avatar_clothing
        col = layout.column(align=True)
        col.prop(opts, "garment_name")
        col.prop(opts, "clearance")
        col.prop(opts, "hem")
        col.operator("avatar_clothing.pattern")
        layout.separator()
        col = layout.column(align=True)
        col.enabled = context.mode == "OBJECT"
        col.prop(opts, "body")
        col.prop(opts, "match_shapes")
        col.operator("avatar_clothing.bind")
        col.separator()
        col.operator("avatar_clothing.export")


CLASSES = (ClothingSettings, CLOTHING_OT_pattern, CLOTHING_OT_bind, CLOTHING_OT_export, VIEW3D_PT_avatar_clothing)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.avatar_clothing = PointerProperty(type=ClothingSettings)


def unregister():
    del bpy.types.Scene.avatar_clothing
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

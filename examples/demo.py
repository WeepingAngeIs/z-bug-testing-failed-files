"""Build an original dress form, fit a sweetheart bodice, and check the exported FBX."""
from math import cos, exp, pi, radians, sin
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
import avatar_clothing as clothing

SEGMENTS = 64
FRONT = Vector((0, -1, 0))
PROFILE = [
    (.73, .196, .137), (.80, .226, .155), (.88, .245, .168), (.95, .226, .153),
    (1.03, .182, .125), (1.095, .163, .114), (1.16, .172, .124), (1.22, .198, .145),
    (1.28, .219, .155), (1.33, .225, .151), (1.38, .234, .135), (1.42, .229, .117),
    (1.45, .204, .100), (1.475, .146, .080), (1.50, .070, .062),
]


def material(name, rgb, roughness=.55, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*rgb, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return mat


def mesh_object(name, coords, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(coords, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def ends(a):
    # The center dips into a sweetheart neckline and a pointed front hem.
    front = max(0, cos(a))
    hem = .994 - .080 * front**8 - .025 * max(0, -cos(a))**4
    neck = 1.303 + .083 * front + .016 * max(0, -cos(a)) - .050 * front**16
    return hem, neck


def point(z, a, offset=0):
    for (lo, w0, d0), (hi, w1, d1) in zip(PROFILE, PROFILE[1:]):
        if z <= hi + .00001:
            t = min(1., max(0., (z-lo)/(hi-lo)))
            w, d = w0 + (w1-w0)*t, d0 + (d1-d0)*t
            return Vector(((w+offset)*sin(a), -(d+offset)*cos(a), z))
    raise ValueError(z)


def body_mesh(linen, rig):
    coords, faces, pattern = [], [], []
    for row in range(33):
        for j in range(SEGMENTS):
            a = 2*pi*j/SEGMENTS
            hem, neck = ends(a)
            if row <= 8:
                z = .73 + (hem-.73)*row/8
            elif row <= 24:
                z = hem + (neck-hem)*(row-8)/16
            else:
                z = neck + (1.5-neck)*(row-24)/8
            coords.append(point(z, a))
    for row in range(32):
        for j in range(SEGMENTS):
            n = (j+1) % SEGMENTS
            if 8 <= row < 24:
                pattern.append(len(faces))
            faces.append((row*SEGMENTS+j, row*SEGMENTS+n, (row+1)*SEGMENTS+n, (row+1)*SEGMENTS+j))
    faces += [tuple(reversed(range(SEGMENTS))), tuple(range(32*SEGMENTS,33*SEGMENTS))]
    obj = mesh_object("Dress form", coords, faces)
    obj.data.materials.append(linen)
    for edge in obj.data.edges:
        a,b = edge.vertices
        if a % SEGMENTS == b % SEGMENTS == SEGMENTS//2:
            edge.use_seam = True
    obj.parent = rig
    clothing.armature_modifier(obj, rig)
    groups = [obj.vertex_groups.new(name=name) for name in ("Hips", "Spine", "Chest")]
    for v in obj.data.vertices:
        t = max(0., min(2., (v.co.z - .93) / .205))
        for i, group in enumerate(groups):
            weight = max(0., 1-abs(t-i))
            if weight:
                group.add([v.index], weight, "REPLACE")
    obj.shape_key_add(name="Basis")
    size = obj.shape_key_add(name="Waist ease")
    for p in size.data:
        expand = 1 + .13*exp(-((p.co.z-1.095)/.085)**2)
        p.co.x *= expand
        p.co.y *= expand
    return obj, pattern


def make_rig():
    arm = bpy.data.armatures.new("Dress form rig")
    rig = bpy.data.objects.new("Dress form rig", arm)
    bpy.context.scene.collection.objects.link(rig)
    clothing.activate(rig)
    bpy.ops.object.mode_set(mode="EDIT")
    last = None
    for name, lo, hi in [("Hips", .71, 1.01), ("Spine", 1.01, 1.24), ("Chest", 1.24, 1.51)]:
        bone = arm.edit_bones.new(name)
        bone.head, bone.tail, bone.parent = (0,0,lo), (0,0,hi), last
        if last:
            bone.use_connect = True
        last = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def make_piping(name, top, mat, body, cloth):
    coords, faces = [], []
    edge_row = 16 if top else 0
    ring = [cloth.matrix_world @ cloth.data.vertices[edge_row*SEGMENTS+j].co for j in range(SEGMENTS)]
    for j in range(SEGMENTS):
        a = 2*pi*j/SEGMENTS
        tangent = (ring[(j+1)%SEGMENTS] - ring[j-1]).normalized()
        radial = Vector((sin(a), -cos(a), 0))
        outward = (radial - tangent * radial.dot(tangent)).normalized()
        lift = outward.cross(tangent)
        center = ring[j] + .0004*outward
        for k in range(8):
            b = 2*pi*k/8
            coords.append(center + (outward*cos(b) + lift*sin(b)) * .00225)
    for j in range(SEGMENTS):
        for k in range(8):
            n,h = (j+1)%SEGMENTS,(k+1)%8
            faces.append((j*8+k,n*8+k,n*8+h,j*8+h))
    obj = mesh_object(name, coords, faces)
    obj.data.materials.append(mat)
    for e in obj.data.edges:
        a,b=e.vertices
        e.use_seam = (a//8 == b//8 == 0) or (a%8 == b%8 == 4)
    clothing.unwrap(obj)
    clothing.bind_garment(body, obj, match_shapes=True)
    return obj


def ellipsoid(name, location, scale, mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=28, ring_count=14, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.use_smooth = True
    return obj


def cylinder(name, z, radius, depth, mat, bevel=.004):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=radius, depth=depth, location=(0,0,z))
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    mod = obj.modifiers.new("Rounded edge", "BEVEL")
    mod.width, mod.segments = bevel, 3
    for p in obj.data.polygons:
        p.use_smooth = len(p.vertices)==4
    return obj


def area(name, position, energy, size, color):
    bpy.ops.object.light_add(type="AREA", location=position)
    lamp = bpy.context.object
    lamp.name = name
    lamp.rotation_euler = (Vector((0,0,1.12)) - lamp.location).to_track_quat("-Z", "Y").to_euler()
    lamp.data.energy, lamp.data.shape, lamp.data.size, lamp.data.color = energy, "DISK", size, color


def camera_view(camera, position):
    camera.location = position
    camera.rotation_euler = (Vector((0,0,1.105)) - camera.location).to_track_quat("-Z", "Y").to_euler()


def validate_export(path, names):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert {o.name for o in meshes} == set(names), "Body or studio geometry leaked into the outfit export"
    rigs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    assert len(rigs)==1 and {b.name for b in rigs[0].data.bones}=={"Hips","Spine","Chest"}
    for obj in meshes:
        assert obj.find_armature() == rigs[0] and obj.data.uv_layers.active
        for v in obj.data.vertices:
            assert abs(sum(g.weight for g in v.groups)-1) < .003, (obj.name, v.index)
    obj = bpy.data.objects["Sweetheart bodice"]
    keys = obj.data.shape_keys.key_blocks
    assert "Waist ease" in keys
    assert max((p.co-q.co).length for p,q in zip(keys["Waist ease"].data,keys[0].data)) > .006
    uvs = [v.uv for v in obj.data.uv_layers.active.data]
    assert max(v.x for v in uvs) - min(v.x for v in uvs) > .5
    assert all(-.001 <= channel <= 1.001 for uv in uvs for channel in uv)
    vg = obj.vertex_groups.get("Chest")
    vertex = next(v for v in obj.data.vertices if any(w.group == vg.index and w.weight > .8 for w in v.groups))
    def posed():
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return evaluated.matrix_world @ evaluated.data.vertices[vertex.index].co
    rest = posed()
    pb = rigs[0].pose.bones["Spine"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler.x = radians(12)
    assert (posed() - rest).length > .005, "The exported garment did not follow the skeleton"


def main():
    dest = Path(sys.argv[sys.argv.index("--")+1]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    linen = material("Unbleached linen", (.63,.48,.37), .86)
    rose = material("Mulberry satin", (.27,.031,.079), .43)
    inset = material("Rose side panels", (.38,.064,.118), .50)
    brass = material("Warm brass", (.61,.32,.12), .25,.72)
    piping = material("Champagne piping", (.84,.61,.39), .41,.22)
    stem = material("Walnut", (.12,.066,.047))
    rig = make_rig()
    body, pattern = body_mesh(linen, rig)
    original = len(body.data.vertices), len(body.data.polygons)
    clothing.register()
    clothing.activate(body)
    chosen = set(pattern)
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    for p in body.data.polygons:
        p.select = p.index in chosen
    opts = bpy.context.scene.avatar_clothing
    opts.garment_name, opts.clearance, opts.hem = "Sweetheart bodice", 3.5, 1.25
    bpy.ops.object.mode_set(mode="EDIT")
    assert "FINISHED" in bpy.ops.avatar_clothing.pattern()
    cloth = bpy.context.active_object
    assert len(cloth.data.polygons) == len(pattern) + 2*SEGMENTS
    assert original == (len(body.data.vertices),len(body.data.polygons))
    cloth.data.materials[0] = rose
    cloth.data.materials.append(inset)
    for i, poly in enumerate(cloth.data.polygons[:len(pattern)]):
        section = i % SEGMENTS
        if 4 <= section <= 12 or 51 <= section <= 59 or 24 <= section <= 39:
            poly.material_index = 1
    body.data.shape_keys.key_blocks["Waist ease"].value = .6
    bpy.context.view_layer.update()
    assert abs(cloth.data.shape_keys.key_blocks["Waist ease"].value - .6) < 1e-4
    body.data.shape_keys.key_blocks["Waist ease"].value = 0
    bpy.context.view_layer.update()
    pieces = [cloth, make_piping("Neckline piping", True, piping, body, cloth), make_piping("Hem piping", False, piping, body, cloth)]
    for i, z in enumerate((1.005,1.075,1.145,1.215,1.285)):
        center = point(z, 0, .006)
        button = ellipsoid(f"Brass button {i+1}", center, (.006,.0045,.006), brass)
        clothing.bind_garment(body,button,match_shapes=True)
        pieces.append(button)
    cylinder("Stand foot",.05,.153,.040,stem)
    cylinder("Stand",.41,.021,.70,brass)
    cylinder("Base fitting",.725,.094,.036,stem)
    cylinder("Finial collar",1.512,.068,.027,brass)
    cylinder("Finial stem",1.54,.027,.04,stem)
    ellipsoid("Finial", (0,0,1.572), (.035,.035,.035), brass)
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new("Studio")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.32,.26,.30,1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .6
    bpy.ops.mesh.primitive_plane_add(size=200)
    bpy.context.object.name = "Studio floor"
    bpy.context.object.data.materials.append(material("Dusty lavender studio",(.24,.19,.23),.9))
    area("Softbox",(-1.75,-2.50,3.20),190,2.0,(1,.93,.88))
    area("Fill",(1.8,-1.1,2.0),80,1.5,(.88,.91,1))
    area("Rim",(1.1,1.4,2.7),175,1.3,(1,.83,.74))
    bpy.ops.object.camera_add()
    cam=bpy.context.object
    scene.camera=cam
    cam.data.type,cam.data.ortho_scale="ORTHO",1.11
    camera_view(cam,(1.25,-3.3,1.67))
    scene.render.engine="CYCLES"
    scene.cycles.device="CPU"
    scene.cycles.samples=12
    scene.cycles.use_denoising=True
    scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage=640,640,100
    scene.render.image_settings.file_format="PNG"
    clothing.activate(cloth)
    bpy.ops.wm.save_as_mainfile(filepath=str(dest/"Dress form.blend"))
    export=clothing.export_outfit(pieces, dest/"Sweetheart bodice.fbx")
    names=[p.name for p in pieces]
    for label, pos in [("front",(1.25,-3.3,1.67)),("back",(-1.35,3.2,1.69))]:
        camera_view(cam,pos)
        scene.render.filepath=str(dest/f"{label}.png")
        bpy.ops.render.render(write_still=True)
    validate_export(export,names)
    assert len(list(dest.glob("*_UV.svg"))) >= len(names)
    print("PASS: pattern, turned hems, live body morph, transferred weights, UV guides, FBX mesh and morph round-trip, posed skeleton.")


if __name__ == "__main__":
    main()

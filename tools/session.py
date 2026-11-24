"""Entry point for the editor, model inspection and headless outfit exports."""
import argparse
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))
import avatar_clothing


def load_model(path):
    suffix = path.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    elif suffix in {".fbx", ".glb", ".gltf"}:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if suffix == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(path))
        else:
            bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError("Open a .blend, .fbx, .gltf, or .glb avatar.")


def inspect():
    scene = bpy.context.scene
    meshes = []
    for obj in sorted(scene.objects, key=lambda o: o.name):
        if obj.type != "MESH":
            continue
        mesh = obj.data
        rig = obj.find_armature()
        meshes.append({
            "name": obj.name,
            "vertices": len(mesh.vertices),
            "faces": len(mesh.polygons),
            "armature": rig.name if rig else None,
            "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
            "uv_layers": list(mesh.uv_layers.keys()),
            "shape_keys": list(mesh.shape_keys.key_blocks.keys()) if mesh.shape_keys else [],
        })
    return {"armatures": {o.name: len(o.data.bones) for o in scene.objects if o.type == "ARMATURE"}, "meshes": meshes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["open", "inspect", "export"])
    parser.add_argument("model", type=Path, nargs="?")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mesh", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if args.model:
        load_model(args.model)
    if args.command == "open":
        avatar_clothing.register()
        return
    if args.command == "inspect":
        report = json.dumps(inspect(), indent=2, ensure_ascii=False)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report + "\n", encoding="utf-8")
        else:
            print(report)
        return
    meshes = []
    for name in args.mesh:
        obj = bpy.context.scene.objects.get(name)
        if obj is None or obj.type != "MESH":
            raise ValueError(f"No mesh named '{name}' in this scene. Inspect the model for its exact names.")
        meshes.append(obj)
    avatar_clothing.export_outfit(meshes, args.output)


if __name__ == "__main__":
    main()

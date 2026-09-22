"""Make the CAD watertight, so the geometry can be used exactly instead of rasterised.

Run under Blender, headless:

    /home/IWS/public/blender/blender -b --python cases/munich-vsf/repair_cad.py -- [part.stl ...]

**Why this exists.** None of the eleven Munich CAD parts is a solid: they carry 150 to
39,554 open edges each, and `substratum.stl` 5,726 non-manifold ones. Verified not to
be an artefact of STL's per-triangle vertices - the points are already merged
(n_points = n_faces/2) and the holes survive a merge-by-distance.

An open surface cannot be sliced (the contours do not close) and cannot be booleaned,
which is why `core.surfaces.wall_footprints` rasterises at `surfaces.resolution` and
hole-fills instead. That workaround costs **0.036 m of the fish pass's 0.1697 m
throat**, and the structure clears its own wall tops by 22 mm, so the workaround is the
level problem.

Originals are left alone; repaired parts are written to `cad-repaired/`.
"""

import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
SRC = HERE / "user-sources" / "geodata" / "cad"
OUT = HERE / "user-sources" / "geodata" / "cad-repaired"

#: Merge vertices closer than this. The throat is 0.17 m and the CAD is drawn to the
#: millimetre, so 0.1 mm closes float-level seams without moving anything real.
MERGE = 1.0e-4


def repair(path: Path) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_mesh.stl(filepath=str(path))
    obj = bpy.context.selected_objects[0]
    bpy.context.view_layer.objects.active = obj
    before = (len(obj.data.vertices), len(obj.data.polygons))

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=MERGE)
    bpy.ops.mesh.delete_loose()
    bpy.ops.mesh.dissolve_degenerate()
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.fill_holes(sides=0)            # 0 = any number of sides
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

    # the 3D-Print Toolbox's own cleanup, which handles what fill_holes cannot
    try:
        bpy.ops.preferences.addon_enable(module="object_print3d_utils")
        bpy.ops.mesh.print3d_clean_non_manifold()
    except Exception as exc:                     # noqa: BLE001
        print(f"  [{path.name}] print3d cleanup unavailable: {exc}")

    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_mesh.stl(filepath=str(OUT / path.name), use_selection=False,
                            ascii=False)
    after = (len(obj.data.vertices), len(obj.data.polygons))
    return {"before": before, "after": after}


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    names = argv or sorted(p.name for p in SRC.glob("*.stl"))
    for name in names:
        src = SRC / name
        if not src.is_file():
            print(f"  [{name}] not found, skipped")
            continue
        info = repair(src)
        print(f"  [{name}] {info['before'][1]:,} faces -> {info['after'][1]:,}; "
              f"{info['before'][0]:,} verts -> {info['after'][0]:,}")


if __name__ == "__main__":
    main()

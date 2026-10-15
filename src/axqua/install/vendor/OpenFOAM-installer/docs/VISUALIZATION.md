# Visualize results

Linux needs a graphical desktop; Windows uses WSL2 + WSLg. On a headless server,
copy the case/export to a desktop. The installer uses your distribution's signed
ParaView package and the SHA256-verified [VisIt 3.5.0 OS-specific binaries](https://github.com/visit-dav/visit/releases/tag/v3.5.0).

**ParaView:** activate the installed environment, run `touch case.foam` inside
your case, and open that file in ParaView. Select `internalMesh`, `bedWall` and
`freeSurface`, enable fields such as `Conc`, `U` and `p`, press **Apply**, color
by a field, and press Play. For BAW water-air cases, select `alpha.water` and
`p_rgh` instead. The built-in OpenFOAM reader needs no OpenFOAM-linked plugin.

**VisIt:** with OpenFOAM activated, run:

```bash
python3 /path/to/repo/postprocess.py /path/to/case
```

It exports legacy VTK and prints the `.visit` files to open in VisIt. Open the
volume manifest, choose **Add > Pseudocolor > Conc**, then **Draw** and Play.
Open the separate `bedWall`/`freeSurface` manifests for changing bed and surface
geometry. BAW cases use `alpha.water`. Missing fields mean they were not written
by the case; the installer does not manufacture them.

Each `.visit` file preserves actual simulation times using `!TIME`; numbered
VTK filenames are not assumed to be physical times. Per-time mesh coordinates
show bed/surface movement. Keep all timestep mesh files and exported VTK files;
never combine different patches as successive timesteps. Reconstruct parallel
results, including changing meshes, before this serial export. If a direct
ParaView reader fails, open the exported VTK sequence instead.
To update manifests after additional timesteps, move the earlier `.visit` files
aside and rerun: existing differing manifests are preserved, never overwritten.

Use the installer's clean-environment GUI launchers: OpenFOAM library paths can
conflict with the visualization applications' bundled libraries. Do not load the
BAW `.so` into ParaView or VisIt; it belongs to the simulation, not the viewer.

Sources: [OpenFOAM reader](https://www.paraview.org/paraview-docs/v5.12.0/python/paraview.simple.OpenFOAMReader.html),
[OpenCFD VTK export](https://www.openfoam.com/news/main-news/openfoam-v1812/post-processing),
[VisIt time-series format](https://visit-sphinx-github-user-manual.readthedocs.io/en/v3.5.0/using_visit/WorkingWithFiles/Supported_File_Types.html#creating-visit-files).

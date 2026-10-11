<div align="center">

<img src="src/geostitch/assets/app_icon.png" alt="GeoStitch icon" width="80">

# GeoStitch

**Align raster images, assign real-world coordinates, and export them for GIS.**

Windows desktop app · Python 3.10+

</div>

GeoStitch helps you position related images using matching points, georeference them using map coordinates, and export each image for use in GIS software. It does not combine source images into one mosaic; each image remains a separate layer and export.

## Get started

To install from a source checkout:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m geostitch
```

Import images with **File > Import images** or drag them into the window. Save work with **Ctrl+S**; projects use `.gsp` files and refer to the original images rather than copying them. Keep those files in place or use **File > Repair missing images** if they move.

## Basic workflow

1. **Choose the project CRS** in the selector at the bottom-right. Search by CRS name or EPSG code, or type another valid CRS. Switching between degrees and metres may ask whether to keep the same numeric georeferencing accuracy limit.
2. **Arrange images** on the canvas. Drag to move; select an image to scale or rotate it with its handles.
3. Turn on **Add points** (`P`) and click recognizable features. Add points to every image where those features appear.
4. Turn on **Link points** (`L`) and click matching point pairs in different images. Run **Image > Align** to fit the linked images.
5. Enter map coordinates for control points in the selected project CRS, or select a point and turn on **Pick map coordinate** (`G`) to click the World Map. Run **Image > Georeference**.
6. Choose **File > Export georeferenced images** to save the solved images as GeoTIFFs or images with world files.

Use **A** to switch between the original arranged layout and the solver-aligned layout. Alignment and georeferencing can be undone with **Ctrl+Z**.

## Workspace

- **Layers** lists images, points, links, and imported GeoJSON. Select a row to see its details. Use the lock icons to protect an image's arranged position or a point's image position.
- **Image Canvas** is where you arrange images, add points, and link matches.
- **Image View** shows the selected image for closer point placement. Clip polygons can limit the area included when exporting.
- **World Map** shows georeferenced image footprints over OpenStreetMap. Uncached map tiles need an internet connection.
- **Link Review** shows linked image points side by side so you can inspect and correct them.

## Solver settings

The default alignment model is **Similarity**: it moves, rotates, and uniformly resizes images. **Translation** and **Rigid** restrict which changes are allowed; **Affine**, **Projective**, and **Deformable (TPS)** allow more complex fits and need suitable, well-spread control points. TPS alignment cannot be combined with georeferencing in one run.

Georeferencing fits image points to their map coordinates. The chosen CRS and model determine which coordinate systems and control layouts are suitable. Readiness checks and solver messages identify missing or weak controls before or after a fit. Use the settings gear beside **Align** or **Georeference** to change the model, accuracy target, or advanced options.

## Export

Exports are per image, not a merged raster. Available formats include GeoTIFF and PNG/JPEG with world files. You can choose an output CRS, pixel size, resampling method, and optional shared bounds. Clip polygons are applied during export. Some nonlinear or cross-CRS mappings require inverse-warp export; warped JPEG output is not supported.

<details>
<summary>Keyboard shortcuts</summary>

| Action | Shortcut |
| --- | --- |
| Add points | `P` |
| Link points | `L` |
| Pick a map coordinate | `G` |
| Lock or unlock selected images/points | `K` |
| Switch aligned/rest canvas layout | `A` |
| Select all / deselect all | `Ctrl+A` / `Ctrl+D` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Y` |
| Import images | `Ctrl+I` |
| Align | `Ctrl+Shift+M` |
| Georeference | `Ctrl+Shift+G` |
| Export | `Ctrl+E` |
| Fit current view | `Ctrl+Shift+F` |
| Arrange images | `Ctrl+Shift+A` |
| Delete selection | `Delete` |

Mouse wheel zooms; middle-drag pans. Shortcuts can be changed under **Settings > Keyboard shortcuts**.

</details>

<details>
<summary>When something goes wrong</summary>

- **Images are missing:** use **File > Repair missing images** and point the project to the original files. Point coordinates refer to the original image pixels, so avoid replacing an image with a resized copy.
- **Alignment is weak or misses its target:** add matching points across the image area and check the highest-error links in the solver report or Link Review.
- **Georeferencing is weak:** check the project CRS and point map coordinates, then spread controls across the image area. Independent checkpoints can help assess the result without fitting to those points.
- **A solver is blocked:** open **Image > Solver diagnostics** for the affected images, points, and links.
- **Export does not match expectations:** confirm that the correct images have world transforms, review the output CRS and pixel size, and check whether the chosen format supports the mapping type.

</details>

<details>
<summary>Development</summary>

Install test dependencies and run the test suite:

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

Launch from a checkout with `python -m geostitch`. Executable builds use `packaging/build.ps1`; the installer requires Inno Setup 6. The [transform and warp roadmap](docs/transform-and-warp-roadmap.md) contains implementation details and planned work.

</details>

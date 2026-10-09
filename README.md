# GeoStitch Control

GeoStitch Control is a PySide6 desktop workspace for marking corresponding control points across raster images and recording their map coordinates.

## Run

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m geostitch
```

In VS Code, select **GeoStitch: desktop app** in **Run and Debug** and press **F5** to launch the GUI under the debugger.

Use **Settings → Keyboard shortcuts...** to customize command shortcuts. Changes are saved for future launches, duplicate assignments are rejected, and **Restore defaults** resets the built-in bindings.

Commands are grouped in the menu bar by **File** (project and image import), **Edit** (Undo/Redo project changes), **Image** (arrangement and simulation), **View** (fit and panel visibility), **Control Points**, and **Constraints**. **Settings** contains keyboard shortcut customization. Undo/redo covers project data edits, including point and constraint changes, image imports/removals, arrangement, and image transforms; viewport-only changes such as pan and zoom are not recorded. The **Images**, **Image Canvas**, **Control Points**, and **World Map** panels can be docked, floated, closed, or split horizontally/vertically by dragging a panel title bar to another panel's edge. Their layout is restored on the next launch. Use **View → Reset panel layout** to restore the default arrangement.

To run the model tests:

```powershell
python -m pytest
```

## Workflow

1. Import images with **Import images** or drag image files into the window. New images are arranged in a balanced, near-square grid on the shared canvas.
2. Drag an image to reposition it. Select one layer in the image list or click it to show transform handles: drag a corner handle to scale proportionally, or drag the handle above the image to rotate. **Arrange images** rebuilds the near-square grid. **Fit current view** (View menu or `Ctrl+Shift+F`) fits the panel you are working in: the selected image, or all layers if none is selected, on the canvas; the georeferenced footprints on the world map; or the image in the image view.
3. Toggle **Add points** on, then click image features continuously. Each click creates a control point that belongs to the clicked image, with an increasing image-based name (for example, `survey-001`). The active image selection stays unchanged while adding points. Toggle **Add points** off to return to moving image layers.
4. Click a point marker on the canvas or in the Image View, or pick it from the Details lists, to select it and edit its name, label, map coordinates and pixel position. Selection alone does not move a point; unlocked markers can be dragged to reposition them. Check **Lock position in image** to prevent dragging or pixel edits. Each point is stored in its image's pixel coordinates and belongs to exactly one image; points in different images are related only through links.
5. Toggle **Link points** on, click a point marker, then click a marker in a different image to add a tie constraint. The source marker highlights while you choose its match; after each pair the tool resets for the next pair and remains enabled. Alternatively, select one control-point row and choose **Link selected point to...**, then choose its target. The dashed scene line shows a connected pair. Select a link and choose **Remove link** to delete it.
6. Save the project as a `.gsp` file. It stores image paths and transforms, point names and optional labels, observations, map coordinates, the CRS identifier, and point constraints.

Use **Image → Simulate alignment** to jointly fit all linked observations and constraints in each connected image group using bounded least squares for translation, rotation, and uniform scale. Each group keeps one reference image fixed; image pairs without enough distinct matches retain their initial rotation and scale. After running alignment, choose **Step 2: Georeference** to solve per-image pixel-to-world transforms against all control-point geographic X/Y coordinates in the project CRS. You can also rerun that second stage from **Image → Georeference from GCPs**. Its report gives residuals per image/control point in CRS units; applying the result stores separate world transforms without changing canvas layout. Both Apply operations are undoable. The app currently estimates transforms only; it does not yet stitch, warp, or resample raster pixels or export a mosaic.

The **Control Points** dock is scoped to the active image context. It lists only points observed in that image, shows editable pixel and map coordinates, and lists constraints attached to those points. Selecting a constraint exposes editable endpoints and constraint type.

The **World Map** dock displays an OpenStreetMap basemap that can be panned with the middle mouse button and zoomed with the wheel. Tiles are fetched over the internet from `tile.openstreetmap.org` and cached locally; an internet connection is needed for tiles that are not already cached. The map includes the required © OpenStreetMap contributors attribution. Images with applied GCP world transforms appear at their solved project-CRS positions, north-up; images without applied world transforms are omitted. Use **View → Fit current view** (with the map active) to fit the georeferenced image footprints, or to return to the full-world view when no footprints are available. The overview does not stitch or rasterize image pixels.

Only one image, control point, or constraint is selected at a time. Selecting an item in another panel clears the previous selection.

Use the mouse wheel to zoom and drag the canvas with the middle mouse button to pan. The canvas has a large fixed navigation range, so you can pan far beyond the current images without reaching an edge; **Fit current view** recenters the relevant image layers. Point markers remain screen-sized while zooming; optional labels are shown in the point table, not on the canvas. Map coordinates may be left blank until known; when entered, both X and Y are required.

## Current scope

This release provides a shared transformable image workspace, tie-point observations, point constraints, and georeferencing metadata entry. Constraint links are recorded and visualized; they do not yet solve image alignment. The app does not yet calculate georeferencing transformations, resample imagery, or export a stitched/georeferenced raster. Image paths refer to original files on disk, so moving those files can break project references.

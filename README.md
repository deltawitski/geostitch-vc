<div align="center">

<img src="src/geostitch/assets/app_icon.png" alt="GeoStitch icon" width="96">

# GeoStitch

**Bring in raster images, stitch them together virtually, and georeference them against the real world.**

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Qt](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/status-alpha-orange)

[Overview](#-overview) · [Workflow](#-workflow) · [Tools](#-tools-and-shortcuts) · [Panels](#-workspace-panels) · [Export](#-exporting) · [Project files](#-project-files) · [Development](#-development)

</div>

---

## ✨ Overview

GeoStitch is a desktop workspace for turning a pile of unreferenced raster images (scanned maps, drone frames, survey sheets, aerial photos) into correctly placed, georeferenced layers.

Instead of stitching pixels, you work **virtually**:

1. Lay all images out on one shared canvas.
2. Mark features that appear in more than one image and **link** them.
3. Let the solver **align** the images to each other.
4. Give some points real-world coordinates and let the solver **georeference** every image.
5. **Export** each image as a GeoTIFF or a world-file image, ready for GIS software.

> [!NOTE]
> Images are never warped or resampled into a single mosaic. Each image is exported with its own georeferencing, so the result drops straight into QGIS, ArcGIS, or any tool that reads GeoTIFFs and world files.

### Highlights

|     | Feature                                                                                |
| --- | -------------------------------------------------------------------------------------- |
| 🖼️  | Shared, pannable and zoomable canvas for any number of images                          |
| 🧲  | Drag, rotate and scale layers with on-canvas handles                                   |
| 📍  | Continuous control-point placement with screen-sized markers                           |
| 🔗  | Tie-point links between matching features in different images                          |
| 📐  | Least-squares alignment and georeferencing with residual reports                       |
| 🗺️  | Live OpenStreetMap world map showing solved footprints, with click-to-pick coordinates |
| ✂️  | Per-image clip polygons (keep inside or outside) applied on export                     |
| 🧩  | GeoJSON vector overlays reprojected to the project CRS                                 |
| ↩️  | Full undo/redo for project edits                                                       |
| 🧱  | Dockable, floatable, splittable panels with remembered layout and custom shortcuts     |

---

The window appears before automatically reopening the last project. Saved-project images decode on background workers and appear progressively; the layer tree is available while they load. SciPy's optimizer and image-export dependencies load only when those commands are used. GeoJSON loading already runs on background workers.

Shared canvas and world-map images use previews capped at 2048 pixels on the longest edge. Their graphics bounds, point observations, clip polygons, arrangement, and geographic footprints remain in the original, EXIF-oriented image pixel coordinates. Display resampling never changes source files or export resolution.

Canvas refreshes reuse image items, point markers, link lines, selection outlines, and transform handles instead of clearing the whole scene. Geometry, visibility, locking, layer order, and residual styles update in place; removed items are cleaned up individually. Background refreshes preserve ongoing point and image drags when their objects remain in the project. Point lookup for links is indexed, and marker styling is batched once per layer refresh.

Image View and Link Review show a Preview, Native detail, or Native resolution indicator. Visible views request native detail on a background worker when their zoom exceeds the preview's pixel density. Native pixmaps share a 256 MiB retention budget, with one native decoder running at a time. Images that do not fit as whole native pixmaps load visible source regions as 512-pixel tiles instead. Tile retention uses up to 64 MiB of the shared budget, prioritizes regions near the viewport center, and reuses overlapping tiles while panning. If the visible regions exceed the remaining budget, surrounding areas stay preview-backed until their tiles are prioritized.

Zooming out, hiding the view, or changing selection releases unneeded native detail. Tiles preserve EXIF orientation, alpha, original pixel coordinates, clipping, zoom, and draggable markers. Preview memory still scales with project image count. Region decoding uses Qt clip rectangles with a Pillow fallback, but some codecs require full-size temporary buffers while decoding; the retention budget is not a cap on total process memory.

Opening an associated `.gsp` file in Windows opens that project instead of the last-used project. The Windows installer registers the association. You can also open a project with `python -m geostitch "path/to/project.gsp"`. If the requested file cannot be opened, the app reports the error without reopening a different project.

Open **File > Settings > Appearance** (also available under **Settings**) to choose an installed Qt style, the GeoStitch dark scheme or Qt's default palette, the **Dark map** option for OpenStreetMap tiles, and symbol colors for images, control points, links, and GeoJSON layers. Dark map is independent of the application's color scheme. Color swatches open a color picker. Apply updates the views immediately; OK applies and closes; Cancel discards changes not yet applied. Restore Defaults resets the dialog to Fusion, the dark application scheme, light map tiles, and the original symbol colors. Appearance preferences are saved independently of projects.

## 🔄 Workflow

```mermaid
flowchart LR
    A[📥 Import images] --> B[🧲 Arrange & position]
    B --> C[📍 Add control points]
    C --> D[🔗 Link matching points]
    D --> E[📐 Align]
    E --> F[🌍 Enter map coordinates]
    F --> G[🧮 Georeference]
    G --> H[📤 Export GeoTIFFs]
```

### 1. 📥 Import images

Use **File → Import images** (`Ctrl+I`) or drag files into the window. New layers are laid out in a balanced, near-square grid. Choose the project **coordinate system** (for example `EPSG:32610`) in the details panel or the CRS selector at the bottom-right of the status bar. The selector remains available when the World Map panel is hidden.

### 2. 🧲 Arrange and position

- Drag a layer to move it.
- Select a layer to show handles: drag a **corner** to scale proportionally, or the **handle above the image** to rotate.
- **Arrange images** (`Ctrl+Shift+A`) rebuilds the grid; **Fit current view** (`Ctrl+Shift+F`) zooms to the selection or everything.
- The Layers panel shows a padlock beside each image and control point. Click it to lock or unlock that row without changing selection: images lock their rest transform, while points lock their pixel position. Open padlocks are unlocked; closed padlocks are locked. GeoJSON rows have no lock control.

### 3. 📍 Add control points

Toggle **Add points** (`P`) and click features. Every click creates one point belonging to the clicked image, displayed as a compact per-image sequence (`P1`, `P2`, ...). Different images can display their own `P1`; unique stored names and IDs still distinguish them. The tool stays active until you toggle it off. Give points optional labels, edit pixel positions, or **lock** them (`K`) to prevent accidental drags. Labels appear beside the short name, such as `P3 · Bridge`; hover for full image context and the unique stored name. Existing custom point names are preserved, and explicitly editing a point name replaces its automatic alias with your custom name. Select images and press `K` to lock their canvas rest position, rotation, and scale, including against Arrange. Image locks are saved with the project and do not lock point observations or solver-aligned and geographic transforms. For a mixed selection, `K` locks all selected image transforms and point positions unless all are already locked, in which case it unlocks them.

### 4. 🔗 Link matching points

Toggle **Link points** (`L`), click a marker, then click its match in a **different** image. The tool stays active for successive pairs. You can also select a point and choose **Link selected point to...** (`Ctrl+Shift+L`). Links appear as dashed lines in the scene.
In the Layers panel, expand a linked control point to see its constraints. Links receive stable names (`L1`, `L2`, ...), saved with the project and not reused after deletion. Rows show the link and other point, such as `L1 → P2`; non-default constraint types are shown, while the default `tie` is omitted. Hover for both images and endpoints, labels, type, and available alignment residual. Point pickers include image context to distinguish local aliases. Select a link row to highlight the constraint and show its details; Delete removes the link without removing either point.

### 5. 📐 Align

**Image > Align** (`Ctrl+Shift+M`) runs immediately with the current project settings. It jointly fits all linked observations for each connected image group, solving translation, rotation and uniform scale with bounded least squares. One reference image per group stays fixed. Results apply automatically when the target tolerance is reached and can be undone. Use **Toggle aligned / rest layout** (`A`) to flip between the arranged and aligned canvas.

The solver toolbar group contains three controls: **Align**, **Georeference**, and **Align + Georeference**. Align and Georeference each have a small settings gear in their bottom-right corner; clicking the main icon runs the solver, while clicking the gear opens its settings. Settings dialogs offer **Apply**, **Run**, and **Cancel** for tolerance, maximum iterations, and automatic application. Apply saves settings without running; Run saves and executes. Uncheck automatic application to compute a preview without changing transforms. Settings are saved per project. Detailed results are available through the Image menu's last-report commands; alignment reports are saved with projects, while georeferencing reports describe the latest run in the current session.

**Image > Solver diagnostics...** checks the current control network without running an optimizer or changing the project. Filter by alignment or georeferencing, inspect a finding's recommendation, and choose **Select affected items** to select its images, points, and links in the Layers tree and fit them on the canvas. Missing source files are warnings because solvers can still use stored observations; pixel review and export require repaired file references.

Manual solver runs stop before computation for blocking issues such as absent usable links or geographic controls, invalid coordinates, invalid group frames, or an invalid geographic CRS. Combined runs check both stages before starting, so missing geographic controls do not leave alignment applied. Warnings do not add confirmation dialogs or prevent existing translation-only solves; the final status reports how many readiness warnings were found.

Diagnostics flag disconnected alignment groups, unlinked images, pairs with only one match, repeated endpoint pixel locations, minimal local redundancy, geographic groups without controls, single-location geographic control, duplicated geographic targets, and incomplete geographic coordinates. Geographic-control clustering is flagged when known source dimensions show a spread below 10% of the image diagonal. Connected geographic groups use the same frame geometry as the solver, rather than counting controls separately per image. The diagnostics dialog describes the current layout; combined runs refresh geographic checks on their newly aligned candidate before georeferencing.

These checks describe available geometry, not measured positional accuracy. Two distinct control locations can determine a similarity transform; collinearity alone is not treated as a blocking error for this transform model. A low solve residual or an empty diagnostics list is not a certificate of geographic accuracy.

**Align + Georeference** computes alignment first and uses that fresh layout for georeferencing. When both stages reach their targets and automatic application is enabled for both, the transforms apply together as one undo step. Preview mode in either solver leaves both sets of transforms unchanged. A missed target stops for review with **Apply best result**, **Adjust settings**, or **Discard**; the combined run does not continue past an alignment target miss. Cancellation or solver failure retains existing transforms.

The Layers **Error** column shows alignment residuals in **scene units**: RMS of incident links for each image, the largest incident-link residual for each point, and the solved endpoint distance for each link. Hover over a value for its full precision and meaning. Selected-link details also show the numerical residual and its current/outdated state. The canvas **Alignment errors** toggle colors links green within the solve's target tolerance, amber within twice that tolerance, and red above it. Selected links keep a thicker solid stroke and highlight both endpoints.

Changing observations, links, or rest transforms marks the previous solve **Outdated**, grays its Layers values, and turns off residual coloring. Undoing those changes restores current results. Coloring is available in both rest and aligned layouts, including current simulations that have not been applied in rest mode. In rest mode, colors show the last solve's residuals, not the gaps between the displayed endpoints. The aligned layout must match the solve to show its colors. Saving a project also saves the last examined residual report, target tolerance, and original solve inputs. Reopening restores the same values and current/outdated state without rerunning the solver, including unapplied simulations. Older projects without a saved report show unavailable errors until alignment is run and the project is saved again. These values measure alignment fit, not independent geographic accuracy; point values are link errors, not geographic target errors.

### 6. 🌍 Enter map coordinates

Give some points real coordinates in the project CRS: type them in the details panel, or toggle **Pick GCP coordinate on map** (`G`) and click the world map.

### 7. 🧮 Georeference

**Image > Georeference** (`Ctrl+Shift+G`) runs with the saved georeferencing settings and solves pixel-to-world transforms from points with map coordinates. Successful results apply automatically by default. The last-report command lists residuals per image and point in CRS units. Applied transforms are stored separately from the canvas layout, and images with a solved transform appear on the world map.

### 8. 📤 Export

**File → Export georeferenced images** (`Ctrl+E`). See [Exporting](#-exporting).

> [!TIP]
> Alignment and georeferencing are both undoable, so experiment freely.

---

## 🛠️ Tools and shortcuts

All shortcuts can be changed in **Settings → Keyboard shortcuts...** (duplicates are rejected; _Restore defaults_ resets them).

| Tool / command                  |              Key               | Description                                                 |
| ------------------------------- | :----------------------------: | ----------------------------------------------------------- |
| Add points                      |              `P`               | Click images to create control points continuously          |
| Link points                     |              `L`               | Click two matching points in different images               |
| Pick GCP coordinate on map      |              `G`               | Click the world map to set the selected point's coordinates |
| Lasso selection                 |              `S`               | Toggle freehand lasso instead of rectangle selection        |
| Lock / unlock images and points |              `K`               | Lock image rest transforms and selected point positions     |
| Toggle aligned / rest layout    |              `A`               | Switch between arranged and solver-aligned layouts          |
| Maximize / restore panel        |              `M`               | Focus on one panel                                          |
| Delete selected                 |             `Del`              | Remove selected images, points or link                      |
| Select all / Deselect all       |      `Ctrl+A` / `Ctrl+D`       |                                                             |
| Undo / Redo                     |      `Ctrl+Z` / `Ctrl+Y`       |                                                             |
| New / Open / Save               | `Ctrl+N` / `Ctrl+O` / `Ctrl+S` | `Ctrl+Shift+S` for Save as                                  |
| Import images                   |            `Ctrl+I`            |                                                             |
| Export georeferenced images     |            `Ctrl+E`            |                                                             |
| Fit current view                |         `Ctrl+Shift+F`         | Fits canvas, map or image view, whichever you are in        |
| Arrange images                  |         `Ctrl+Shift+A`         |                                                             |
| Align                           |         `Ctrl+Shift+M`         | Runs with the current project settings                      |
| Georeference from GCPs          |         `Ctrl+Shift+G`         |                                                             |
| Link selected point to...       |         `Ctrl+Shift+L`         |                                                             |

**Mouse:** wheel to zoom, middle-drag to pan. In select mode, `Ctrl` adds to the selection and `Shift` drags over images.

World Map wheel zoom is animated around the cursor and supports fractional wheel input. Tile requests settle after zoom motion instead of restarting on every frame. Existing tiles remain visible while replacements fade in, including when zooming out; a bounded decoded-tile cache makes returning to recently viewed areas quicker. Fit, pan, and restored views stop pending zoom motion.

The checkable control-point button over the World Map's top-right corner shows or hides point markers. Its highlighted state indicates that points are shown; hover for the marker legend.

The Image Canvas has three checkable buttons in its top-right corner: **Aligned layout** switches between rest and solver-aligned transforms (also available with `A`), **Show clipping** previews clip polygons, and **Alignment errors** colors links using the latest solve residuals. Highlighted means the option is enabled. The layout button is disabled until aligned transforms are available; the error button is disabled when the residual report is unavailable or outdated. Numerical errors are available in the Layers **Error** column, its tooltips, and selected-link details.

Image View uses the same top-right overlay style for **Draw clip polygon**, **Clear polygon**, the **inside/outside clip-mode menu**, and **Apply clip**. Hover for each control's name; draw and apply controls highlight when enabled. The Fit button is omitted because **Fit current view** (`Ctrl+Shift+F`) already fits the image when Image View is active.

---

## 🪟 Workspace panels

| Panel              | Purpose                                                                                                            |
| ------------------ | ------------------------------------------------------------------------------------------------------------------ |
| **Images**         | Layer list; select, reorder and remove images                                                                      |
| **Image Canvas**   | Shared workspace for positioning images and placing points and links                                               |
| **Image View**     | Single-image view for precise point placement                                                                      |
| **Control Points** | Points and links for the active image, with editable pixel and map coordinates, names, labels and constraint types |
| **World Map**      | OpenStreetMap basemap with solved image footprints, GeoJSON overlays, and coordinate picking                       |
| **Link Review**    | Searchable, sortable link list and side-by-side endpoint images for correcting observations                        |

Panels can be docked, floated, closed, or split by dragging a title bar to another panel's edge. The layout is restored on next launch; use **View → Reset panel layout** to return to the default.

### Layers filtering

The Layers panel has a case-insensitive search box, an image selector, and point-status filters for **Missing coordinates**, **With coordinates**, **Unlinked points**, and **Linked points**. Search matches image and vector names or paths, unique and displayed point names, labels, and link names or types. Link matches retain endpoint and parent-image context. Choose an image to restrict the result to that image, and combine it with search or a point-status filter. A point missing either geographic coordinate counts as Missing coordinates; Unlinked means it has no incident point constraint.

Filtering affects only tree rows, not canvas/map visibility or project data. The summary reports matching layers and points. Existing selections remain selected even outside the results, with a **selected outside filter** count; editing, deleting, or locking still applies to that selection, not all matching rows. Use the normal Deselect all command to clear it. Layer drag-reordering is disabled while any filter is active.

Matching branches expand to reveal results. The reset icon clears search, image scope, and point status together and restores the expansion state from before filtering. Filters stay active through tree refreshes and undo/redo; coordinate, label, and link edits update their results. Removing a scoped image returns the image selector to All images. Filters are workspace UI state only and are not saved in project files or restored on application startup.

### Linked-point review

Open **View > Link Review** to review linked observations side by side. The panel is hidden by default. Selecting a link in its table selects the same link in the Layers tree and canvas; selecting a link elsewhere updates the review panel. Each image centers on its endpoint, with independent zoom and pan. Drag an unlocked marker to correct its pixel observation; the correction is one undoable edit, and the link remains selected. Locked observations cannot be dragged. The center icon or **Fit current view** recenters both endpoints without changing their zoom.

Search by link name or type, image name, point name, or point label. Click column headings to sort; residuals sort numerically, with largest errors first by default. **Above threshold** filters links above the chosen value in scene units; **No residual** finds links absent from the last alignment report. Previous and next arrows follow the current filtered and sorted list. A selected link remains visible in the endpoint views even if a filter excludes its table row.

Residuals are from the last examined alignment result, not newly computed distances. Edits mark those values **Outdated** and gray them until a new alignment is examined or undo restores its original inputs. Reviewing links and changing filters do not modify the project. Source images load through the existing background loader, and the review controls are disabled while a solver runs.

> [!NOTE]
> Map tiles are fetched from `tile.openstreetmap.org` and cached locally, so an internet connection is needed for uncached areas. © OpenStreetMap contributors.

### Extras

- **Clip polygons** - draw a polygon on an image and keep the inside or outside; applied on export, with a cutline GeoJSON written alongside.
- **GeoJSON layers** - **File → Import GeoJSON layers** overlays vector data, reprojected to the project CRS.

---

## 📤 Exporting

Every image with a solved world transform is written to the folder you choose as `<name>_georef.<ext>`.

| Format            | Output                              |
| ----------------- | ----------------------------------- |
| GeoTIFF           | `.tif` with embedded georeferencing |
| PNG + world file  | `.png` + `.pgw`                     |
| JPEG + world file | `.jpg` + `.jgw`                     |

Clipped images additionally produce a `_cutline.geojson` (and a `_mask.png` for formats without alpha).

Export runs on a background worker using a snapshot of the project at the start of the batch. You can continue navigating or editing the project without changing the export in progress. The format and destination folder are remembered for the next export.

The nonmodal progress dialog and **File > Cancel export** can request cancellation. The current image finishes first; subsequent images are skipped, and completed outputs remain available. Closing the application during export offers cancellation and keeps the window open until the current image finishes; close it again afterward.

Existing output images and metadata sidecars require explicit overwrite confirmation. Expand the confirmation dialog's details to inspect the conflicting paths. Each image and its sidecars are encoded in a temporary staging folder before publication. Failed publication attempts restore previous outputs where possible; if restoration fails, the error lists retained backup files in the output folder. Any incomplete outputs that could not be removed are also listed. Source images cannot be overwritten by export. Output publication also supports drives without hard-link support.

After output has been written, **File > Open export folder** opens its destination. The completion message reports how many images were exported, and failures are listed separately. Cancellation is not an interruption of a codec operation, so very large images may take time to finish before the export stops.

---

## 💾 Project files

Projects are saved as versioned JSON `.gsp` files containing image paths and transforms, points and labels, observations, map coordinates, the CRS, constraints, and view state. Version 14 added the last examined alignment residual report; version 15 added settings for both solvers; version 16 added compact point aliases and stable link names. Version 17 stores one authoritative alignment report, deriving point and image summaries from its original solve inputs instead of saving redundant summary maps. Older project and report versions remain readable, retain their point names, receive link numbers on load, and receive default solver settings where needed.

Changing the project CRS refits world transforms using the saved georeferencing tolerance and iteration limit, with the same cancellable progress as the normal solver. Cancelling or failing the refit restores the previous CRS, coordinates, transforms, and report. A completed CRS change remains a single undo step; automatic-application settings for manual previews do not suppress the refit needed for the new coordinate system.

### Saving and recovery

- **File > Recent projects** lists the ten most recently opened or saved projects, with folder context and full-path tooltips to distinguish matching filenames. Successful opens and saves move entries to the top; failed opens or saves and cancelled project switches leave the history unchanged. Missing files remain marked Missing. The menu refreshes when opened, and older settings seed the list from the previous last-used project.
- **Clear recent projects** clears only the navigation history, not files, the current project, or the last-project startup setting. **File > Open project folder** opens the saved project's containing folder; it is unavailable for untitled projects. Recent-project selection uses the same Save, Discard, and Cancel protection as normal opening.
- An asterisk in the window title marks unsaved document changes. Close, New, and Open offer Save, Discard, or Cancel. Cancelling the save dialog or failing to save keeps the current project open. Panning, zooming, and switching between rest and aligned views do not mark the document modified.
- Saves use a temporary file and atomic replacement. The previous successful save is retained as `<project>.gsp.bak`; select Project backup in the Open dialog to open it, then use Save As to save a `.gsp` project.
- Modified projects get a separate recovery snapshot every minute, unless a solver or grouped edit is running. These snapshots do not overwrite the saved project and are removed after a successful save or an intentional discard. Changes since the most recent snapshot may still be lost in a crash.
- After an interrupted session, opening the same project offers its newer recovery snapshot. **File > Recover project...** also lists available snapshots, including untitled projects and copies whose source files are missing or corrupt. Recovered work stays marked unsaved until explicitly saved.
- Recovery snapshots live in the application's local data directory under `recovery`. They contain project data and file references, not copies of the source images.

> [!WARNING]
> Projects reference the **original image files on disk**. Moving or renaming them breaks the references.

Use **File > Repair missing images...** to repair broken references. **Search folder...** searches the chosen folder and its subfolders on a background worker, matching original filenames without regard to case. Unique matches appear as proposed replacements; duplicate filenames require an explicit choice. Each row also has a **Locate** folder button for moved or renamed files. Review the paths and select **Apply** to repair all chosen rows, or **Cancel** to leave the project unchanged. Unresolved images can be repaired later.

Repairs keep image IDs, names, transforms, masks, and control-point observations intact, and are recorded as one undoable project edit. Repaired images reload in the background. Choose the same original image, not a resized or different image: existing observations remain in the original pixel coordinate system. Save the project to persist the new paths.

Older files with multi-image points are split into linked per-image points on load.

---

## 📦 Install and run

Requires **Python 3.10+**.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m geostitch
```

---

## 👩‍💻 Development

### Layout

```text
src/geostitch/
├── app.py            # main window, tools, panels, dialogs
├── canvas.py         # shared image canvas
├── image_view.py     # single-image view
├── map_view.py       # world map and tiles
├── models.py         # Qt-independent project data
├── project_io.py     # .gsp serialization
├── adjustment.py     # alignment solver
├── georeference.py   # GCP georeferencing solver
├── export.py         # GeoTIFF / world-file export
├── masking.py        # clip polygons
├── vector*.py        # GeoJSON loading and reprojection
└── assets/           # icons and images
tests/                # pytest suite
packaging/            # PyInstaller specs, build script, installer
```

### Conventions

- Keep Qt UI code in `src/geostitch`; keep `models.py` free of Qt.
- A control point belongs to exactly one image; points in different images relate only through constraints.
- Keep `.gsp` serialization backward compatible and covered by tests.

### Tests

```powershell
python -m pytest
```

In VS Code, pick **GeoStitch: desktop app** under **Run and Debug** and press **F5** to launch under the debugger.

### Building executables

```powershell
.\packaging\build.ps1            # folder build + Inno Setup installer
.\packaging\build.ps1 -OneFile   # single self-contained dist\GeoStitch.exe
```

The installer step requires [Inno Setup 6](https://jrsoftware.org/isinfo.php).

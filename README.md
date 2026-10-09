<div align="center">

# <img src="src/geostitch/assets/app_icon.png" alt="" width="48" align="center"> GeoStitch

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

## 🔄 Workflow

```mermaid
flowchart LR
    A[📥 Import images] --> B[🧲 Arrange & position]
    B --> C[📍 Add control points]
    C --> D[🔗 Link matching points]
    D --> E[📐 Simulate alignment]
    E --> F[🌍 Enter map coordinates]
    F --> G[🧮 Georeference]
    G --> H[📤 Export GeoTIFFs]
```

### 1. 📥 Import images

Use **File → Import images** (`Ctrl+I`) or drag files into the window. New layers are laid out in a balanced, near-square grid. Choose the project **coordinate system** (for example `EPSG:32610`) in the details panel or the map toolbar.

### 2. 🧲 Arrange and position

- Drag a layer to move it.
- Select a layer to show handles: drag a **corner** to scale proportionally, or the **handle above the image** to rotate.
- **Arrange images** (`Ctrl+Shift+A`) rebuilds the grid; **Fit current view** (`Ctrl+Shift+F`) zooms to the selection or everything.

### 3. 📍 Add control points

Toggle **Add points** (`P`) and click features. Every click creates one point belonging to the clicked image, auto-named per image (`survey-001`, `survey-002`, …). The tool stays active until you toggle it off. Give points optional labels, edit pixel positions, or **lock** them (`K`) to prevent accidental drags.

### 4. 🔗 Link matching points

Toggle **Link points** (`L`), click a marker, then click its match in a **different** image. The tool stays active for successive pairs. You can also select a point and choose **Link selected point to...** (`Ctrl+Shift+L`). Links appear as dashed lines in the scene.

### 5. 📐 Simulate alignment

**Image → Simulate alignment** (`Ctrl+Shift+M`) jointly fits all linked observations for each connected image group, solving translation, rotation and uniform scale with bounded least squares. One reference image per group stays fixed. Review the residual report, then apply it. Use **Toggle aligned / rest layout** (`A`) to flip between the arranged and aligned canvas.

### 6. 🌍 Enter map coordinates

Give some points real coordinates in the project CRS: type them in the details panel, or toggle **Pick GCP coordinate on map** (`G`) and click the world map.

### 7. 🧮 Georeference

**Image → Georeference from GCPs** (`Ctrl+Shift+G`) solves a pixel-to-world transform per image from every point that has map coordinates. The report lists residuals per image and point in CRS units. Applied transforms are stored separately from the canvas layout, and images with a solved transform appear on the world map.

### 8. 📤 Export

**File → Export georeferenced images** (`Ctrl+E`). See [Exporting](#-exporting).

> [!TIP]
> Alignment and georeferencing are both undoable, so experiment freely.

---

## 🛠️ Tools and shortcuts

All shortcuts can be changed in **Settings → Keyboard shortcuts...** (duplicates are rejected; _Restore defaults_ resets them).

| Tool / command               |              Key               | Description                                                 |
| ---------------------------- | :----------------------------: | ----------------------------------------------------------- |
| Add points                   |              `P`               | Click images to create control points continuously          |
| Link points                  |              `L`               | Click two matching points in different images               |
| Pick GCP coordinate on map   |              `G`               | Click the world map to set the selected point's coordinates |
| Lasso selection              |              `S`               | Toggle freehand lasso instead of rectangle selection        |
| Lock / unlock points         |              `K`               | Prevent dragging and pixel edits for selected points        |
| Toggle aligned / rest layout |              `A`               | Switch between arranged and solver-aligned layouts          |
| Maximize / restore panel     |              `M`               | Focus on one panel                                          |
| Delete selected              |             `Del`              | Remove selected images, points or link                      |
| Select all / Deselect all    |      `Ctrl+A` / `Ctrl+D`       |                                                             |
| Undo / Redo                  |      `Ctrl+Z` / `Ctrl+Y`       |                                                             |
| New / Open / Save            | `Ctrl+N` / `Ctrl+O` / `Ctrl+S` | `Ctrl+Shift+S` for Save as                                  |
| Import images                |            `Ctrl+I`            |                                                             |
| Export georeferenced images  |            `Ctrl+E`            |                                                             |
| Fit current view             |         `Ctrl+Shift+F`         | Fits canvas, map or image view, whichever you are in        |
| Arrange images               |         `Ctrl+Shift+A`         |                                                             |
| Simulate alignment           |         `Ctrl+Shift+M`         |                                                             |
| Georeference from GCPs       |         `Ctrl+Shift+G`         |                                                             |
| Link selected point to...    |         `Ctrl+Shift+L`         |                                                             |

**Mouse:** wheel to zoom, middle-drag to pan. In select mode, `Ctrl` adds to the selection and `Shift` drags over images.

---

## 🪟 Workspace panels

| Panel              | Purpose                                                                                                            |
| ------------------ | ------------------------------------------------------------------------------------------------------------------ |
| **Images**         | Layer list; select, reorder and remove images                                                                      |
| **Image Canvas**   | Shared workspace for positioning images and placing points and links                                               |
| **Image View**     | Single-image view for precise point placement                                                                      |
| **Control Points** | Points and links for the active image, with editable pixel and map coordinates, names, labels and constraint types |
| **World Map**      | OpenStreetMap basemap with solved image footprints, GeoJSON overlays, and coordinate picking                       |

Panels can be docked, floated, closed, or split by dragging a title bar to another panel's edge. The layout is restored on next launch; use **View → Reset panel layout** to return to the default.

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

---

## 💾 Project files

Projects are saved as versioned JSON `.gsp` files containing image paths and transforms, points and labels, observations, map coordinates, the CRS, constraints, and view state.

> [!WARNING]
> Projects reference the **original image files on disk**. Moving or renaming them breaks the references.

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

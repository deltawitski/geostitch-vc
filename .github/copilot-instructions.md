# Workspace Guidance

- This is a Python 3.10+ PySide6 desktop application. Keep Qt UI code in `src/geostitch` and domain data independent of Qt in `models.py`.
- A `GroundControlPoint` belongs to exactly one image (`image_id`); its single observation stores that image's pixel coordinates. Points in different images relate only through `PointConstraint` links. Geographic coordinates belong to each point and use the project's CRS. Loading older files splits legacy multi-image points into linked per-image points.
- Default point names are incremented per active image; optional human-readable labels are separate from unique point names.
- The continuous Add points tool creates one point per image click and remains active until toggled off. Keep canvas point overlays screen-sized across zoom levels.
- The Link points canvas tool creates constraints by pairing clicked point markers from different image layers and remains active for successive pairs.
- Images share one transformable canvas. Persist layer position, rotation, and scale; observations remain in each source image's pixel coordinates.
- Batch image imports and file drops should arrange layers in a near-square grid while preserving each image's transform-aware bounds.
- Point constraints connect two project control points. Remove a constraint if either endpoint is deleted. Constraints are recorded for future adjustment but do not yet solve transforms.
- Project files are versioned JSON with the `.gsp` extension. Keep serialization compatibility covered by tests.
- Install with `python -m pip install -e ".[test]"`; launch with `python -m geostitch`; test with `python -m pytest`.
- Do not describe the app as performing raster stitching or georeferencing transforms until those operations and their exports are implemented.

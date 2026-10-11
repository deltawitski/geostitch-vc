# Transform and Warp Support Roadmap

Status: core Phases 1-5, Phase 6 geographic-CRS fitting/refit infrastructure, and a usable geographic TPS georeferencing path are implemented. Phase 6 independent validation and Phase 7 deformable acceptance remain open; Phase 8 and broad release validation are not implemented. See Current Status for the authoritative summary.

## Current Status

Checkpoint entries below are chronological snapshots. Their “remaining” or “next” notes
describe the state at that checkpoint and may be superseded by later work; this section is
the current status source of truth.

| Area                            | Status                                                                                                                                                                                                                                                                                                                      | Remaining work                                                                                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mapping storage and canvas      | Core implemented through project format 26; matrix, CRS, and native TPS world mappings persist through project save/load and undo/redo. Geographic checkpoints persist separately from fit-control status.                                                                                                                  | Scalar poses remain compatibility state; TPS is georeferencing-only and is not a layout/canvas transform; broader project and packaged-app validation.                        |
| World Map raster and markers    | Adaptive raster rendering and observation-derived GCP markers consume native matrix-to-CRS chains, including antimeridian imagery.                                                                                                                                                                                          | Broader high-DPI, projection-edge, and independent sample validation.                                                                                                         |
| Raster export                   | Affine/Projective and TPS native/cross-CRS export uses explicit grids, mapping-chain inversion, alpha-aware sampling, and chain-aware cutlines.                                                                                                                                                                             | Broader CRS/driver edge cases, TPS inverse performance/accuracy, and packaged Windows checks. Warped JPEG remains unsupported.                                                |
| Georeference result application | Results can carry native mapping records; apply/refit, project save/load, and undo/redo preserve the full chain. Version-26 checkpoint points are excluded from fits and report independent mapped/unmapped errors for Affine, Projective, and TPS.                                                                         | Compatibility-pose refits remain approximate; broaden checkpoint coverage across raster pixels, masks, CRS combinations, and failure cases.                                   |
| Geographic-CRS fitting          | Affine/Projective fit in a datum-preserving local AEQD frame per connected group; residuals/robust thresholds use metres and results preserve the CRS chain. Control and mapped-domain distortion use the project datum ellipsoid. A withheld geographic checkpoint is tested through fit, save/load, and cross-CRS export. | Validate sampled warning thresholds/coverage, preserve or disclose unavailable domain diagnostics, and expand operation/grid/checkpoint tests across more CRSs and workflows. |
| Deformable and joint models     | Geographic TPS georeferencing is selectable in the application and flows through version-25 native mapping persistence, World Map rendering, CRS refits, and inverse-warp export. Version-26 checkpoints are excluded from fit and reported independently.                                                                  | Validate boundary/inverse performance, large networks, and distortion limits. TPS alignment and Phase 8 joint adjustment remain unimplemented.                                |
| Release verification            | Focused regressions and selected Rasterio readbacks exist.                                                                                                                                                                                                                                                                  | Complete broader independent GIS, performance, high-DPI, and packaged-app gates.                                                                                              |

### Next Steps

1. Characterize local-frame distortion over mapped image domains as well as GCP pairs. A bounded post-fit sample now reports large or unavailable domains as warnings; validate coverage and thresholds with independent checkpoints before considering any blocking policy.
2. Georeference distance settings now convert across exact same-dimension unit changes; angular/linear transitions require confirmation and keep the numeric values. Broaden CRS/unit coverage and continue reporting effective fit units.
3. Control and mapped-domain distortion now use the project datum ellipsoid, and operation accuracy/area/grid metadata is surfaced. Validate selected operation and datum behavior across more geographic CRSs, high latitudes, and antimeridian-spanning controls.
4. Persisted independent checkpoints now report error/coverage and remain out of fit residuals/topology. TPS, Affine, and Projective fit exclusion is tested; extend checkpoint validation across CRS refit/undo for all base models, map markers/raster, masks, and cross-CRS export, then complete GIS, performance, high-DPI, and packaged-Windows release gates.
5. TPS now runs as a georeferencing-only geographic model through fit, apply, persistence, World Map, and warped export. Expand independent checkpoints, masks, high-DPI rendering, domain/inverse performance, and CRS-operation coverage before considering TPS alignment or Phase 8 joint adjustment.

## Implementation Progress

The sections below preserve implementation history; later checkpoints supersede earlier
status notes where their claims differ.

The first Phase 1 increment is implemented and tested:

- Immutable MatrixTransform supports matrix evaluation, inverse, composition, Jacobians, affine extraction, and mapping-level serialization.
- Projective point/domain horizon checks reject invalid mappings; general mappings cannot silently become similarity or affine poses.
- CRSMapping and MappingChain retain separate CRS operations, use explicit X/Y order, support inverse operations, and reject invalid coordinate results. Nonlinear chains cannot silently collapse to matrices.
- Existing ImageRecord scalar fields remain the compatibility-pose owner. Native world mappings now persist separately for exact map reprojection; rest/aligned mapping adapters and solver-result adapters retain complete matrix geometry.
- Existing similarity solves, fitted map points, map footprint coordinates, and affine export share matrix math. Geographic images now use the adaptive raster renderer described in the fourth checkpoint; three-corner input remains only as a compatibility path for old map callers/tests.
- At the first checkpoint, mapping-level serialization was tested while projects still used version 17. The second checkpoint below introduces project-format migration.
- A visible combined-solve smoke check confirmed image display and expected source-to-world coordinates. It also identified a stale Details-panel geographic-image count; apply now refreshes it, with apply/undo/redo coverage.

Validation: 413 numerical/integration/workflow tests passed before the final UI-count
fix, followed by 74 targeted mapping/solve/refit/map checks after that fix. New checks
cover legacy save/load geometry, unavailable mapping states, snapshot immutability,
Qt forward/inverse matrix agreement, canvas marker positions/item reuse, CRS mapping
across image interiors, and identical fitted map-point/export coefficients.

### Second Checkpoint: Versioned Mapping Storage

- Project format 18 stores separate rest/aligned/world mapping records with explicit source-pixel and layout/native-CRS frames. Versions 1-17 remain readable and migrate without changing source-to-layout/world geometry.
- Exact compatibility pose parameters remain alongside validated matrices during the runtime transition, preserving unset state and rotations beyond one turn. Conflicting pose/matrix data, frame mismatches, CRS mismatches, and invalid geometry are rejected.
- ImageMapping records can represent similarity, affine, and projective matrices and validate source-domain horizons when dimensions are known. The application deliberately rejects general image-mapping files until canvas editing and rendering can consume them completely.
- Alignment report schema 3 preserves full solver matrices, frame metadata, and known source domains. Legacy report versions 1-2 remain readable. General affine/projective results can be archived without approximation, but application is blocked before any project or undo mutation.
- Original, EXIF-oriented source dimensions are committed only after a successful explicit save, using a candidate snapshot. Background loading stays read-only; failed saves leave the model's source metadata unchanged. Persisted dimensions supply source bounds before preview loading completes.
- Undo/redo preserves dimensions learned during save for the same image ID/path, so redo to saved solver geometry returns to a clean document state. Source repair clears geometry metadata tied to the replaced file; snapshots restore the correct metadata without changing point observations or poses.
- ImageRecord runtime pose fields and legacy solver tuples are still compatibility owners. Fully authoritative runtime matrix storage, native-CRS chains in image records, mapping-aware stale signatures, and source-repair geometry confirmation are not yet complete.

Final focused validation: 255 tests passed across mapping storage, models, project files,
solver methods/workflows/refits, feedback, previews, startup, save/recovery protection,
recent projects, and source repair. A combined-solve/save/reopen smoke check
confirmed version-18 storage, schema-3 reports, restored canvas/map layers, matching
coordinates, source bounds before decoding, current feedback, and a clean document
after background loading. A larger aggregate run stopped making progress near completion
and was terminated; it is not counted as a passed full regression suite. The final focused
run above completed successfully with stack reporting enabled for stalled tests.

### Third Checkpoint: Matrix-Aware Layout Canvas

- Project format 19 stores rest/aligned intrinsic matrices plus editable outer similarity poses. The complete layout mapping is their composition; versions 1-18 remain readable. Geographic mappings remain similarity-only until accurate CRS rendering and export integration are ready.
- Qt matrix adapters preserve affine/projective coefficient ordering. Canvas layers accept complete mappings, validate image domains and observation positions before scene changes, and reset scalar Qt transforms to prevent double application.
- Complete mapping readback/signals preserve shear/perspective through moves, group drags, refresh, and center-based rotation/scale handles. Legacy similarity signals remain compatible; general mappings never emit an invented similarity pose.
- Source-pixel point dragging uses a validated inverse. Invalid projective coordinates leave the observation unchanged and provide a tooltip. Markers remain screen-sized; source review stays unwarped.
- Intrinsic matrices participate in app refresh, transformed arrangement bounds, saved snapshots, and undo/redo. Details distinguish intrinsic models from their outer poses. General aligned results can be applied and displayed read-only; rest/aligned toggles retain separate geometry.
- Alignment report schema 4 includes intrinsic coefficients in its stale-input signature. Legacy report schemas 1-3 migrate to the new signature. Matrix coefficient changes mark feedback outdated independently of outer-pose edits.
- Similarity-only solvers refuse linked intrinsic layouts rather than flattening them. A combined rerun may replace an old general aligned result from a valid similarity rest layout and clears the old intrinsic before geographic fitting. Affine/projective solver choices remain unavailable.
- Projective source replacement validates original EXIF-oriented dimensions before any path changes. Matching geometry preserves its domain; changed or undecodable geometry is refused with an actionable error.
- Synchronous and background decode refuse source files whose dimensions no longer match a persisted intrinsic layout, rather than drawing changed pixels beneath stale geometry. This check does not rewrite project data or affect ordinary similarity loading.

Focused checks include real Qt mouse drags for affine/projective images and projective
points, one-step undo transactions, apply/toggle/save/load/undo/redo, locks/read-only,
live group-drag refresh, absolute similarity edits, transformed arrangement, horizon
refusal, legacy report migration, and source repair. A visible projective checkerboard
smoke test confirmed a nonblank image, transformed outline, five handles, two markers,
and their link; source-to-scene coordinates matched direct mapping evaluation.

Final focused validation after the decode-domain and result-domain guards: 250 tests passed across canvas
matrices, layout storage, feedback, readiness, solve workflows, repair, project loading,
and save/undo safety. A separate 57-test check included startup and preview loading.
An additional 105-test run passed for exports/workers, source-image review, Link Review,
recent projects, and solver dialogs.
Another 76-test run passed for map placement/navigation/controls, CRS refits, and existing
mapping integration. Results with conflicting source domains are refused before mutation.
The broader aggregate checkpoint run was started before the final guards, stopped making
progress near completion, and was terminated. It is not counted as a passed full suite.
The completed focused and separate regression runs above are the validation evidence.

### Fourth Checkpoint: Adaptive CRS Map Rendering

- App image payloads now provide a source-pixel -> image-native/project CRS -> EPSG:3857 -> map-scene mapping chain. Y-axis conversion is explicit and used consistently for source-raster sampling and inferred point coordinates.
- World-map rasters use adaptive triangle tessellation in source pixels. Subdivision tests edge midpoints and cell centers in device space against a 0.25 px target; triangle and visited-cell budgets bound work. Smooth texture sampling remains enabled while triangle edge antialiasing is disabled to prevent basemap-colored mesh seams.
- EPSG:3857 legacy corner payloads and direct `WorldMapView.set_world_images` callers remain supported. App-produced image payloads use the mapping chain and the new renderer.
- Invalid/out-of-world Web Mercator samples are clipped and triangles spanning discontinuities subdivide then drop rather than draw long false edges. A visible warning identifies images whose mesh reaches a work limit; it says their displayed placement may exceed the target.
- The renderer is verified against direct pyproj evaluations across image interiors in EPSG:4326, EPSG:26910, and EPSG:3857. High-latitude EPSG:4326 visual smoke: 32 triangles, 0.154 px maximum sampled leaf error under the 0.25 px target, no budget exhaustion, and texture pixel colors aligned at mapped source positions.
- `WarpedMapImageItem.map_source_to_scene` exposes nonlinear point mapping. A single Qt `mapToScene` affine is intentionally not asserted for a nonlinear chain.

This is an adaptive evaluated-sample target, not a formal bound over every point, and it
does not establish CRS operation/datum accuracy. Current bounds use sampled image-domain
coordinates; points near a projection discontinuity are clipped and surfaced as a map
warning. Antimeridian-crossing imagery now renders in adjacent wrapped copies and fits to
a compact seam interval. Map-image tooltips expose the selected pyproj operation, its
reported or unknown accuracy, area of use, and grid availability. Geographic-CRS matrix
fitting and general warped raster export remain unavailable.

Focused validation: 211 tests passed across warped-map accuracy/rendering, map view,
navigation, previews, canvas interactions, exports, CRS refits, project persistence, and
source-pixel/map integration. A visible high-latitude raster smoke test produced 32 mesh
triangles with a maximum sampled leaf error of 0.154 pixels, below the 0.25-pixel target,
and source-color probes landed on the expected checkerboard pixels. Pylance reports no
errors in the new renderer or touched app/map paths. Projection-edge clipping, native
control/world mapping preservation, CRS-operation metadata, and antimeridian wrapping
have since been added; geographic-CRS matrix solving and warped export remain.

General layout matrices, Affine/Projective alignment, projected-CRS Affine georeferencing,
and same-CRS native Affine export are supported. Geographic-CRS Affine fitting, Projective
georeferencing, deformable solving, and cross-CRS/non-affine export remain gated. Native
world mappings are preserved for map display, while similarity CRS refits remain a
compatibility path for solver and legacy export behavior.

### Fifth Checkpoint: Native Control-Coordinate Provenance

- Project format 20 adds native X/Y/CRS provenance to complete geographic control points. Versions 1-19 initialize that source from the coordinates and project CRS they contain; incomplete X-only/Y-only coordinates remain valid legacy data without a native pair.
- User-entered and map-picked coordinates establish new native coordinates in the active project CRS. CRS changes compute every point's working coordinates directly from its stored native pair rather than from the prior projected pair.
- Repeated switches and save/load are tested against direct pyproj source-to-target transforms. CRS changes retain the existing transaction, undo, cancellation, and world-transform-refit workflow. Undo restores both working and native coordinates.
- This prevents repeated control-coordinate round-trip drift. It does not yet make native world-image matrices authoritative: image world transforms are still reprojected approximately and refit for the active project CRS. World exports still use the active-CRS affine fit.

### Sixth Checkpoint: Projection-Edge Clipping

- The adaptive map item rejects samples outside the standard Web Mercator world extent. Cells whose mapped bounds span a projection jump, including antimeridian wrap, subdivide and are dropped instead of drawing a long bridge across the map.
- Invalid-source samples and dropped discontinuity cells now set a distinct clipped-domain state. The map emits an accuracy warning for either clipping or exhausted tessellation budgets.
- Antimeridian-crossing rasters now have translated mesh copies in adjacent worlds. Seam-aware fitting uses the compact interval around +180 degrees; OSM tile X indices wrap modulo the zoom tile count while scene placement remains unwrapped. No output triangle spans the seam, and the existing clipping warning remains visible for dropped seam cells.

Validation for these additions: 95 tests passed across native CRS provenance,
serialization, solver refit rollback, map projection clipping, and map display. Native
geographic image transforms, selected-operation/grid-accuracy reports, geographic
affine solving, and reprojection-aware export remain pending.

### Seventh Checkpoint: Affine Alignment

- Alignment settings now expose Affine with independent X/Y scales and shear. The solver uses source-coordinate normalization, fixes one reference mapping per connected image group, jointly minimizes linked-point residuals, and retains the exact matrix result plus compatibility tuples only where a result is genuinely a similarity.
- Affine readiness requires at least three non-collinear correspondences on both images of every pair. Collinear/insufficient pairs block before optimization. Fitted variable matrices are checked against configured minimum/maximum principal stretch, maximum condition number, and an explicit reflection policy.
- Full-rank affine controls with normalized design condition above 10,000 now produce a readiness warning; the solve remains available so users can add spread or inspect residuals.
- Advanced affine controls apply to alignment and projected-CRS georeferencing. Georeferencing blocks linked affine/projective canvas layouts rather than flattening their group frames; direct affine georeferencing and same-CRS native affine export are now supported.
- Affine settings persist principal-stretch limits, maximum condition number, and reflection policy. Invalid solved matrices are rejected before a result is applied.
- Affine results apply to the matrix-aware canvas, survive aligned/rest, save/load, feedback signatures, and undo/redo. A combined affine alignment + georeference will stop at the geographic matrix gate rather than discard the affine geometry.

Validation: six numerical affine recovery/validity tests and a real MainWindow solve test
verify shear, independent scales, fixed anchors, matrix canvas placement, version-21
save/reopen, and undo/redo. A combined-run test with geographic controls confirms the
affine candidate reaches the explicit geographic-matrix gate and applies no transforms.
Settings, readiness, similarity workflows, and CRS refit regressions are run separately
because the combined Windows Qt suite can stall after repeated QApplication teardown.
The final affine/settings/readiness/workflow/storage run passed 247 tests.

Next: broaden affine network conditioning tests and enable selective mixed model/graph
support only when its gauge and rank behavior are explicit. Projective alignment and
projected-CRS affine georeferencing are implemented; geographic-CRS fitting, cross-CRS
warped export, and independent GIS validation remain.

### Eighth Checkpoint: Native World-Mapping Preservation

- Project format 23 stores each available image world transform as a native-CRS
  `ImageMapping`, separately from the active-CRS compatibility pose. Versions 1-22
  initialize the native mapping from the world transform and project CRS they contain.
- Applying a georeference result records its matrix and result CRS. On later CRS changes,
  the native mapping is retained while existing similarity refits continue to update the
  compatibility pose; the World Map now evaluates native pixels through a direct CRS
  mapping to Web Mercator instead of reprojecting the refitted pose.
- Project snapshots, save/load, undo, and redo preserve the native mapping. A CRS-switch
  regression compares mapped source pixels to direct pyproj results before and after the
  active-CRS refit.
- Similarity refits retain active-CRS compatibility poses. Affine georeferencing now
  stores its exact native matrix; same-CRS export uses it directly, while cross-CRS
  affine export inverse-warps onto a bounded explicit output grid. Projective mapping
  export remains future work.

Focused validation passed 176 tests across mapping serialization/migration, layouts,
map rendering, CRS switching/refit rollback, and status/CRS workflows. Pylance reports
no errors in the touched model and app modules.

### Ninth Checkpoint: Antimeridian World Wrapping

- Projective and nonlinear map images that cross the Web Mercator seam render in the
  canonical world and translated adjacent-world copies. Seam-spanning mesh cells remain
  subdivided/dropped to avoid false bridges.
- Image fitting uses a compact interval around the date line instead of treating the two
  sides as a nearly world-wide extent. The scene supports unwrapped X placement; OSM tile
  requests wrap X indices modulo the zoom level and reuse canonical tile cache entries.
- Focused validation passed 64 tests across warped mesh accuracy, antimeridian rendering,
  map controls/navigation, and wrapped tile requests. Pylance reports no errors in the
  touched map renderer and view.

### Tenth Checkpoint: CRS Operation Metadata

- CRS mapping diagnostics report the description, nominal accuracy, area of use, and
  selected grids from the same cached `Transformer` used for coordinate evaluation.
  `TransformerGroup` adds unavailable higher-accuracy candidate grids without changing
  operation selection or map coordinates.
- Warped map-image tooltips show this operation metadata, label pyproj's unknown accuracy
  (`-1`) as unknown, identify ballpark operations, and list unavailable candidate grids.
  Display-mesh error remains separate from CRS/datum operation accuracy.
- Focused validation passed 79 tests across CRS mapping, operation metadata, warped image
  tooltips, map rendering, OSM tiles, and navigation. Pylance reports no errors in the
  touched mapping and renderer modules.

### Eleventh Checkpoint: Native Affine Export

- Export selects a persisted native affine mapping when its CRS matches the requested
  output CRS, preserving shear, rotation, scale, and translation in GeoTIFF transforms,
  world files, and mask cutlines. Legacy similarity exports remain unchanged.
- Projective native mappings still require inverse-warp export. Affine native mappings
  in a different output CRS use the explicit bounded warp-grid path rather than being
  flattened to the active-CRS compatibility pose.
- Focused validation passed 122 tests across export writers/workers/workflows and project
  mapping serialization. Pylance reports no errors in `export.py`.

### Twelfth Checkpoint: Projected Affine Georeferencing

- Affine georeferencing fits a normalized six-coefficient group-frame-to-CRS mapping,
  refines geometric residuals with the configured robust loss, and composes that matrix
  with each linked image's source-to-group similarity frame.
- Readiness requires non-collinear controls in both group-frame and geographic target
  coordinates. Full-rank but poorly conditioned geometry warns; fitted stretches, condition,
  and reflection policy are checked. Affine currently requires a projected CRS because its
  thresholds are expressed in CRS units; Projective georeferencing also requires known
  source dimensions and validates its homography across each image domain.
- Results retain exact per-image native affine matrices, apply/save/undo as one operation,
  render through the native CRS map chain, and export exactly when output and native CRSs
  match. Affine and Projective mappings inverse-warp to an explicit output grid when needed;
  unsupported output paths are rejected rather than flattened.
- Focused validation passed 105 solver/readiness/UI/workflow tests and 122 export/worker/
  persistence tests. Pylance reports no errors in the touched solver and app modules.

### Thirteenth Checkpoint: Cross-CRS Affine Warp Grid

- Export now accepts an explicit output CRS, pixel size, nearest/bilinear resampling, and
  optional shared bounds. Empty bounds derive a sampled, resolution-snapped extent from
  each native image footprint.
- Cross-CRS Affine and Projective rasters are inverse-sampled in bounded 256x512-pixel
  chunks through pyproj and the inverse native matrix. Output grids are limited to 20 million
  pixels per image; outside-source samples become transparent. Source masks are resampled
  into alpha using premultiplied-alpha bilinear sampling to avoid transparent-color fringes;
  mask cutlines are densified/reprojected to the output CRS.
- Cross-CRS warped JPEG and images lacking native CRS provenance are refused explicitly.
  World-file images also publish a GDAL PAM metadata sidecar alongside `.prj`. Rasterio
  is added only to the test extra; independent-reader tests verify native/warped PNG and
  JPEG world files and native/affine-warped/projective-warped GeoTIFF CRS, transforms,
  pixels, and alpha. Pyproj transforms preserve continuous coordinates across the
  antimeridian for automatic grid bounds, inverse sampling, and reprojected mask cutlines.
  Broader CRS/edge cases and high-volume performance remain release validation tasks.
- Focused validation passed 40 export/GIS-reader tests, 7 Qt worker/workflow tests, and
  92 persistence tests in separate runs. A combined Windows run hit a native access
  violation in a pooled Qt worker and is not counted. Pylance reports no errors in the
  export, dialog, worker, or app modules.

### Fourteenth Checkpoint: Projective Georeferencing and Warp Export

- Projective georeferencing now fits one normalized DLT/SVD-seeded homography per linked
  group in a projected CRS, refines geometric residuals, and composes the result with each
  image's source-to-group mapping. Every image needs known source dimensions; rank,
  horizon, sampled stretch, condition, and reflection checks run before application.
- Projective results retain exact native homographies and apply/save/undo through the map
  renderer. Export inverse-samples them onto an explicit grid in the same or a different
  CRS; it does not approximate them as affine world files.
- Focused solver, readiness, and UI coverage passed 94 tests. A solve/apply/save/projective
  GeoTIFF workflow and Rasterio readback pass. Remaining acceptance includes more noisy and
  multi-image networks, grid/mask edge cases, non-projected CRS local-metric support, and
  independent-reader validation across supported raster formats.
- Combined Projective/Affine solver, readiness, dialog, alignment, similarity/refit
  regressions passed 128 tests. Export/grid/dialog/worker/persistence plus Rasterio checks
  passed 135 tests. Geographic-CRS metric fitting and broader GIS/performance validation
  remain open.

### Fifteenth Checkpoint: Antimeridian-Safe Raster Export

- Cross-CRS grid planning, inverse pixel sampling, and mask cutline reprojection use
  continuous coordinates through the antimeridian instead of wrapping longitudes back
  across the world. Automatic grids remain compact for date-line-crossing imagery.
- Regressions check the bounded grid, recovered pixels on both sides of the seam, and
  continuous cutline coordinates. The explicit output grid may extend beyond the canonical
  coordinate range of a periodic CRS such as Web Mercator; this is necessary to keep the
  image contiguous and is represented by the output geotransform.
- Export, GIS-reader, grid-dialog, worker, and workflow validation passed 50 tests.
  Broader CRS operation/edge cases and high-volume performance remain release validation.

### Sixteenth Checkpoint: Local-Frame Mapping and Geographic Fitting

- Native ImageMapping records may declare a matrix-output CRS distinct from their
  native/project CRS. The record exposes the exact matrix-plus-CRS MappingChain and stores
  the extra CRS metadata using mapping-record version 2; matrix-only records retain version 1.
- Project format 24 stores these records; project formats 1-23 remain readable. Tests cover
  JSON save/load, transformed coordinates, and rejection of an invalid working CRS.
- The World Map now composes the stored chain with its native-CRS-to-Web-Mercator step;
  an integration regression compares an image interior point with direct pyproj transforms.
- Raster export plans bounds through the full forward chain, inverse-samples output pixels
  through native CRS and matrix CRS, and reprojects mask cutlines through both steps. A
  local-frame matrix forces inverse-warp export even when output CRS equals native CRS.
- GeoreferenceResult may carry validated native ImageMapping records alongside its matrix
  view. Its legacy world-pose tuple is checked against the mapped source origin and first
  pixel axis; application and CRS-refit paths preserve the record through project snapshots,
  save/load, undo, and redo.
- Geographic-CRS Affine/Projective fitting now uses one datum-preserving local AEQD frame
  per connected group. Residuals, robust thresholds, and matrix validity checks use metres;
  results retain the exact frame-to-project CRS chain. Readiness warns when controls span a
  wide frame or sampled pairwise distortion is high. Full image-domain distortion and CRS
  unit changes remain acceptance work.
- Map/warped-image validation passed 52 tests; export/GIS-reader/grid/worker/workflow passed
  53; local/projected Affine/Projective workflows passed 11; readiness/solver-method tests
  passed 47; solver-dialog/report tests passed 51; mapping-storage/project-file tests passed
  95; CRS-refit/status tests passed 15. Pylance reports no errors in touched solver, app,
  readiness, dialog, and test files.

### Seventeenth Checkpoint: Local-Frame Image-Domain Diagnostics

- Geographic Affine/Projective results sample each known source domain on a 5x5 grid,
  bounded to 64 samples per connected group. The report separates maximum mapped-domain
  radius and sampled AEQD-vs-geodesic distance distortion from the existing control-only
  metrics.
- Large extent/distortion, unavailable dimensions, or a failed sample produces a report
  warning rather than blocking an otherwise valid fit. Projective horizon and Jacobian
  validity checks remain blocking; sampled distortion is not presented as a global bound.
- A regression fits clustered controls on a large image and verifies the mapped-domain
  warning metrics detect the extrapolated extent. Geographic Affine/Projective fit,
  readiness, and report checks passed 5 focused tests. Independent checkpoint coverage,
  broader domain thresholds, and CRS-unit transition policy remain open.

### Eighteenth Checkpoint: CRS-Stable Georeference Settings

- CRS changes convert georeference tolerance, robust-loss scale, similarity scale bounds,
  and Affine/Projective stretch bounds when source and target units share an exact scalar
  conversion. The rebased settings are part of the existing CRS undo transaction.
- Angular/linear transitions, or axis systems without one exact scalar conversion, ask
  before retaining and reinterpreting the saved numeric values. Cancellation leaves the
  project and CRS selector unchanged.
- Solver settings and result reports identify effective units, including local metres,
  degrees, metres, and named projected units such as US survey feet. CRS/settings, dialog,
  refit, confirmation, and undo validation passed 51 tests. Broader CRS-axis cases and
  independent geographic checkpoints remain open. The complete solver-dialog and
  geographic-metric integration suites also passed 53 tests.

### Nineteenth Checkpoint: Datum-Aware Local Distortion

- Control-pair and mapped-image-domain distortion comparisons now use the ellipsoid of
  the project geographic CRS instead of unconditionally using WGS84. The local AEQD frame
  and its distortion diagnostics therefore use a consistent datum ellipsoid.
- OSGB36 regressions compare both control and mapped-domain distortion values against
  direct geodesic calculations on the Airy ellipsoid. The CRS operation, local-metric,
  geographic fit, and readiness regression slice passed 21 tests; Pylance reports no
  errors in the touched solver/test files.
- Operation accuracy, area-of-use, grid availability, and datum behavior still need
  broader independent coverage across additional CRSs and geographic edge cases.

### Twentieth Checkpoint: Selected CRS Operation Validation

- Runtime CRS mapping is checked against the selected pyproj transformer for OSGB36 to
  WGS84 in Great Britain and NAD83 to Web Mercator at high latitude. The regression
  verifies coordinate agreement, nominal accuracy, area of use, grid disclosure, and
  ballpark/best-available reporting when applicable.
- Looking up operation metadata is verified not to change the mapped coordinate. The
  complete mapping-operation and map-tooltip regression slice passed 17 tests; Pylance
  reports no errors in the new operation tests.
- Additional regional grids, datum operations, antimeridian controls, and independent
  holdout comparisons remain release validation work.

### Twenty-First Checkpoint: Independent Geographic Holdout

- Affine and Projective synthetic fits now reserve a source location outside the training
  control set and compare its mapped geographic position with the known truth using
  geodesic error. The checkpoint is absent from the solver's observation residuals.
- The fitted mapping is saved and reopened, then used to inverse-warp a one-pixel
  cross-CRS export centered on the holdout location. Both model cases sample valid source
  pixels and stay below 1 m checkpoint error.
- The complete local-metric test file passed 6 tests with no Pylance errors. Marker/raster
  agreement, masks, CRS refit, and undo/redo checkpoint cases remain open.

### Twenty-Second Checkpoint: Holdout Through CRS Refit

- The CRS-refit workflow now checks an unobserved source pixel against its direct native
  CRS-to-Web-Mercator position after refit, undo, and redo. The GCP used by the refit and
  this withheld pixel are tested separately.
- The combined CRS-refit and local-metric suites passed 17 tests with no Pylance errors.
  Independent map-raster pixel and mask checkpoint cases remain open.

### Twenty-Third Checkpoint: Holdout Marker and Mask Agreement

- The held-out geographic checkpoint now compares its World Map point-overlay scene
  position with the fitted warped raster's source-to-scene position for both Affine and
  Projective fits. The check uses the saved/reopened mapping chain.
- Cross-CRS exports centered on the held-out source pixel verify opaque output inside the
  image mask and transparent output outside it for both inside/outside mask modes, using
  EPSG:3857, UTM zone 60N, and OSGB36-to-British-National-Grid output paths. The twelve
  fit/mask/CRS combinations pass under nearest-neighbor sampling. A concave mask notch
  distinguishes the held-out pixel from an adjacent excluded source location, and unique
  source colors verify both exported raster samples. The combined local-metric,
  mask-alpha, and painted-raster regressions passed 18 tests with no Pylance errors.
- More independent raster-color probes and additional mask geometries/CRSs remain release
  validation work.

### Twenty-Fourth Checkpoint: TPS Numerical Prototype

- A test-only prototype composes regularized SciPy thin-plate-spline displacement over a
  fixed base matrix, normalizes source controls, limits evaluation to their convex hull,
  numerically inverts with bounded least-squares, samples Jacobian stretch/orientation,
  and reconstructs the fit from serialized input controls and settings.
- Synthetic checks cover base-transform equivalence, reduced withheld error under
  smoothing, forward/inverse consistency, reconstruction, unsupported extrapolation, and
  rejection of a sampled local fold. The prototype suite passed 4 tests; Pylance reports
  no errors.
- SciPy's public `RBFInterpolator` API exposes neither fitted coefficients nor Jacobians.
  The test serializes fit inputs and refits on load; production persistence needs an owned,
  versioned coefficient/basis representation and a validated derivative/inverse policy.
  No project format, solver choice, or application path uses this prototype.

### Twenty-Fifth Checkpoint: Coefficient-Backed TPS Mapping

- `geostitch.warp.WarpTransform` fits regularized TPS displacement over a fixed
  `MatrixTransform` base. Source controls are normalized; the augmented radial/polynomial
  coefficients, base matrix, smoothing value, and convex-hull support policy are stored
  explicitly and round-trip through a versioned mapping record.
- Forward evaluation rejects extrapolation outside the control convex hull. Analytic TPS
  derivatives compose with the base Jacobian; sampled relative-area and stretch checks
  reject observed folds/collapse. The inverse uses SciPy bounded least-squares and accepts
  a solution only when residual and support checks pass.
- Tests compare coefficient evaluation to public SciPy `RBFInterpolator`, verify base
  equivalence, regularization, analytic derivatives, inverse round trips, serialization,
  and sampled fold rejection. The new transform and prior numerical prototype passed
  9 tests; Pylance reports no errors.
- This is not yet part of `MappingChain`, Project serialization/versioning, solver,
  rendering, or export. The 512-control cap bounds the dense fit, and sampled Jacobians
  are not a global injectivity guarantee; end-to-end integration remains gated.

### Twenty-Sixth Checkpoint: TPS Georeferencing Application Workflow

- Project format 25 accepts native TPS mapping records (image-mapping record version 3);
  versions 1-24 still load. `MappingChain` composes forward/inverse warp steps without
  flattening them to a matrix. TPS settings persist smoothing, relative-area, and stretch
  limits; the settings dialog offers TPS only for georeferencing and readiness blocks
  unsupported CRS, insufficient/collinear controls, and missing source dimensions.
- The solver fits one shared local-metric warp per linked image group, aggregates linked
  control components to avoid duplicate basis centers, composes each image frame into its
  source-to-world warp, validates the full source rectangle at sampled locations, and
  reports per-observation residuals. TPS remains disallowed for image alignment.
- Native mappings render through the existing adaptive World Map chain and inverse-sample
  explicit export grids in bounded batches. Project save/load, undo/redo, map placement,
  masked GeoTIFF export, and a linked two-image holdout are covered.
- The dense TPS coefficient solve is capped at 512 basis controls. Support is the convex
  hull of linked controls plus each source image's corners; samples outside support export
  transparent and inverse requests fail unless they converge back inside that support.
  Sampled positive Jacobians are not a proof of global injectivity.
- TPS/mapping/storage/readiness/UI/alignment/export/rendering regressions passed 247 tests;
  Pylance reports no errors. This does not complete Phase 7: high-density/large-image
  checkpoints, high-DPI and seam tests for nonlinear warps, non-convex support policies,
  and performance profiling remain. Joint adjustment and TPS alignment remain Phase 8 or
  later work.

### Twenty-Seventh Checkpoint: Independent Geographic Checkpoints

- Project format 26 persists an explicit geographic-checkpoint role; legacy points default
  to fit controls. The point details panel can mark/unmark a complete observed point, and
  layer text/tooltips identify checkpoint points, and the World Map uses a distinct marker.
  Undo/save/load use the regular project snapshot path.
- Georeference solvers exclude checkpoint coordinates from fit matches and checkpoint-only
  links from connected-group topology, while alignment continues to use those links. The
  final native mapping is evaluated separately at each checkpoint; reports and post-solve
  status list mapped/unmapped coverage and maximum checkpoint error without including that
  error in the training target.
- The TPS application regression marks a known-offset GCP through the UI, verifies it is
  absent from fit residuals, measures its independent error, and confirms the role survives
  project save/load and CRS refit/undo/redo. Its on-demand report shows training residuals
  and checkpoint errors in separate sections; the World Map draws checkpoints as distinct
  diamond markers. Separate Affine and Projective tests confirm checkpoint errors do not
  influence training convergence. A checkpoint-only tie cannot connect an otherwise
  ungrounded TPS fit group. Broader marker/raster, masks, and failed-transform validation
  remains open.

## Goals and Boundaries

- Add affine, projective, and eventually regularized deformable fitting to alignment and georeferencing.
- Preserve current translation, rigid, similarity, disconnected-group, preview, auto-apply, cancellation, and undo workflows.
- Keep observations and masks in original EXIF-oriented source pixels. Geographic controls retain explicit CRS semantics.
- Make images, derived point positions, links, outlines, masks, map footprints, and exports use the same authoritative mapping.
- Preserve existing canvas layout semantics. The shared canvas remains alignment/layout space; the map remains Web Mercator display space. Neither view silently changes the project's CRS.
- Define accuracy as measured numerical, display, and export tolerances. Control accuracy, datum accuracy, terrain, and basemap accuracy remain separate limitations.
- Do not enable solver models before their results can be saved, edited, rendered, and exported without losing geometry.

## Current Systems and Gaps

| System                         | Implemented                                                                                                                                                     | Remaining                                                                                                                                  |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Domain models                  | Per-image points/observations, masks, native GCP provenance, source dimensions, and format-24 matrix-to-CRS mapping records.                                    | Scalar pose fields remain compatibility state; source-repair and mapping-domain policy needs continued validation.                         |
| Transform math                 | `MatrixTransform`, `CRSMapping`, and `MappingChain` support validated matrix/CRS composition, inversion, and serialization.                                     | Deformable mapping representation and a validated nonlinear inverse are not implemented.                                                   |
| Solvers                        | Translation/Rigid/Similarity plus Affine/Projective alignment and Affine/Projective georeferencing in projected or geographic CRSs.                             | Full image-domain local-frame diagnostics and CRS-unit transition policy; then deformable and optional joint models.                       |
| Canvas and source review       | Matrix-aware layout edits, source-pixel inverse observation edits, and unwarped Image View/Link Review.                                                         | Preserve these editing semantics as solver models expand; no warped source-review mode is required yet.                                    |
| World Map                      | Adaptive raster and observation-derived GCP rendering through native matrix-to-CRS chains, antimeridian copies, vectors, and CRS-operation metadata.            | Validate mapped marker/raster agreement across projection edges, multiple CRSs, and high DPI.                                              |
| CRS workflows                  | Native GCP coordinate provenance, native image mapping preservation, chain-aware display/export/result application, transactional undo/rollback.                | Compatibility-pose refits remain approximate; measure domain disagreement and preserve or confirm distance-setting units when CRSs change. |
| Export                         | Native affine writers and bounded explicit-grid Affine/Projective warping with chain-aware sampling, alpha, cutlines, collision protection, and staging.        | Warped JPEG is refused; broaden CRS/driver/edge, performance, and packaged-app validation.                                                 |
| Settings and reports           | Persisted Affine/Projective options, robust-loss settings, local-metre labels/reports, per-group frame extent/distortion, and mapping-aware alignment feedback. | Add image-domain distortion/checkpoint coverage and stable unit semantics across CRS changes.                                              |
| Packaging and GIS dependencies | PySide6/Pillow/NumPy/SciPy/pyproj runtime path; Rasterio is test-only for independent reading.                                                                  | Validate the installed Windows build, supported raster drivers, CRS operation/grid behavior, startup, and memory/performance.              |

Primary current owners: `src/geostitch/models.py`, `mapping.py`, `transforms.py`,
`adjustment.py`, `georeference.py`, `solver_readiness.py`, `canvas.py`, `image_item.py`,
`map_view.py`, `app.py`, `alignment_feedback.py`, `solver_dialog.py`, `image_tiles.py`,
and `export.py`.

## Authoritative Mapping Design

Introduce immutable, Qt-independent mapping objects with double-precision data:

- MatrixTransform: a normalized 3x3 matrix plus model identity. Similarity and affine are restricted forms; projective is a homography.
- WarpTransform: a regularized deformation model with its basis/grid, normalization, support domain, boundary policy, and coefficients.
- MappingChain: ordered mapping steps, including optional CRS reprojection. Composition must not approximate a nonlinear chain as a matrix.
- Mapping metadata: source domain and dimensions, orientation convention, input/output frames, native CRS when geographic, and mapping revision.

The API provides batch forward mapping, inverse mapping with validity/convergence masks,
Jacobian evaluation, sampled bounds, composition, serialization, and domain validation.
Matrix inverses are analytical. Warp inverses require a tested numerical procedure and
must explicitly fail where a unique valid inverse cannot be established.

Use an explicit source-pixel convention, including current pixel-edge/pixel-center and
EXIF behavior. Absorb the image-Y/world-Y sign change in a named frame conversion;
do not scatter negative-Y conversions between solvers, views, and exports.

Store separate rest, aligned, and geographic mappings. Keep the editable outer pose
separate from an intrinsic warp where necessary: existing move/rotate/scale handles can
compose an outer pose without destroying shear, perspective, or deformation. General
matrices must never be reconstructed from scalar Qt position/rotation/scale getters.

Keep original source pixels and source files unchanged. Image View and Link Review
continue to edit source observations. An optional warped preview is a separate view mode.

## Persistence and Compatibility

- Advance the project format when introducing mappings; advance the saved report schema when its transform/signature representation changes.
- Read supported old projects and convert legacy scalar transforms algebraically into exact equivalent mappings. Preserve unset positions and unavailable results as explicit states.
- Provide temporary compatibility adapters for similarity callers, but maintain one authoritative stored representation. Fail explicitly when a general mapping cannot be represented by a legacy tuple.
- Never write an old-format project by dropping shear/perspective/warp data. Older applications should reject the new version.
- Persist source dimensions and EXIF-oriented geometry independently of previews. Validate mapping domains after source repair; changed geometry needs confirmation or invalidation.
- Persist warp coefficients and basis metadata, not Qt objects, caches, or triangulated display meshes. Rebuild derived caches on load.
- Version signatures for stale feedback. Include mapping inputs, relevant settings, topology, and coordinate frames; exclude display-only choices.
- Ensure snapshots, copy/export workers, undo/redo, and saved reports preserve the complete mapping and native CRS.
- Bound serialized array sizes and validate finite values, dimensions, invertibility, references, and model-specific domain safety.

## Delivery Phases

### Phase 1: Mapping Foundation Without New Solver Choices

Implement the mapping API, frame conventions, new persistence, legacy adapters, and
matrix-aware result objects. Route existing similarity calculations through it first.
Replace direct field arithmetic in app.py with domain operations incrementally.

Acceptance: legacy projects keep the same source-to-layout/world coordinates; save,
load, undo, redo, CRS rollback, and export snapshots preserve them. Forward/inverse
round trips pass at small and large coordinate magnitudes. All existing solver and
serialization tests remain green. No new model dropdown entries yet.

### Phase 2: Canvas and Shared Update Integration

Apply matrices using QTransform with reset scalar state to avoid double composition.
Change transform signals and update dictionaries to carry complete mappings. Maintain
keyed item reuse and ongoing drag poses; matrix revisions invalidate all affected items.
Screen-sized point overlays remain separate from raster geometry.

Route point dragging through the authoritative inverse. Rebuild transformed outlines,
link endpoints, mask paths, bounds, fit-to-selection, and handle locations from the same
mapping. Ordinary handles edit an outer pose; affine/projective-specific handles are
optional later, not prerequisites for preserving solver results.

Acceptance: synthetic shear and perspective mappings survive refresh, drag, apply,
undo/redo, and aligned/rest toggles. Marker positions agree with mapped source pixels.
No stale areas or changes to observation coordinates. Preserve source-review workflows.

### Phase 3: Accurate CRS Display and Shared Warped Rendering

Replace the map's three-corner approximation with a renderer that evaluates
source -> image mapping -> native/project CRS -> Web Mercator -> screen.
Use an exact matrix fast path only when the complete mapping is matrix-representable.
For nonlinear reprojection, generate adaptive warped tiles or meshes from that pipeline.
Share the rendering infrastructure with later deformable canvas display.

Perform a backend spike comparing GDAL/rasterio-based warped tiles with adaptive mesh
rendering. Choose on correctness, cancellation, bounded memory, zoom behavior, Windows
packaging, and seam quality. Do not duplicate CRS formulas in a custom renderer.

Use screen-space error tolerance and additional edge/interior validation samples to
drive subdivision. Proposed normal display target: at most 0.25 physical display pixel
approximation error within a valid continuous domain, subject to measured validation.
Expose failures rather than presenting that target as a mathematical guarantee.

Keep source tiles reusable. Key warped tile/mesh caches by image geometry, mapping
revision, CRS pipeline, zoom/quality, mask revision, and relevant resampling settings.
Compute visible source regions through inverse mapping and estimate local magnification
from the Jacobian, not only view.transform().m11(). Use generation IDs to reject stale work.

Map target GCP markers represent supplied coordinates; fitted observation markers
represent mapped pixels. Preserve this distinction and offer residual connectors so a
real fitting error is not confused with rendering mismatch. Densify mask boundaries,
footprints, and vector segments for nonlinear CRS display. Split invalid/wrapped regions;
never connect across the antimeridian or a projection discontinuity accidentally.

Acceptance: image interiors, boundaries, masks, and fitted markers agree with direct
pyproj evaluation over test grids across several CRSs, rotations, and extents. Cover
Web Mercator latitude limits, invalid domains, antimeridian handling, high DPI, seams,
zoom/pan, stale jobs, and native-detail switching. Existing map navigation remains intact.

### Phase 4: Affine Solvers and Export

Use normalized coordinates and rank-revealing linear least squares for affine seeds;
retain robust/nonlinear refinement as appropriate. Solve six parameters per variable
image or controlled group, fixing the gauge per disconnected alignment component.

Require sufficient geometric rank, not just three counted matches. Diagnose collinear,
duplicated, clustered, and poorly conditioned controls. Add bounds/regularization for
principal scales and shear, with an explicit reflection policy. Do not reinterpret a
single uniform scale bound as a bound on every matrix entry.

Compose group and image mappings algebraically. Retain the current group-georeference
mode as the default. Affine alignment and projected-CRS affine georeferencing are now
implemented; geographic-CRS fitting and independent validation of cross-CRS affine export
remain gated.

The GeoTIFF/world-file writers consume persisted native affine coefficients when the
output CRS matches. Cross-CRS Affine and Projective export now use an explicit CRS,
extent, pixel size, and resampling choice, with a 20-million-pixel per-image budget.
Validate output grid pixel centers, CRS metadata, masks/cutlines, nodata/alpha, and
independent third-party reading across more drivers and edge cases.

Acceptance: exact recovery of independent X/Y scales, rotation, translation, and shear;
stable noisy/robust fits; degeneracy refusal; independent groups; canvas/map consistency;
and affine exports independently reopened and checked against source-pixel mappings.

### Phase 5: Projective Solvers and Warped Export

Alignment-only projective fitting is now implemented as an initial Phase 5 checkpoint.
It uses normalized DLT/SVD seeds followed by geometric residual refinement, requires
four correspondences in general position for each image pair, and fixes one reference
image per connected component. Variable images require known source dimensions. Final
homographies are checked for horizon clearance and sampled 7x7 local Jacobian stretch,
conditioning, and reflection policy. Project format 22 persists its solver settings;
format 23 adds native world-mapping provenance, format 24 stores local-frame CRS metadata,
and formats 1-23 remain readable with default projective limits. The canvas, alignment
feedback, apply/save, and undo/redo paths retain complete projective matrices.

Projected-CRS Projective georeferencing and inverse-warp export are now implemented as
an additional Phase 5 checkpoint. Projective fitting requires known dimensions and full
domain checks; exports require an explicit grid and resample pixels. Geographic-CRS
Projective fitting and broad independent export validation remain gated.

Focused validation passed 211 projective/settings/UI/readiness/persistence/layout tests
and 42 existing adjustment, affine alignment, solver workflow, and CRS-refit tests.
Pylance reported no errors in the touched solver, model, dialog, readiness, or app modules.

Use normalized DLT/SVD for initialization and geometric residual refinement. A standalone
homography needs four correspondences in general position; validate network rank and
control coverage as well. Check invertibility and the denominator over the entire source
domain, rejecting near-horizon, unbounded, collapsed, or unintended reflected mappings.

Projective parameters have no single global scale. Replace scale-only constraints with
local Jacobian stretch/conditioning limits evaluated across the domain. Report unsupported
regions and extrapolation risk separately from residual accuracy.

Add export backend support for inverse warping onto an explicitly chosen affine grid.
Choose output CRS, resolution, extent, interpolation, alpha/nodata, and pixel alignment.
A GCP/TPS approximation must not silently substitute for an exact homography; verify
the selected backend implements the intended mapping, or use explicit inverse sampling.
Retain native source resolution options only where meaningful; warping resamples pixels.

World files describe the resulting output grid, not the original homography. Inspect the
composed mapping: a projective aligned image remains projective even when its later
geographic group fit is affine. Unsupported direct exports must fail with actionable text.

Acceptance: homography recovery, domain/horizon rejection, inverse consistency, and
identical geometry across canvas, map, warped raster, alpha, and cutlines. Check output
grid/half-pixel conventions independently and retain collision/rollback guarantees.

### Phase 6: CRS Changes Without Silent Geometry Loss

Replace the single-axis CRS conversion approximation. Preserve the native mapping and
CRS, appending exact coordinate conversion to the mapping chain when the project CRS
changes. Do not assume a reprojected affine/homography is still affine/projective.

Keep control-coordinate provenance and authoritative precision to avoid repeated-roundtrip
drift. Make refitting in the new CRS an explicit operation, preserving existing transaction,
busy-state, cancellation, and undo rules. If a simplified refit is requested, measure and
report its disagreement over the image domain as well as at controls.

Handle metric regularization and robust thresholds in declared units. Geographic Affine and
Projective fitting now uses local AEQD metres per connected group; map, export, result
application, refits, and readiness consume or preserve that CRS chain. The remaining Phase 6
gates are image-domain distortion/coverage diagnostics, distance-setting unit semantics
across CRS changes, independent checkpoints, and visible operation accuracy/grid/area-of-use
information. Do not treat control-only distortion samples as a global guarantee.

Acceptance: project-CRS changes preserve physical placement through mapping chains;
repeated change/undo does not drift; failed transformations roll back; missing grids and
unknown accuracy are visible. Native mappings still export correctly into selected CRSs.

### Phase 7: Regularized Deformable Model

Start with one explicit model: regularized thin-plate-spline displacement over a stable
base mapping for modest networks. Use a proven fitting implementation, initially evaluating
SciPy facilities; verify saved coefficients, batch evaluation, and inverse requirements in
a prototype. Larger local B-spline/displacement grids are a separate future model.

Expose deformation strength, support/boundary policy, and validity limits. Fit in a suitable
metric working frame and normalize for numerical stability. Avoid ambiguous constraints
between base pose and displacement. Monitor Jacobian orientation, minimum area, maximum
stretch, and bending; sample adaptively near high curvature. Sampled checks do not prove
global injectivity: do not label them a guarantee of fold-free deformation.

Initially support a common geographic warp on an aligned group. Linked coincident points
then undergo the same mapping. Deformable per-image alignment needs a subsequent joint
solve with links, anchors, and regularization; do not fit images independently and assume
their borders still agree. Make both modes explicit before enabling them.

Add independent checkpoints that do not enter the fit. Report checkpoint errors, coverage,
unsupported extrapolation, and distortion alongside training residuals. Nearly zero training
error is not evidence of accurate geography. Reject known foldovers and invalid inverses;
warn about unsampled/uncontrolled regions rather than accepting them silently.

Acceptance: identity/base-transform equivalence, controlled smooth synthetic deformations,
regularization against noisy controls, invalid/folded warp rejection, inverse convergence,
edge behavior, checkpoints, serialized reconstruction, tiled rendering, and warped export.

### Phase 8: Optional Joint Adjustment and Per-Image Overrides

Add an opt-in geographic joint adjustment combining tie-point and world-control residuals
in consistent units. Specify relative weights/uncertainties, gauge constraints, fixed
parameters, model inheritance, and independent-group behavior. Preserve group fitting as
the default; never change its internal-geometry semantics implicitly.

Per-image/group model overrides inherit project defaults explicitly. Validate mixed-model
networks and report effective models/fallbacks; do not silently downgrade insufficiently
controlled affine/projective/deformable requests. A chosen lower-order model is a user
decision, separate from a geometry diagnostic.

Acceptance: joint constraints remain satisfied within declared tolerances, checkpoint
accuracy is independently measured, mixed-model settings round-trip, and missing controls
or disconnected groups produce predictable partial/blocked outcomes.

## Settings and User Experience

- Project solver settings own model, optimizer/loss, fitting frame, regularization, domain-validity limits, grouping strategy, and optional overrides.
- Application/view settings own rendering quality and memory budgets; they do not alter fitted geometry.
- Export settings own output CRS/grid/resolution/resampling; a frozen export plan records the effective choices.
- Keep compact post-solve warnings: target status, actual invalidity or high-error items, and the most relevant next action. Put parameters and distortion details in the full report.
- Show only controls meaningful for the chosen model. Explain fixed parameters, unsupported export paths, and approximate CRS operations before applying results.
- Separate target GCPs, fitted locations, and independent checkpoints in views and reports.
- Retain current defaults. Enable new models incrementally only after each end-to-end acceptance gate passes.

## Workers, Performance, and Dependencies

Use immutable mapping/project snapshots in dedicated solve/render/export workers. Extend
the existing export/image-loader generation and cancellation patterns. Replace synchronous
solver execution where new workloads would block Qt; retain one apply/undo transaction on
the UI thread. Cancellation must work within long iterations and not only between stages.

Use existing numpy/scipy/pyproj and Qt first. Evaluate rasterio/GDAL in the rendering/export
spike rather than adding it speculatively. Validate Python 3.10+ support, supported Windows
interpreter wheels, PyInstaller collection, raster drivers, CRS databases/grids, license
distribution, startup cost, and installed-app behavior. Optional backends need explicit
capability reporting; never silently downgrade an unsupported transform.

Bound source/warped tile caches, deformation array sizes, mesh complexity, and output grid
dimensions. Test many images and disconnected groups, not only small single-image examples.

## Verification and Release Gates

- Unit: model math, frames/Y conventions, conditioning, bounds, derivatives, inverses, composition, serialization, and legacy conversion.
- Solver: exact synthetic recovery, noise/outliers, rank deficiency, model mixtures, anchors, isolated groups, partial controls, checkpoint exclusion, and deterministic seeds.
- Qt integration: keyed reuse, live drag preservation, source-pixel inverse editing, screen-sized markers, masks, outlines, apply/toggle/undo/redo, and full repaint when geometry changes.
- CRS: multiple projected/geographic CRSs, large coordinates, nonlinear interiors/edges, native mapping preservation, missing grids, invalid domains, and antimeridian splitting.
- Rendering: numerical sample-to-scene comparison plus desktop/high-DPI screenshots, pixel checks, tile seams, zoom/pan, view changes, and stale worker completion.
- Export: independent GIS/raster reader, affine metadata, projective/deformable warped checkpoints, grid centers/edges, CRS metadata, alpha/nodata, cancellation, collision protection, and publication rollback.
- Performance: representative image/network sizes, background responsiveness, memory ceilings, expensive inverse cases, and packaged Windows smoke tests.

Mapping/canvas foundations, adaptive CRS raster and observation-marker display, Affine/
Projective alignment and projected/geographic-CRS georeferencing, native mapping-chain
persistence/application, chain-aware Affine/Projective export, and antimeridian handling
are implemented. Remaining gates are image-domain local-frame accuracy/coverage, CRS-unit
transition policy and independent checkpoints, deformable fitting, joint adjustment, broader
independent GIS validation, performance, and packaged release checks.

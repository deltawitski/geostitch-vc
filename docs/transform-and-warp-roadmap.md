# Transform and Warp Support Roadmap

Status: implementation plan, not implemented functionality.

## Goals and Boundaries

- Add affine, projective, and eventually regularized deformable fitting to alignment and georeferencing.
- Preserve current translation, rigid, similarity, disconnected-group, preview, auto-apply, cancellation, and undo workflows.
- Keep observations and masks in original EXIF-oriented source pixels. Geographic controls retain explicit CRS semantics.
- Make images, derived point positions, links, outlines, masks, map footprints, and exports use the same authoritative mapping.
- Preserve existing canvas layout semantics. The shared canvas remains alignment/layout space; the map remains Web Mercator display space. Neither view silently changes the project's CRS.
- Define accuracy as measured numerical, display, and export tolerances. Control accuracy, datum accuracy, terrain, and basemap accuracy remain separate limitations.
- Do not enable solver models before their results can be saved, edited, rendered, and exported without losing geometry.

## Current Systems to Retain or Replace

| System                   | Retain                                                                   | Modify or replace                                                                |
| ------------------------ | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| Domain models            | Per-image points, PointConstraint links, source-pixel observations/masks | Four-scalar transform storage and has-alignment/world presence checks            |
| Transform math           | Qt-independent domain boundary                                           | Similarity-only apply/compose/invert helpers with a typed mapping API            |
| Solvers                  | SciPy, robust-loss options, independent connected groups                 | Parameter packing, seeds, geometry checks, result contracts                      |
| Canvas                   | Keyed item reuse, source images, screen-sized markers, selection/tools   | Four-value transform signals, layer application, handles, invalidation           |
| Image View / Link Review | Source-pixel editing, preview/detail loaders, source tile cache          | Explicit adapters for warped previews; do not distort normal source-review views |
| World map                | Basemap tiles, navigation, selection, stacking                           | Three-corner raster approximation with error-controlled reprojection             |
| CRS workflows            | pyproj, explicit X/Y order, transactional undo/rollback                  | Single-axis world-transform approximation and silent approximate refits          |
| Export                   | Planning, collision protection, staging, cancellation, affine writers    | Transform extraction, nonlinear raster warping, cutline mapping                  |
| Settings/reports         | Per-project settings, compact warnings, retained run configuration       | Model-specific options, validity/coverage/checkpoint reporting                   |
| Packaging                | Existing Windows build workflow                                          | Bundle and validate any additional raster backend and CRS grids                  |

Primary current owners: src/geostitch/models.py, transforms.py, adjustment.py,
georeference.py, solver_readiness.py, canvas.py, image_item.py, map_view.py,
app.py, alignment_feedback.py, solver_dialog.py, image_tiles.py, and export.py.

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
mode as the default. Enable Affine only after reports, readiness, canvas/map rendering,
and export extraction consume its complete result.

The current GeoTIFF/world-file writers accept general affine coefficients; replace
similarity-only image_affine extraction and validate coefficients, CRS metadata,
pixel-center offsets, masks, and third-party reading.

Acceptance: exact recovery of independent X/Y scales, rotation, translation, and shear;
stable noisy/robust fits; degeneracy refusal; independent groups; canvas/map consistency;
and affine exports independently reopened and checked against source-pixel mappings.

### Phase 5: Projective Solvers and Warped Export

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

Handle metric regularization and robust thresholds in declared units. When CRS units
change, convert meaningful distance settings or require confirmation rather than blindly
reusing values. Geographic CRS fitting may need a local metric working frame; show which
frame residuals and thresholds refer to. Inspect transformation accuracy, required grids,
area of use, and approximation/fallback status through pyproj/GDAL capabilities.

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

The immediate implementation milestone is Phases 1-3: preserve existing behavior while
establishing general mappings and accurate rendering. Affine follows as the first complete
new solver model. Projective and deformable options remain unavailable until their own
rendering, validity, persistence, and export gates pass. CRS-preservation work in Phase 6
should begin alongside Phase 3 and must finish before shipping nonlinear model support.

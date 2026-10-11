from dataclasses import dataclass
from math import hypot, isfinite
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError

from geostitch.adjustment import (
    AFFINE_CONTROL_CONDITION_WARNING, _affine_control_condition, _affine_control_rank,
    _image_components, _projective_control_rank,
)
from geostitch.georeference import (
    LOCAL_METRIC_WARNING_DISTORTION, LOCAL_METRIC_WARNING_RADIUS_M,
    _chunk_frames, build_local_metric_frame,
)
from geostitch.models import Project
from geostitch.transforms import apply_similarity_transform


@dataclass(frozen=True)
class ReadinessIssue:
    stage: str
    severity: str
    code: str
    message: str
    recommendation: str
    image_ids: tuple[str, ...] = ()
    point_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()


def check_readiness(
    project: Project, stages: tuple[str, ...] = ("alignment", "georeference"),
    image_sizes: dict[str, tuple[int, int]] | None = None,
) -> list[ReadinessIssue]:
    if any(stage not in ("alignment", "georeference") for stage in stages):
        raise ValueError("Unknown solver stage.")
    issues = []
    images = {image.id: image for image in project.images}
    points = {point.id: point for point in project.control_points}
    if "georeference" in stages:
        try:
            CRS.from_user_input(project.crs)
        except (ValueError, CRSError):
            issues.append(ReadinessIssue("georeference", "Error", "invalid_crs",
                "The project coordinate system is invalid.", "Choose a valid CRS before assigning or solving geographic coordinates."))
    invalid = False
    for point in project.control_points:
        if "georeference" in stages and point.geographic_checkpoint and (
            point.geographic_x is None or point.geographic_y is None or not point.observations
        ):
            invalid = True
            issues.append(ReadinessIssue(
                "georeference", "Error", "incomplete_checkpoint",
                "An independent geographic checkpoint needs complete map coordinates and at least one image observation.",
                "Add both map coordinates and an image observation, or turn off checkpoint status.",
                tuple(observation.image_id for observation in point.observations if observation.image_id in images),
                (point.id,),
            ))
        for observation in point.observations:
            if observation.image_id not in images or not all(isfinite(value) for value in (observation.x, observation.y)):
                invalid = True
                for stage in stages:
                    issues.append(ReadinessIssue(stage, "Error", "invalid_observation",
                        "A point has a missing image or non-finite pixel coordinates.",
                        "Reconnect the point to an existing image and enter finite pixel coordinates.",
                        (observation.image_id,) if observation.image_id in images else (), (point.id,)))
        geographic = (point.geographic_x, point.geographic_y)
        if "georeference" in stages and any(value is not None and not isfinite(value) for value in geographic):
            invalid = True
            issues.append(ReadinessIssue("georeference", "Error", "invalid_geographic",
                "A point has non-finite geographic coordinates.", "Enter finite X and Y coordinates in the project CRS.",
                (point.image_id,) if point.image_id in images else (), (point.id,)))
        elif "georeference" in stages and (geographic[0] is None) != (geographic[1] is None):
            issues.append(ReadinessIssue("georeference", "Warning", "incomplete_geographic",
                "A point has only one geographic coordinate and will be ignored.",
                "Enter both geographic X and Y, or clear both.",
                (point.image_id,) if point.image_id in images else (), (point.id,)))
    if invalid:
        return issues

    missing = tuple(image.id for image in project.images if not Path(image.path).is_file())
    if missing:
        for stage in stages:
            issues.append(ReadinessIssue(stage, "Warning", "missing_sources",
                f"{len(missing)} source image file(s) are unavailable. Stored observations can still be solved.",
                "Use File > Repair missing images before reviewing pixels or exporting these images.", missing))

    adjacency: dict[str, set[str]] = {}
    georeference_adjacency: dict[str, set[str]] = {}
    pairs = {}
    for constraint in project.constraints:
        first, second = points.get(constraint.first_point_id), points.get(constraint.second_point_id)
        if first is None or second is None or not first.observations or not second.observations:
            for stage in stages:
                issues.append(ReadinessIssue(stage, "Warning", "ignored_link",
                    "A link has a missing endpoint or observation and will be ignored.",
                    "Choose valid observed endpoints or remove the link.", constraint_ids=(constraint.id,)))
            continue
        for first_observation in first.observations:
            for second_observation in second.observations:
                source, target = first_observation.image_id, second_observation.image_id
                if source == target:
                    continue
                adjacency.setdefault(source, set()).add(target)
                adjacency.setdefault(target, set()).add(source)
                if not first.geographic_checkpoint and not second.geographic_checkpoint:
                    georeference_adjacency.setdefault(source, set()).add(target)
                    georeference_adjacency.setdefault(target, set()).add(source)
                pair = tuple(sorted((source, target)))
                first_location, second_location = (first_observation.x, first_observation.y), (second_observation.x, second_observation.y)
                if source != pair[0]:
                    first_location, second_location = second_location, first_location
                pairs.setdefault(pair, []).append((constraint.id, first.id, second.id, first_location, second_location))

    unsupported = False
    for stage in stages:
        affected = tuple(
            image.id for image in project.images if image.id in (
                adjacency if stage == "alignment" else georeference_adjacency
            ) and (
                image.rest_intrinsic is not None and project.alignment_settings.transform_model not in ("affine", "projective", "tps") if stage == "alignment" else
                (image.aligned_intrinsic is not None if image.has_alignment else image.rest_intrinsic is not None)
            )
        )
        if affected:
            unsupported = True
            issues.append(ReadinessIssue(stage, "Error", "unsupported_layout_mapping",
                "This linked group contains affine or projective layout mappings not supported by the current solver.",
                "Keep this layout for editing; matrix-aware solver support is not enabled yet.", affected))
    if unsupported:
        return issues

    if "alignment" in stages:
        if project.alignment_settings.transform_model == "tps" and "georeference" in stages:
            issues.append(ReadinessIssue(
                "alignment", "Error", "tps_combined_solve",
                "TPS alignment cannot be combined with georeferencing because the combined candidate does not yet preserve nonlinear layout mappings through both stages.",
                "Run TPS alignment separately; do not apply a combined alignment/georeference solve.",
                tuple(images),
            ))
        for image_id in adjacency:
            image = images[image_id]
            frame = (image.position_x if image.position_x is not None else 0.0,
                     image.position_y, image.rotation, image.scale)
            if not all(isfinite(value) for value in frame) or image.scale <= 0:
                issues.append(ReadinessIssue("alignment", "Error", "invalid_frame",
                    "A linked image has invalid rest coordinates, rotation, or scale.",
                    "Set finite transform values with positive scale before alignment.", (image_id,)))
        if not pairs:
            issues.append(ReadinessIssue("alignment", "Error", "no_links",
                "No links connect observed points in different images.",
                "Add matching points in at least two images and link their observations.", tuple(images)))
        else:
            components = _image_components(adjacency, list(images))
            if len(components) > 1:
                issues.append(ReadinessIssue("alignment", "Warning", "disconnected_groups",
                    f"{len(components)} image groups will align independently, each with its own fixed reference.",
                    "Add cross-group links if these images should share one alignment.",
                    tuple(image_id for component in components for image_id in component)))
            if project.alignment_settings.transform_model == "tps":
                controls_by_image: dict[str, set[tuple[float, float]]] = {image_id: set() for image_id in adjacency}
                for pair, matches in pairs.items():
                    for _link_id, _first_id, _second_id, first_location, second_location in matches:
                        controls_by_image[pair[0]].add(first_location)
                        controls_by_image[pair[1]].add(second_location)
                for image_id, locations in controls_by_image.items():
                    image = images[image_id]
                    if _affine_control_rank(list(locations)) < 3:
                        issues.append(ReadinessIssue(
                            "alignment", "Error", "tps_alignment_geometry",
                            f"TPS alignment needs at least three unique, non-collinear linked observations on '{image.name}'.",
                            "Add linked features spread across this image; collinear or coincident controls cannot determine a stable TPS field.",
                            (image_id,),
                        ))
                    if image.source_dimensions is None and (image_sizes is None or image_id not in image_sizes):
                        issues.append(ReadinessIssue(
                            "alignment", "Error", "tps_alignment_source_dimensions",
                            f"TPS alignment requires known source dimensions for '{image.name}' to validate its full image domain.",
                            "Load or decode the image before solving.", (image_id,),
                        ))
                    starting_mapping = image.rest_mapping
                    if starting_mapping is not None and not starting_mapping.is_affine:
                        issues.append(ReadinessIssue(
                            "alignment", "Error", "tps_alignment_projective_base",
                            f"TPS alignment cannot start from the projective mapping on '{image.name}'.",
                            "Use an affine or similarity starting layout before running TPS alignment.", (image_id,),
                        ))
            for pair, matches in pairs.items():
                point_ids = tuple(dict.fromkeys(point_id for match in matches for point_id in match[1:3]))
                link_ids = tuple(match[0] for match in matches)
                model = project.alignment_settings.transform_model
                affine_insufficient = model == "affine" and (
                    _affine_control_rank([match[3] for match in matches]) < 3
                    or _affine_control_rank([match[4] for match in matches]) < 3
                )
                projective_insufficient = model == "projective" and _projective_control_rank(
                    [match[3] for match in matches], [match[4] for match in matches],
                ) < 8
                if affine_insufficient or projective_insufficient:
                    message = (
                        "Affine alignment needs three non-collinear linked observations for every image pair."
                        if affine_insufficient else
                        "Projective alignment needs four correspondences in general position for every image pair."
                    )
                    recommendation = (
                        "Add at least three matching features spread across both images; collinear points cannot determine an affine transform."
                        if affine_insufficient else
                        "Add four or more well-spread matching features; duplicated, collinear, or near-degenerate points cannot determine a homography."
                    )
                    issues.append(ReadinessIssue("alignment", "Error", "affine_geometry" if affine_insufficient else "projective_geometry",
                        message, recommendation,
                        pair, point_ids, link_ids))
                elif model == "projective":
                    for image_id in pair:
                        image = images[image_id]
                        if image.source_dimensions is None and (image_sizes is None or image_id not in image_sizes):
                            issues.append(ReadinessIssue("alignment", "Error", "projective_source_dimensions",
                                "Projective alignment requires source dimensions to validate the complete image domain.",
                                "Load or decode the image first, then retry projective alignment.", (image_id,), point_ids, link_ids))
                elif model == "affine" and max(
                    _affine_control_condition([match[3] for match in matches]),
                    _affine_control_condition([match[4] for match in matches]),
                ) > AFFINE_CONTROL_CONDITION_WARNING:
                    issues.append(ReadinessIssue("alignment", "Warning", "affine_conditioning",
                        "Affine control geometry is poorly conditioned and may amplify observation noise.",
                        "Add independent points spread across both images; avoid controls concentrated near a line.",
                        pair, point_ids, link_ids))
                elif len(matches) < 2 and project.alignment_settings.transform_model != "translation":
                    issues.append(ReadinessIssue("alignment", "Warning", "translation_only",
                        "An image pair has only one linked observation. Non-reference images touching this pair retain their rotation and scale.",
                        "Add at least one more matching point pair at a distinct location.", pair, point_ids, link_ids))
                elif len(matches) >= 2 and project.alignment_settings.transform_model != "translation" and (
                    len({match[3] for match in matches}) < 2 or len({match[4] for match in matches}) < 2
                ):
                    issues.append(ReadinessIssue("alignment", "Warning", "coincident_links",
                        "Linked observations repeat a single pixel location on an endpoint image; rotation and scale are weakly constrained.",
                        "Use distinct matching features spread across both images.", pair, point_ids, link_ids))
                elif len(matches) == 2:
                    issues.append(ReadinessIssue("alignment", "Warning", "minimal_links",
                        "An image pair has exactly two matching observations, without a third match to check local consistency.",
                        "Add an independent third matching feature when possible; other network links may provide additional checks.",
                        pair, point_ids, link_ids))
        isolated = tuple(image_id for image_id in images if image_id not in adjacency)
        if isolated and pairs:
            issues.append(ReadinessIssue("alignment", "Warning", "unlinked_images",
                f"{len(isolated)} image(s) have no cross-image links and will not be aligned.",
                "Add points and links for these images, or leave them out intentionally.", isolated))

    if "georeference" in stages:
        if project.georeference_settings.transform_model == "tps":
            try:
                tps_crs = CRS.from_user_input(project.crs)
            except (ValueError, CRSError):
                tps_crs = None
            if tps_crs is not None and not tps_crs.is_geographic:
                issues.append(ReadinessIssue(
                    "georeference", "Error", "tps_georeference_crs",
                    "Thin-plate-spline georeferencing currently requires a geographic project CRS.",
                    "Choose a geographic CRS so the TPS fit can use a datum-preserving local metric frame.",
                ))
        if project.georeference_settings.transform_model in ("affine", "projective"):
            transform_model = project.georeference_settings.transform_model
            try:
                target_crs = CRS.from_user_input(project.crs)
            except (ValueError, CRSError):
                target_crs = None
            if target_crs is not None and not (target_crs.is_projected or target_crs.is_geographic):
                issues.append(ReadinessIssue(
                    "georeference", "Error", f"{transform_model}_georeference_crs",
                    f"{transform_model.title()} georeferencing requires a projected or geographic CRS.",
                    "Choose a projected CRS or a supported geographic CRS.",
                ))
        checkpoint_ids = {
            point.id for point in project.control_points if point.geographic_checkpoint
        }
        chunk_of, frames = _chunk_frames(project, excluded_point_ids=checkpoint_ids)
        invalid_frames = set()
        for image_id, frame in frames.items():
            if not all(isfinite(value) for value in frame) or frame[3] <= 0:
                invalid_frames.add(image_id)
                issues.append(ReadinessIssue("georeference", "Error", "invalid_frame",
                    "An image's group frame has invalid coordinates, rotation, or scale.",
                    "Use a finite transform with positive scale before georeferencing.", (image_id,)))
        controls = {}
        for point in project.control_points:
            if point.geographic_checkpoint or point.geographic_x is None or point.geographic_y is None:
                continue
            for observation in point.observations:
                frame = frames[observation.image_id]
                if observation.image_id in invalid_frames:
                    continue
                location = apply_similarity_transform(observation.x, -observation.y, frame)
                controls.setdefault(chunk_of[observation.image_id], []).append((point.id, location))
        if not controls:
            issues.append(ReadinessIssue("georeference", "Error", "no_geographic_controls",
                "No observed points have both geographic X and Y coordinates.",
                "Assign coordinates in the project CRS to observed control points.", tuple(images)))
        for chunk_id in dict.fromkeys(chunk_of.values()):
            members = tuple(image_id for image_id, chunk in chunk_of.items() if chunk == chunk_id)
            matches = controls.get(chunk_id, [])
            point_ids = tuple(point_id for point_id, location in matches)
            if not matches:
                if controls:
                    issues.append(ReadinessIssue("georeference", "Warning", "ungrounded_group",
                        "An image group has no geographic controls and will not receive a world transform.",
                        "Assign geographic coordinates to points in this group, or link it to a controlled group.", members))
                continue
            model = project.georeference_settings.transform_model
            if model == "tps":
                unique_source = {
                    (round(location[0], 9), round(location[1], 9))
                    for _point_id, location in matches
                }
                if len(unique_source) < 3 or len(unique_source) != len(matches) or _affine_control_rank(
                    [location for _point_id, location in matches],
                ) < 3:
                    issues.append(ReadinessIssue(
                        "georeference", "Error", "tps_georeference_geometry",
                        "TPS georeferencing needs at least three unique, non-collinear linked control groups.",
                        "Add well-spread control groups across the linked image group; duplicated or collinear controls cannot determine a two-dimensional warp.",
                        members, point_ids,
                    ))
                missing_dimensions = tuple(
                    image_id for image_id in members
                    if images[image_id].source_dimensions is None
                    and (image_sizes is None or image_id not in image_sizes)
                )
                if missing_dimensions:
                    issues.append(ReadinessIssue(
                        "georeference", "Error", "tps_georeference_source_dimensions",
                        "TPS georeferencing needs known source dimensions for every image in the linked group.",
                        "Load or decode each image before solving so TPS support can cover its source domain.",
                        missing_dimensions, point_ids,
                    ))
            elif model in ("affine", "projective"):
                target_locations = [
                    (points[point_id].geographic_x, points[point_id].geographic_y)
                    for point_id, _location in matches
                ]
                if target_crs is not None and target_crs.is_geographic:
                    try:
                        metric_frame, transformer = build_local_metric_frame(project.crs, target_locations)
                        target_x, target_y = transformer.transform(
                            [location[0] for location in target_locations],
                            [location[1] for location in target_locations],
                            errcheck=True,
                        )
                        target_locations = list(zip(target_x, target_y))
                    except (ValueError, RuntimeError) as error:
                        issues.append(ReadinessIssue(
                            "georeference", "Error", "local_metric_frame",
                            f"A local metric frame cannot be built for this control group: {error}",
                            "Check geographic controls for valid, spatially coherent coordinates.",
                            members, point_ids,
                        ))
                        continue
                    if metric_frame.maximum_control_radius_m > LOCAL_METRIC_WARNING_RADIUS_M or (
                        metric_frame.sampled_distance_distortion > LOCAL_METRIC_WARNING_DISTORTION
                    ):
                        issues.append(ReadinessIssue(
                            "georeference", "Warning", "local_metric_extent",
                            f"Controls span {metric_frame.maximum_control_radius_m / 1000:.1f} km in the local metric frame; "
                            f"sampled pairwise distance distortion reaches {metric_frame.sampled_distance_distortion:.2%}.",
                            "Review local-frame extent and independent checkpoints; this sampled diagnostic is not a global distortion bound.",
                            members, point_ids,
                        ))
                if model == "affine":
                    if _affine_control_rank([location for _point_id, location in matches]) < 3 or _affine_control_rank(target_locations) < 3:
                        issues.append(ReadinessIssue(
                            "georeference", "Error", "affine_georeference_geometry",
                            "Affine georeferencing needs three non-collinear control locations in each linked image group.",
                            "Add at least three well-spread geographic controls; collinear source or target controls cannot determine an affine mapping.",
                            members, point_ids,
                        ))
                        continue
                    if max(
                        _affine_control_condition([location for _point_id, location in matches]),
                        _affine_control_condition(target_locations),
                    ) > AFFINE_CONTROL_CONDITION_WARNING:
                        issues.append(ReadinessIssue(
                            "georeference", "Warning", "affine_georeference_conditioning",
                            "Affine geographic control geometry is poorly conditioned and may amplify observation noise.",
                            "Add independent controls spread across the image group and geographic targets.",
                            members, point_ids,
                        ))
                else:
                    if _projective_control_rank(
                        [location for _point_id, location in matches], target_locations,
                    ) < 8:
                        issues.append(ReadinessIssue(
                            "georeference", "Error", "projective_georeference_geometry",
                            "Projective georeferencing needs four controls in general position in each linked image group.",
                            "Add at least four well-spread controls; duplicated or near-degenerate source or target positions cannot determine a homography.",
                            members, point_ids,
                        ))
                        continue
                    missing_dimensions = tuple(
                        image_id for image_id in members
                        if images[image_id].source_dimensions is None
                        and (image_sizes is None or image_id not in image_sizes)
                    )
                    if missing_dimensions:
                        issues.append(ReadinessIssue(
                            "georeference", "Error", "projective_georeference_source_dimensions",
                            "Projective georeferencing needs known source dimensions for every image in the linked group.",
                            "Load or decode each image before solving so the complete projective domain can be validated.",
                            missing_dimensions, point_ids,
                        ))
                        continue
            unique = {(round(location[0], 9), round(location[1], 9)) for point_id, location in matches}
            if len(unique) < 2 and project.georeference_settings.transform_model != "translation":
                issues.append(ReadinessIssue("georeference", "Warning", "translation_only",
                    "An image group has only one distinct geographic-control location; rotation and scale are not determined by its controls.",
                    "Add another geographic control at a distinct group-frame location.", members, point_ids))
            elif len(matches) == 2:
                issues.append(ReadinessIssue("georeference", "Warning", "minimal_controls",
                    "An image group has exactly two controls, with no redundancy for checking their consistency.",
                    "Add an independent third control and spread controls across the group.", members, point_ids))
            if len(unique) >= 2 and len({(points[point_id].geographic_x, points[point_id].geographic_y) for point_id in point_ids}) < 2:
                issues.append(ReadinessIssue("georeference", "Warning", "coincident_geographic_targets",
                    "Distinct group-frame control locations all use the same geographic coordinate.",
                    "Check copied coordinates and assign distinct real-world positions to distinct features.", members, point_ids))
        if image_sizes:
            for image_id, size in image_sizes.items():
                controls_on_image = [point for point in project.control_points
                    if point.image_id == image_id and point.geographic_x is not None and point.geographic_y is not None
                    and point.observations]
                if len(controls_on_image) < 2:
                    continue
                positions = [point.observations[0] for point in controls_on_image]
                spread = hypot(max(item.x for item in positions) - min(item.x for item in positions),
                               max(item.y for item in positions) - min(item.y for item in positions))
                diagonal = hypot(*size)
                if diagonal > 0 and spread < diagonal * 0.1:
                    issues.append(ReadinessIssue("georeference", "Warning", "clustered_controls",
                        "Controls occupy less than 10% of the source-image diagonal; extrapolation may be weak.",
                        "Spread geographic controls across the image. This is a geometry heuristic, not an accuracy estimate.",
                        (image_id,), tuple(point.id for point in controls_on_image)))
    return issues
from dataclasses import dataclass
from math import hypot, isfinite
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError

from geostitch.adjustment import _image_components
from geostitch.georeference import _chunk_frames
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
                pair = tuple(sorted((source, target)))
                first_location, second_location = (first_observation.x, first_observation.y), (second_observation.x, second_observation.y)
                if source != pair[0]:
                    first_location, second_location = second_location, first_location
                pairs.setdefault(pair, []).append((constraint.id, first.id, second.id, first_location, second_location))

    if "alignment" in stages:
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
            for pair, matches in pairs.items():
                point_ids = tuple(dict.fromkeys(point_id for match in matches for point_id in match[1:3]))
                link_ids = tuple(match[0] for match in matches)
                if len(matches) < 2 and project.alignment_settings.transform_model != "translation":
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
        chunk_of, frames = _chunk_frames(project)
        invalid_frames = set()
        for image_id, frame in frames.items():
            if not all(isfinite(value) for value in frame) or frame[3] <= 0:
                invalid_frames.add(image_id)
                issues.append(ReadinessIssue("georeference", "Error", "invalid_frame",
                    "An image's group frame has invalid coordinates, rotation, or scale.",
                    "Use a finite transform with positive scale before georeferencing.", (image_id,)))
        controls = {}
        for point in project.control_points:
            if point.geographic_x is None or point.geographic_y is None:
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
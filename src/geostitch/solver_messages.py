from __future__ import annotations

from geostitch.solver_readiness import ReadinessIssue


_READINESS_COPY = {
    "invalid_crs": (
        "The project coordinate system is not recognized.",
        "Choose a coordinate system from the CRS selector before georeferencing.",
    ),
    "incomplete_checkpoint": (
        "A checkpoint is missing its map location or image point.",
        "Add both map coordinates and an image observation, or turn off checkpoint status.",
    ),
    "invalid_observation": (
        "A point is not attached to a usable image location.",
        "Place the point on an existing image and check its pixel position.",
    ),
    "invalid_geographic": (
        "A point has invalid map coordinates.",
        "Enter finite X and Y values in the project coordinate system.",
    ),
    "incomplete_geographic": (
        "A point has only one map coordinate, so it will be skipped.",
        "Enter both map coordinates or clear both fields.",
    ),
    "missing_sources": (
        "Some source images cannot currently be found.",
        "Repair the image paths before reviewing or exporting those images. Saved point data can still be used.",
    ),
    "ignored_link": (
        "A link has a missing point and will be skipped.",
        "Reconnect the link to two placed points or remove it.",
    ),
    "unsupported_layout_mapping": (
        "This solver cannot use the current image layout transform.",
        "Use a supported starting layout, or choose a different solver model.",
    ),
    "tps_combined_solve": (
        "A deformable alignment cannot be combined with georeferencing in one run.",
        "Run alignment first, then georeference as a separate step.",
    ),
    "invalid_frame": (
        "A linked image has an invalid starting position or size.",
        "Set a valid positive image size and finite position before solving.",
    ),
    "no_links": (
        "There are no links between points in different images.",
        "Add matching points to at least two images, then link them.",
    ),
    "disconnected_groups": (
        "Some linked image groups are separate and will be solved independently.",
        "Add links between groups if they should align together.",
    ),
    "tps_alignment_geometry": (
        "A deformable alignment needs at least three well-spread points in each linked image.",
        "Add linked points in different areas of the image; points along one line are not enough.",
    ),
    "tps_alignment_source_dimensions": (
        "The full image size is needed to check a deformable alignment.",
        "Load the image, then try again.",
    ),
    "tps_alignment_projective_base": (
        "A deformable alignment cannot start from a perspective layout.",
        "Start from a regular or affine layout before using Deformable alignment.",
    ),
    "affine_geometry": (
        "The affine fit needs at least three well-spread links for each image pair.",
        "Add matching points in different areas of both images; points along one line are not enough.",
    ),
    "projective_geometry": (
        "The perspective fit needs at least four well-spread links for each image pair.",
        "Add matching points around the image area; avoid repeated or nearly straight-line points.",
    ),
    "projective_source_dimensions": (
        "The full image size is needed to check a perspective fit.",
        "Load the image, then try again.",
    ),
    "affine_conditioning": (
        "The affine links are clustered or nearly in a line, so the result may be unstable.",
        "Spread matching points across both images.",
    ),
    "translation_only": (
        "There is only one distinct link for an image pair; rotation and size will stay unchanged.",
        "Add another matching point in a different location if rotation or size should change.",
    ),
    "coincident_links": (
        "Several links use the same location in an image, so rotation and size are weakly determined.",
        "Use distinct matching features spread across the image.",
    ),
    "minimal_links": (
        "This image pair has only two links, leaving little room to check the fit.",
        "Add another independent matching point if possible.",
    ),
    "unlinked_images": (
        "Some images have no links and will not be aligned.",
        "Add points and links to include them, or leave them out intentionally.",
    ),
    "tps_georeference_crs": (
        "Deformable georeferencing needs a geographic project coordinate system.",
        "Choose a geographic CRS or use another georeferencing model.",
    ),
    "affine_georeference_crs": (
        "Affine georeferencing needs a geographic or projected coordinate system.",
        "Choose a supported coordinate system in the CRS selector.",
    ),
    "projective_georeference_crs": (
        "Perspective georeferencing needs a geographic or projected coordinate system.",
        "Choose a supported coordinate system in the CRS selector.",
    ),
    "no_geographic_controls": (
        "No placed points have map (geographic) coordinates.",
        "Enter map coordinates for observed points before georeferencing.",
    ),
    "ungrounded_group": (
        "An image group has no map coordinates and will not be georeferenced.",
        "Add map coordinates to points in this group or link it to a group that has them.",
    ),
    "tps_georeference_geometry": (
        "Deformable georeferencing needs at least three unique, well-spread map points.",
        "Add map points in different areas; duplicated or straight-line points are not enough.",
    ),
    "tps_georeference_source_dimensions": (
        "The full image sizes are needed to check deformable georeferencing.",
        "Load each image in the linked group, then try again.",
    ),
    "local_metric_frame": (
        "The map coordinates cannot be handled reliably as one local area.",
        "Check the coordinates and split distant areas into separate projects or groups.",
    ),
    "local_metric_extent": (
        "The map points cover a very large or distorted area for this fit.",
        "Review the points and independent checkpoints; consider splitting distant areas into separate groups.",
    ),
    "affine_georeference_geometry": (
        "Affine georeferencing needs at least three well-spread points in the image and on the map.",
        "Add points across the image with distinct map locations.",
    ),
    "affine_georeference_conditioning": (
        "The affine control points are clustered or nearly in a line.",
        "Spread image points and map locations across the area.",
    ),
    "projective_georeference_geometry": (
        "Perspective georeferencing needs at least four well-spread points.",
        "Add points around the image and at distinct map locations.",
    ),
    "projective_georeference_source_dimensions": (
        "The full image size is needed to check perspective georeferencing.",
        "Load each image in the linked group, then try again.",
    ),
    "coincident_geographic_targets": (
        "Different image points have the same map location.",
        "Check for copied map coordinates and correct the point locations.",
    ),
    "clustered_controls": (
        "Map points cover only a small part of the image, so other areas may be inaccurate.",
        "Add map points across the image, especially near areas you need to trust.",
    ),
}


def readiness_copy(issue: ReadinessIssue) -> tuple[str, str]:
    """Return concise, user-facing finding and next step for a readiness issue."""
    return _READINESS_COPY.get(issue.code, (issue.message, issue.recommendation))


def solver_failure_copy(stage: str, error: Exception) -> tuple[str, str]:
    """Translate common solver exceptions into a plain reason and next action."""
    detail = str(error)
    text = detail.casefold()
    if "non-collinear" in text or "general position" in text or "rank deficient" in text:
        return (
            "The selected points do not provide enough independent positions for this fit.",
            "Add more matching points spread across the images or map area.",
        )
    if "source dimensions" in text or "known source dimensions" in text:
        return (
            "The full image size is needed to check this transformation.",
            "Load the source images and retry the solve.",
        )
    if "reflect" in text:
        return (
            "The best fit would flip an image, which is currently not allowed.",
            "Check point links and positions. Allow reflections only if a flipped image is expected.",
        )
    if "stretch" in text or "condition limit" in text or "horizon" in text or "folded" in text or "collapsed" in text:
        return (
            "The fit would distort or fold part of an image too much.",
            "Check point positions and use well-spread controls; if appropriate, relax the model's advanced limits.",
        )
    if "geographic crs" in text or "projected or geographic crs" in text:
        return (
            "This fit is not supported by the project coordinate system.",
            "Choose a supported geographic or projected CRS, or use another fit model.",
        )
    if "no control points" in text or "no geographic controls" in text:
        return (
            "There are no usable points for this fit.",
            "Place points on images and add their map coordinates, or link matching points for alignment.",
        )
    if "no constraints" in text or "no links" in text:
        return (
            "There are no links connecting points in different images.",
            "Add matching points to at least two images and link them.",
        )
    if "outside" in text and "support" in text:
        return (
            "A point falls outside the area supported by the deformable fit.",
            "Add control points around the area you need to fit, or move the affected point inward.",
        )
    action = "Check the point positions, image coverage, and selected fit model, then try again."
    if stage == "georeference":
        action = "Check the map coordinates, project CRS, and selected fit model, then try again."
    return ("The solver could not complete this fit.", action)

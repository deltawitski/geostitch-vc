from dataclasses import dataclass

from geostitch.models import Project


@dataclass(frozen=True)
class LayerMatches:
    image_ids: frozenset[str]
    vector_ids: frozenset[str]
    point_ids: frozenset[str]
    link_rows: frozenset[tuple[str, str]]


def match_layers(project: Project, query: str = "", status: str = "all", image_id: str | None = None) -> LayerMatches:
    if status not in {"all", "missing_geo", "with_geo", "unlinked", "linked"}:
        raise ValueError("Unknown point filter.")
    query = query.strip().casefold()
    images = {image.id: image for image in project.images}
    points = {point.id: point for point in project.control_points}
    links_by_point = {}
    for link in project.constraints:
        for point_id in (link.first_point_id, link.second_point_id):
            links_by_point.setdefault(point_id, []).append(link)

    def contains(*values: str | None) -> bool:
        return not query or query in " ".join(value or "" for value in values).casefold()

    def point_context(point_id: str) -> str:
        point = points.get(point_id)
        if point is None:
            return ""
        image = images.get(point.image_id)
        return " ".join((point.name, point.display_name, point.label or "", image.name if image else ""))

    matching_links = {link.id for link in project.constraints if contains(
        link.name, link.kind, point_context(link.first_point_id), point_context(link.second_point_id),
    )}
    matching_images = set()
    matching_points = set()
    link_rows = set()
    if status == "all":
        matching_images.update(image.id for image in project.images
            if (image_id is None or image.id == image_id) and contains(image.name, image.path))
    for point in project.control_points:
        image = images.get(point.image_id)
        if image is None or (image_id is not None and image.id != image_id):
            continue
        geographic = point.geographic_x is not None and point.geographic_y is not None
        links = links_by_point.get(point.id, [])
        if status == "missing_geo" and geographic or status == "with_geo" and not geographic:
            continue
        if status == "unlinked" and links or status == "linked" and not links:
            continue
        direct_match = contains(point.name, point.display_name, point.label, image.name, image.path)
        if not direct_match and not any(link.id in matching_links for link in links):
            continue
        matching_points.add(point.id)
        matching_images.add(image.id)
        link_rows.update((link.id, point.id) for link in links if direct_match or link.id in matching_links)
    matching_vectors = {layer.id for layer in project.vector_layers
        if image_id is None and status == "all" and contains(layer.name, layer.path)}
    return LayerMatches(frozenset(matching_images), frozenset(matching_vectors),
                        frozenset(matching_points), frozenset(link_rows))
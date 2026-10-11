import json

import pytest

from geostitch.mapping import ImageMapping
from geostitch.models import ImageRecord, Project
from geostitch.adjustment import AlignmentResult
from geostitch.georeference import GeoreferenceResult
from geostitch.alignment_feedback import AlignmentFeedback
from geostitch.transforms import MatrixTransform
from geostitch.warp import WarpTransform
from test_solver_workflow import solver_window


@pytest.mark.parametrize("frame, crs, mapping", [
    ("layout", None, MatrixTransform.from_similarity((100, 200, 23, 1.2))),
    ("layout", None, MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))),
    ("layout", None, MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))),
    ("native_crs", "EPSG:26910", MatrixTransform.from_similarity((500000, 5400000, 12, 2), pixel_y_down=True)),
])
def test_image_mapping_round_trip_preserves_geometry_and_frames(frame, crs, mapping):
    record = ImageMapping(mapping, frame, crs, source_size=(1000, 800))
    restored = ImageMapping.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record
    assert restored.mapping.map_point(500, 400) == mapping.map_point(500, 400)


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"source_frame": "world"}, {"target_frame": "screen"},
    {"native_crs": "EPSG:3857"}, {"source_size": [0, 10]}, {"source_size": [True, 10]},
    {"source_size": [10]}, {"source_size": "10x10"}, {"mapping": {}},
])
def test_invalid_mapping_metadata_is_rejected(changes):
    saved = ImageMapping(MatrixTransform.identity(), "layout").to_dict()
    saved.update(changes)
    with pytest.raises(ValueError):
        ImageMapping.from_dict(saved)


def test_world_mapping_requires_valid_native_crs():
    for crs in (None, "", "not a CRS"):
        with pytest.raises(ValueError):
            ImageMapping(MatrixTransform.identity(), "native_crs", crs)


def test_world_mapping_requires_valid_matrix_crs():
    with pytest.raises(ValueError, match="Invalid CRS mapping"):
        ImageMapping(
            MatrixTransform.identity(), "native_crs", "EPSG:4326", matrix_crs="not a CRS",
        )


def test_source_dimensions_validate_projective_horizon():
    matrix = MatrixTransform((1, 0, 0, 0, 1, 0, -0.002, 0, 1))
    with pytest.raises(ValueError, match="horizon"):
        ImageMapping(matrix, "layout", source_size=(1000, 800))


def test_project_mapping_format_preserves_all_poses_and_unavailable_states():
    project = Project(crs="EPSG:26910")
    image = project.add_image("image.png")
    image.position_y, image.rotation = 40, 450
    image.world_y = 100
    initial = project.to_dict()
    assert initial["version"] == 26
    assert initial["images"][0]["mappings"]["rest"] == {"pose": [None, 40, 450, 1.0], "transform": None, "intrinsic": None}
    assert Project.from_dict(initial).to_dict() == initial
    image.position_x = 120
    image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale = 200, -40, -450, 2
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 500000, 5400000, 810, 0.5
    saved = project.to_dict()
    restored = Project.from_dict(json.loads(json.dumps(saved)))
    assert restored.to_dict() == saved
    assert restored.images[0].rotation == 450
    assert restored.images[0].world_rotation == 810
    for name in ("rest_mapping", "aligned_mapping", "world_mapping"):
        assert getattr(restored.images[0], name).map_point(20, 30) == getattr(image, name).map_point(20, 30)


def test_native_world_mapping_round_trips_separately_from_active_crs_pose():
    project = Project(crs="EPSG:3857")
    image = project.add_image("image.png")
    image.world_x, image.world_y = 1_000_000, 2_000_000
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((0.2, 0.03, -123, 0.04, -0.2, 45)), "native_crs", "EPSG:4326",
    )

    saved = project.to_dict()
    restored = Project.from_dict(json.loads(json.dumps(saved)))

    assert saved["version"] == 26
    assert restored.images[0].world_mapping == image.world_mapping
    assert restored.images[0].native_world_mapping == image.native_world_mapping
    assert restored.to_dict() == saved


def test_native_world_mapping_round_trips_local_matrix_crs_chain():
    from geostitch.mapping import CRSMapping

    project = Project(crs="EPSG:4326")
    image = project.add_image("local-frame.png")
    image.world_x, image.world_y = 10, 45
    image.source_dimensions = (100, 80)
    matrix = MatrixTransform.from_affine((2, 0.1, 500000, 0.2, -2, 4000000))
    image.native_world_mapping = ImageMapping(
        matrix, "native_crs", project.crs, source_size=image.source_dimensions,
        matrix_crs="EPSG:32610",
    )

    saved = project.to_dict()
    restored = Project.from_dict(json.loads(json.dumps(saved)))
    restored_mapping = restored.images[0].native_world_mapping
    expected = CRSMapping("EPSG:32610", "EPSG:4326").map_point(*matrix.map_point(30, 20))

    assert saved["version"] == 26
    assert restored_mapping == image.native_world_mapping
    assert restored_mapping.mapping_chain.map_point(30, 20) == pytest.approx(expected)


@pytest.mark.parametrize("version", range(18, 23))
def test_older_mapping_projects_seed_native_world_mapping_from_saved_crs(version):
    project = Project(crs="EPSG:26910")
    image = project.add_image("image.png")
    image.world_x, image.world_y = 500_000, 5_400_000
    saved = project.to_dict()
    saved["version"] = version
    saved_image = saved["images"][0]
    saved_image.pop("native_world_mapping")
    expected = ImageMapping(image.world_mapping, "native_crs", project.crs)

    restored = Project.from_dict(saved)

    assert restored.images[0].native_world_mapping == expected


def test_format_23_project_loads_and_migrates_to_format_26():
    project = Project(crs="EPSG:26910")
    image = project.add_image("image.png")
    image.world_x, image.world_y = 500_000, 5_400_000
    image.native_world_mapping = ImageMapping(
        image.world_mapping, "native_crs", project.crs,
    )
    saved = project.to_dict()
    saved["version"] = 23

    restored = Project.from_dict(saved)

    assert restored.images[0].native_world_mapping == image.native_world_mapping
    assert restored.to_dict()["version"] == 26


def test_warped_native_mapping_round_trips_project_format_26():
    project = Project(crs="EPSG:4326")
    image = project.add_image("tps-image.png")
    image.source_dimensions = (10, 10)
    image.world_x, image.world_y = -123.0, 48.0
    controls = ((0, 0), (10, 0), (0, 10), (10, 10))
    base = MatrixTransform.from_affine((100, 0, 500000, 0, -100, 5400000))
    targets = tuple((base.map_point(*point)[0] + point[0] * point[1], base.map_point(*point)[1]) for point in controls)
    warp = WarpTransform.fit(base, controls, targets, smoothing=0.01)
    image.native_world_mapping = ImageMapping(
        warp, "native_crs", project.crs, source_size=image.source_dimensions,
        matrix_crs="EPSG:32610",
    )

    saved = project.to_dict()
    restored = Project.from_dict(json.loads(json.dumps(saved)))
    restored_record = restored.images[0].native_world_mapping

    assert saved["version"] == 26
    assert saved["images"][0]["native_world_mapping"]["version"] == 3
    assert restored_record == image.native_world_mapping
    for source in ((2, 3), (5, 5), (8, 7)):
        assert restored_record.mapping_chain.map_point(*source) == pytest.approx(
            image.native_world_mapping.mapping_chain.map_point(*source), abs=1e-10,
        )
    assert restored.to_dict() == saved


@pytest.mark.parametrize("version", range(1, 18))
def test_legacy_scalar_project_migrates_without_changing_geometry(version):
    image = ImageRecord("image", "image.png", "image", position_x=100, position_y=-40, rotation=450, scale=1.2,
                        world_x=500000, world_y=5400000, world_rotation=-23, world_scale=2)
    legacy_image = vars(image).copy()
    legacy_image.pop("native_world_mapping")
    restored = Project.from_dict({"version": version, "crs": "EPSG:26910", "images": [legacy_image]})
    assert restored.images[0].rest_mapping == image.rest_mapping
    assert restored.images[0].world_mapping == image.world_mapping
    assert restored.images[0].native_world_mapping == ImageMapping(image.world_mapping, "native_crs", "EPSG:26910")
    assert restored.images[0].rotation == 450
    assert restored.to_dict()["version"] == 26


@pytest.mark.parametrize("mutation", ["matrix", "availability", "frame", "crs", "pose", "legacy", "general"])
def test_project_rejects_conflicting_or_unsupported_mapping_data(mutation):
    project = Project()
    image = project.add_image("image.png")
    image.position_x = 0
    image.world_x, image.world_y = 1000, 2000
    data = project.to_dict()
    saved = data["images"][0]
    rest = saved["mappings"]["rest"]
    if mutation == "matrix":
        rest["transform"]["mapping"]["coefficients"][2] += 10
    elif mutation == "availability":
        rest["transform"] = None
    elif mutation == "frame":
        rest["transform"]["source_frame"] = "world"
    elif mutation == "crs":
        saved["mappings"]["world"]["transform"]["native_crs"] = "EPSG:4326"
    elif mutation == "pose":
        rest["pose"][2] = float("nan")
    elif mutation == "legacy":
        saved["rotation"] = 12
    else:
        rest["transform"]["mapping"] = MatrixTransform.from_affine((1, 0.3, 0, 0, 1, 0)).to_dict()
    with pytest.raises(ValueError):
        Project.from_dict(data)


@pytest.mark.parametrize("matrix", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_complete_general_alignment_result_round_trips_without_approximation(matrix):
    project = Project()
    image = project.add_image("image.png")
    result = AlignmentResult(
        {}, {}, 3, False, (image.id,), matrix_transforms={image.id: matrix},
        source_dimensions={image.id: (1000, 800)},
    )
    project.alignment_feedback = AlignmentFeedback.capture(project, result, 0.5)
    saved = project.to_dict()
    assert saved["alignment_feedback"]["version"] == 4
    assert saved["alignment_feedback"]["result"]["transforms"] == {}
    restored = Project.from_dict(json.loads(json.dumps(saved)))
    assert restored.alignment_feedback.result == result
    assert restored.alignment_feedback.result.mappings[image.id] == matrix
    assert restored.alignment_feedback.result.mapping_records[image.id].source_size == (1000, 800)
    assert not restored.alignment_feedback.matches_layout(restored)
    assert restored.to_dict() == saved


@pytest.mark.parametrize("stage", ["georeference"])
def test_incomplete_general_result_application_leaves_project_and_undo_unchanged(solver_window, stage):
    app, window, images, alignment, world, errors = solver_window
    matrix = MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))
    result = (
        AlignmentResult({}, {}, 3, False, (images[0].id,), matrix_transforms={images[0].id: matrix})
        if stage == "alignment" else
        GeoreferenceResult({}, {}, 3, False, window.project.crs, matrix_transforms={images[0].id: matrix})
    )
    before = window.project.to_dict()
    undo_count = window.undo_stack.count()
    with pytest.raises(ValueError, match="complete native mapping"):
        if stage == "alignment":
            window._apply_alignment_result(result)
        else:
            window._apply_georeference_result(result)
    assert window.project.to_dict() == before
    assert window.undo_stack.count() == undo_count


def test_saved_alignment_report_rejects_matrix_pose_disagreement(solver_window):
    app, window, images, alignment, world, errors = solver_window
    saved = AlignmentFeedback.capture(window.project, alignment, 0.5).to_dict()
    saved["result"]["mappings"][images[0].id]["mapping"]["coefficients"][2] += 10
    with pytest.raises(ValueError, match="disagree"):
        AlignmentFeedback.from_dict(saved)


def test_result_copies_input_matrices_and_does_not_expose_mutable_mapping_view():
    matrices = {"image": MatrixTransform.from_affine((1, 0.3, 0, 0, 1, 0))}
    result = AlignmentResult({}, {}, 1, False, (), matrix_transforms=matrices)
    matrices.clear()
    result.mappings.clear()
    assert "image" in result.mappings


def test_world_result_records_declare_native_crs_and_source_pixel_frame():
    matrix = MatrixTransform.from_affine((2, 0.3, 500000, 0.2, -1.5, 5400000))
    result = GeoreferenceResult(
        {}, {}, 1, False, "EPSG:26910", matrix_transforms={"image": matrix},
        source_dimensions={"image": (1000, 800)},
    )
    record = result.mapping_records["image"]
    assert record.source_frame == "source_pixels"
    assert record.target_frame == "native_crs"
    assert record.native_crs == "EPSG:26910"
    assert record.mapping == matrix
    assert record.source_size == (1000, 800)


def test_project_persists_source_geometry_in_image_and_mapping_records():
    project = Project()
    image = project.add_image("image.png")
    image.position_x = 0
    image.source_dimensions = (3000, 2000)
    saved = project.to_dict()
    assert saved["images"][0]["source_dimensions"] == [3000, 2000]
    assert saved["images"][0]["mappings"]["rest"]["transform"]["source_size"] == [3000, 2000]
    assert Project.from_dict(saved).images[0].source_dimensions == (3000, 2000)
    saved["images"][0]["mappings"]["rest"]["transform"]["source_size"] = [2000, 3000]
    with pytest.raises(ValueError, match="dimensions disagree"):
        Project.from_dict(saved)


def test_explicit_save_persists_decoded_source_size_without_background_project_edits(solver_window, tmp_path):
    from PySide6.QtCore import QSize
    from geostitch.project_io import load_project

    app, window, images, alignment, world, errors = solver_window
    window._mark_project_saved()
    window._image_sizes[images[0].id] = QSize(3000, 2000)
    assert images[0].source_dimensions is None
    assert not window._is_project_modified()
    window.project_path = tmp_path / "source-geometry.gsp"
    assert window.save_current_project()
    assert images[0].source_dimensions == (3000, 2000)
    assert load_project(window.project_path).images[0].source_dimensions == (3000, 2000)
    assert not window._is_project_modified()
    window._image_sizes.clear()
    assert window._image_size(images[0]) == QSize(3000, 2000)


def test_failed_save_does_not_commit_source_geometry(solver_window, tmp_path, monkeypatch):
    from PySide6.QtCore import QSize

    app, window, images, alignment, world, errors = solver_window
    window._image_sizes[images[0].id] = QSize(3000, 2000)
    window.project_path = tmp_path / "failed.gsp"

    def fail(*args, **kwargs):
        raise OSError("Disk full")

    monkeypatch.setattr("geostitch.app.save_project", fail)
    assert not window.save_current_project()
    assert images[0].source_dimensions is None
    assert "Disk full" in errors[-1]


def test_redo_to_saved_solver_geometry_restores_clean_document_state(solver_window, tmp_path):
    from PySide6.QtCore import QSize

    app, window, images, alignment, world, errors = solver_window
    window._image_sizes[images[0].id] = QSize(3000, 2000)
    window._apply_georeference_result(world)
    window.project_path = tmp_path / "undo-source-geometry.gsp"
    assert window.save_current_project()
    assert not window._is_project_modified()
    window.undo_stack.undo()
    assert window._is_project_modified()
    window.undo_stack.redo()
    assert window.project.images[0].source_dimensions == (3000, 2000)
    assert not window._is_project_modified()


def test_repair_clears_source_geometry_without_changing_pose_or_observations(solver_window, tmp_path):
    from geostitch.image_repair import apply_replacements

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.source_dimensions = (3000, 2000)
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"replacement")
    pose = (image.position_x, image.position_y, image.rotation, image.scale)
    observations = [(point.id, point.observations[0].x, point.observations[0].y) for point in window.project.control_points]
    before = window.project.to_dict()
    assert apply_replacements(window.project.images, {image.id: replacement}) == [image.id]
    assert image.source_dimensions is None
    assert (image.position_x, image.position_y, image.rotation, image.scale) == pose
    assert [(point.id, point.observations[0].x, point.observations[0].y) for point in window.project.control_points] == observations
    after = window.project.to_dict()
    window._restore_project_snapshot(before)
    assert window.project.images[0].source_dimensions == (3000, 2000)
    window._restore_project_snapshot(after)
    assert window.project.images[0].source_dimensions is None
from __future__ import annotations

import sys
from contextlib import contextmanager
from math import atan2, ceil, cos, degrees, hypot, isfinite, radians, sin, sqrt
from pathlib import Path

from geostitch.adjustment import AlignmentResult, simulate_alignment
from geostitch.georeference import GeoreferenceResult, simulate_georeference
from geostitch.map_view import WorldMapView
from pyproj import CRS, Transformer
from shiboken6 import isValid

from PySide6.QtCore import QElapsedTimer, QEvent, QEventLoop, QItemSelectionModel, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QCursor, QImage, QImageReader, QKeySequence, QPalette, QPixmap, QTransform, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from geostitch.canvas import ImageCanvas
from geostitch.export import FORMATS as EXPORT_FORMATS, export_project_images
from geostitch.icons import app_icon, make_icon
from geostitch.default_layout import DEFAULT_DOCK_STATE, DEFAULT_GEOMETRY
from geostitch.layers import IMAGE_KIND, POINT_KIND, LayerItem, LayerTree
from geostitch.image_view import ImageViewer
from geostitch.masking import masked_pixmap
from geostitch.models import GroundControlPoint, PointConstraint, Project
from geostitch.project_io import load_project, save_project
from geostitch.status_bar import ScrollingStatusBar


class ShortcutSettingsDialog(QDialog):
    def __init__(self, actions: dict[str, QAction], defaults: dict[str, tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(520, 560)
        self._actions = actions
        self._defaults = defaults
        self.editors: dict[str, QKeySequenceEdit] = {}

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(actions), 2, self)
        self.table.setHorizontalHeaderLabels(["Command", "Shortcut"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, self.table.horizontalHeader().ResizeMode.Stretch)
        for row, (key, action) in enumerate(actions.items()):
            self.table.setItem(row, 0, QTableWidgetItem(action.text().replace("&", "")))
            editor = QKeySequenceEdit(action.shortcut(), self.table)
            editor.setClearButtonEnabled(True)
            self.editors[key] = editor
            self.table.setCellWidget(row, 1, editor)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.reset_button = QPushButton("Restore defaults", self)
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        self.reset_button.clicked.connect(self.restore_defaults)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def restore_defaults(self) -> None:
        for key, editor in self.editors.items():
            editor.setKeySequence(QKeySequence(self._defaults[key][1]))

    def shortcut_values(self) -> dict[str, QKeySequence]:
        return {key: editor.keySequence() for key, editor in self.editors.items()}

    def _accept_if_valid(self) -> None:
        conflicts = find_shortcut_conflicts(self.shortcut_values(), self._actions)
        if conflicts:
            QMessageBox.warning(self, "Shortcut conflict", "These shortcuts are assigned more than once:\n" + "\n".join(conflicts))
            return
        self.accept()


def find_shortcut_conflicts(shortcuts: dict[str, QKeySequence], actions: dict[str, QAction]) -> list[str]:
    owners: dict[str, str] = {}
    conflicts = []
    for key, sequence in shortcuts.items():
        normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText).casefold()
        if not normalized:
            continue
        owner = owners.get(normalized)
        if owner is not None:
            conflicts.append(f"{normalized}: {actions[owner].text()} / {actions[key].text()}")
        else:
            owners[normalized] = key
    return conflicts


class PanelDock(QDockWidget):
    """Dock whose drag hint only appears over its title bar."""

    DRAG_HINT = "Drag this panel's title bar to another panel edge to split horizontally or vertically."

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            content = self.widget()
            title_height = content.y() if content is not None else 0
            if 0 < title_height and event.pos().y() < title_height:
                QToolTip.showText(event.globalPos(), self.DRAG_HINT, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)


class ProjectSnapshotCommand(QUndoCommand):
    def __init__(self, window, before: dict, after: dict, label: str):
        super().__init__(label)
        self._window = window
        self._before = before
        self._after = after
        self._first_redo = True

    def undo(self) -> None:
        self._window._restore_project_snapshot(self._before)

    def redo(self) -> None:
        if self._first_redo:
            self._first_redo = False
            return
        self._window._restore_project_snapshot(self._after)


class MainWindow(QMainWindow):
    IMAGE_EXTENSIONS = {
        f".{bytes(image_format).decode('ascii').lower()}"
        for image_format in QImageReader.supportedImageFormats()
    }

    SHORTCUTS = {
        "new_project": ("New project", "Ctrl+N"),
        "open_project": ("Open project", "Ctrl+O"),
        "save_project": ("Save project", "Ctrl+S"),
        "save_project_as": ("Save project as...", "Ctrl+Shift+S"),
        "select_all": ("Select all", "Ctrl+A"),
        "deselect_all": ("Deselect all", "Ctrl+D"),
        "import_images": ("Import images", "Ctrl+I"),
        "export_images": ("Export georeferenced images", "Ctrl+E"),
        "fit_images": ("Fit current view", "Ctrl+Shift+F"),
        "arrange_images": ("Arrange images", "Ctrl+Shift+A"),
        "simulate_alignment": ("Simulate alignment", "Ctrl+Shift+M"),
        "georeference": ("Georeference from GCPs", "Ctrl+Shift+G"),
        "toggle_alignment": ("Toggle aligned / rest layout", "A"),
        "maximize_dock": ("Maximize / restore current panel", "M"),
        "add_points": ("Toggle Add points", "P"),
        "link_points": ("Toggle Link points", "L"),
        "pick_gcp": ("Pick GCP coordinate on map", "G"),
        "update_point": ("Update selected point", ""),
        "delete_point": ("Delete selected", "Delete"),
        "toggle_lock": ("Lock / unlock selected points", "K"),
        "lasso_select": ("Lasso selection (off: rectangle)", "S"),
        "create_constraint": ("Link selected point to...", "Ctrl+Shift+L"),
        "remove_constraint": ("Remove selected link", ""),
        "remove_image": ("Remove selected image", ""),
        "quit_app": ("Exit", "Ctrl+Q"),
        "undo": ("Undo", "Ctrl+Z"),
        "redo": ("Redo", "Ctrl+Y"),
    }

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        application = QApplication.instance()
        if application is not None:
            application.setOrganizationName("GeoStitch")
            application.setApplicationName("GeoStitch")
        self._settings = settings or QSettings()
        self.shortcut_actions: dict[str, QAction] = {}
        self.undo_stack = QUndoStack(self)
        self._active_edit_before: dict | None = None
        self._active_edit_label: str | None = None
        self._edit_depth = 0
        self._alignment_result: AlignmentResult | None = None
        self.project = Project()
        self.project_path: Path | None = None
        self._image_pixmaps: dict[str, QPixmap] = {}
        self._masked_pixmaps: dict[str, tuple[tuple, QPixmap, QPixmap]] = {}
        self.current_image_id: str | None = None
        self._layers_signature: list | None = None
        self.pending_point_id: str | None = None
        self.add_points_mode = False
        self.link_points_mode = False
        self.pending_constraint_point_id: str | None = None
        self.pending_constraint_image_id: str | None = None
        self.selected_point_ids: list[str] = []
        self.selected_constraint_ids: list[str] = []
        self._last_active_dock: QDockWidget | None = None
        self._maximized_dock: QDockWidget | None = None
        self._hidden_docks: list[QDockWidget] = []
        self._layout_before_maximize = None
        self._syncing_selection = False
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)
        self.setStatusBar(ScrollingStatusBar(self))
        self.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.North)
        self.setWindowTitle("GeoStitch | Untitled project")
        self.setWindowIcon(app_icon())
        self.resize(1320, 820)
        self._build_ui()
        self._load_shortcuts()
        self._restore_window_layout()
        self._apply_style()
        self._refresh_all()
        last_project = str(self._settings.value("last_project", "") or "")
        if last_project and Path(last_project).is_file():
            QTimer.singleShot(0, lambda: self._open_project_path(last_project, quiet=True))

    def _build_ui(self) -> None:
        for key, label, callback in (
            ("new_project", "New", self.new_project),
            ("open_project", "Open", self.open_project),
            ("save_project", "Save", self.save_current_project),
            ("save_project_as", "Save as", self.save_project_as),
            ("select_all", "Select all", self.select_all),
            ("deselect_all", "Deselect all", self.deselect_all),
            ("import_images", "Import images", self.import_images),
            ("export_images", "Export georeferenced images", self.export_georeferenced_images),
        ):
            self._register_shortcut_action(key, label, callback)
        fit_action = self._register_shortcut_action("fit_images", "Fit current view", self._fit_canvas)
        fit_action.setToolTip("Fit the panel you are working in: canvas (selected or all images), world map, or image view")
        self._register_shortcut_action("arrange_images", "Arrange images", self.arrange_images)
        self._register_shortcut_action("simulate_alignment", "Simulate alignment", self.simulate_alignment_dialog)
        self._register_shortcut_action("georeference", "Georeference from GCPs", self.simulate_georeference_dialog)
        self.add_points_action = self._register_shortcut_action("add_points", "Add points", None)
        self.add_points_action.setCheckable(True)
        self.add_points_action.setToolTip("Toggle on, then click images to continuously add control points")
        self.add_points_action.toggled.connect(self._set_add_points_mode)
        self.link_points_action = self._register_shortcut_action("link_points", "Link points", None)
        self.link_points_action.setCheckable(True)
        self.link_points_action.setToolTip("Toggle on, then click a point in each of two different images")
        self.link_points_action.toggled.connect(self._set_link_points_mode)
        self.pick_gcp_action = self._register_shortcut_action("pick_gcp", "Pick GCP coordinate on map", None)
        self.pick_gcp_action.setCheckable(True)
        self.pick_gcp_action.setToolTip("Click the map to set geographic coordinates on the selected control point")
        self.pick_gcp_action.toggled.connect(self._set_map_pick_mode)
        self.toggle_alignment_action = self._register_shortcut_action("toggle_alignment", "Aligned layout", None)
        self.toggle_alignment_action.setCheckable(True)
        self.toggle_alignment_action.setEnabled(False)
        self.toggle_alignment_action.toggled.connect(self._on_toggle_alignment)
        self._register_shortcut_action("maximize_dock", "Maximize panel", self.toggle_maximize_dock)
        self._register_shortcut_action("toggle_lock", "Lock / unlock selected points", self.toggle_selected_locks)
        self.lasso_action = self._register_shortcut_action("lasso_select", "Lasso selection", None)
        self.lasso_action.setCheckable(True)
        self.lasso_action.toggled.connect(self._on_lasso_toggled)

        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(12, 14, 8, 12)
        image_title = QLabel("LAYERS")
        image_title.setObjectName("sectionTitle")
        title_row = QHBoxLayout()
        title_row.addWidget(image_title)
        title_row.addStretch(1)
        for text, tooltip, name in (("+", "Expand all layers", "expandAll"), ("\u2212", "Collapse all layers", "collapseAll")):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.setAutoRaise(True)
            button.clicked.connect(lambda _checked=False, method=name: getattr(self.image_list, method)())
            title_row.addWidget(button)
        image_layout.addLayout(title_row)
        self.image_list = LayerTree()
        self.image_list.itemSelectionChanged.connect(self._on_image_selected)
        self.image_list.visibility_toggled.connect(self._on_layer_visibility_toggled)
        self.image_list.layers_reordered.connect(self._on_layers_reordered)
        image_layout.addWidget(self.image_list, 1)
        remove_image_action = self._register_shortcut_action("remove_image", "Remove selected image", self.remove_selected_image)
        remove_image_button = QPushButton(remove_image_action.text())
        remove_image_button.clicked.connect(remove_image_action.trigger)
        image_layout.addWidget(remove_image_button)
        self.image_dock = self._create_panel_dock("imagesDock", "Layers", image_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.image_dock)

        self.canvas = ImageCanvas()
        self.canvas.image_clicked.connect(self._on_canvas_clicked)
        self.canvas.background_clicked.connect(self._on_canvas_background_clicked)
        self.canvas.point_marker_clicked.connect(self._on_constraint_point_clicked)
        self.canvas.point_marker_selected.connect(self._on_canvas_point_selected)
        self.canvas.point_observation_interaction_started.connect(self._begin_point_observation_drag)
        self.canvas.point_observation_changed.connect(self._on_point_observation_changed)
        self.canvas.point_observation_interaction_finished.connect(self._finish_project_edit)
        self.canvas.point_observation_interaction_finished.connect(lambda *_args: self._refresh_image_view())
        self.canvas.point_observation_interaction_finished.connect(lambda *_args: self._refresh_map_points())
        self.canvas.point_marker_missed.connect(self._on_constraint_point_missed)
        self.canvas.files_dropped.connect(self._import_image_paths)
        self.canvas.image_selected.connect(self._on_canvas_image_selected)
        self.canvas.image_toggled.connect(self._on_canvas_image_toggled)
        self.canvas.area_selected.connect(self._on_canvas_area_selected)
        self.canvas.constraint_clicked.connect(self._on_canvas_constraint_clicked)
        self.canvas.constraint_toggled.connect(self._on_canvas_constraint_toggled)
        self.canvas.point_marker_toggled.connect(self._on_canvas_point_toggled)
        self.canvas.transform_changed.connect(self._on_image_transform_changed)
        self.canvas.transform_interaction_started.connect(lambda _image_id: self._begin_project_edit("Transform image"))
        self.canvas.transform_interaction_finished.connect(lambda _image_id: self._finish_project_edit())
        canvas_panel = QWidget()
        canvas_layout = QVBoxLayout(canvas_panel)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_header = QHBoxLayout()
        canvas_header.setContentsMargins(6, 4, 6, 0)
        canvas_header.addWidget(QLabel("Layout"))
        self.alignment_toggle_button = QToolButton()
        self.alignment_toggle_button.setDefaultAction(self.toggle_alignment_action)
        self.alignment_toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        canvas_header.addWidget(self.alignment_toggle_button)
        self.canvas_clip_checkbox = QCheckBox("Show clipping")
        self.canvas_clip_checkbox.setChecked(True)
        self.canvas_clip_checkbox.setToolTip("Hide the parts of images outside their clip polygons; turn off to see full images")
        self.canvas_clip_checkbox.toggled.connect(lambda _checked: self._refresh_canvas())
        canvas_header.addWidget(self.canvas_clip_checkbox)
        canvas_header.addStretch(1)
        canvas_layout.addLayout(canvas_header)
        canvas_layout.addWidget(self.canvas, 1)
        self.canvas_dock = self._create_panel_dock("canvasDock", "Image Canvas", canvas_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.canvas_dock)
        self.splitDockWidget(self.image_dock, self.canvas_dock, Qt.Orientation.Horizontal)

        self.world_map = WorldMapView()
        world_map_panel = QWidget()
        world_map_layout = QVBoxLayout(world_map_panel)
        world_map_layout.setContentsMargins(6, 6, 6, 6)
        map_toolbar = QHBoxLayout()
        self.map_points_checkbox = QCheckBox("Show control points")
        self.map_points_checkbox.setToolTip(
            "Green: has map coordinates. Yellow: placed through its image's world transform. "
            "Square: locked in its image. Circle: unlocked."
        )
        self.map_points_checkbox.toggled.connect(lambda _checked: self._refresh_map_points())
        map_toolbar.addWidget(self.map_points_checkbox)
        map_toolbar.addStretch(1)
        self.map_crs_combo = QComboBox()
        self.map_crs_combo.setEditable(True)
        self.map_crs_combo.setToolTip("Project coordinate system")
        self.map_crs_combo.addItems(["EPSG:4326", "EPSG:3857", "EPSG:32610", "EPSG:32611", "EPSG:26910", "EPSG:26911", "EPSG:2229"])
        self.map_crs_combo.setCurrentText(self.project.crs)
        self.map_crs_combo.activated.connect(self._on_map_crs_chosen)
        self.map_crs_combo.lineEdit().editingFinished.connect(self._on_map_crs_chosen)
        map_toolbar.addWidget(QLabel("CRS"))
        map_toolbar.addWidget(self.map_crs_combo)
        world_map_layout.addLayout(map_toolbar)
        world_map_layout.addWidget(self.world_map, 1)
        self.world_map.coordinate_picked.connect(self._on_world_coordinate_picked)
        self.world_map_dock = self._create_panel_dock("worldMapDock", "World Map", world_map_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.world_map_dock)
        self.splitDockWidget(self.canvas_dock, self.world_map_dock, Qt.Orientation.Vertical)

        self.image_view_dock = self._create_panel_dock("imageViewDock", "Image View", self._build_image_view_panel())
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.image_view_dock)
        self.splitDockWidget(self.world_map_dock, self.image_view_dock, Qt.Orientation.Horizontal)

        self.details_dock = self._create_panel_dock("detailsDock", "Details", self._build_details_panel())
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.details_dock)

        self._build_menus()
        self._build_toolbar()
        QApplication.instance().focusChanged.connect(self._remember_active_dock)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("shortcutHints")
        self.statusBar().addPermanentWidget(self.hint_label)
        for signal in (
            self.image_list.itemSelectionChanged,
            self.add_points_action.toggled,
            self.link_points_action.toggled,
            self.undo_stack.indexChanged,
        ):
            signal.connect(lambda *_args: self._update_hints())
        self.statusBar().showMessage("Import images to begin a project.")

    def _build_details_panel(self) -> QWidget:
        self.details_stack = QStackedWidget()
        for page in (
            self._build_project_page(),
            self._build_image_page(),
            self._build_point_page(),
            self._build_constraint_page(),
            self._build_selection_page(),
        ):
            self.details_stack.addWidget(page)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 14, 12, 12)
        self.details_title = QLabel("PROJECT")
        self.details_title.setObjectName("sectionTitle")
        layout.addWidget(self.details_title)
        layout.addWidget(self.details_stack, 1)
        self.instruction_label = QLabel("Use Add points to click control points onto images, then Link points to join matches.")
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setObjectName("instruction")
        layout.addWidget(self.instruction_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        return scroll

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _info_label() -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _make_list(self, handler, minimum_height: int = 80) -> QListWidget:
        widget = QListWidget()
        widget.setMinimumHeight(minimum_height)
        widget.itemClicked.connect(handler)
        return widget

    def _action_button(self, key: str, text: str, callback, primary: bool = False) -> QPushButton:
        action = self._register_shortcut_action(key, text, callback)
        button = QPushButton(text)
        if primary:
            button.setObjectName("primaryButton")
        button.clicked.connect(action.trigger)
        return button

    def _build_project_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.crs_edit = QLineEdit(self.project.crs)
        self.crs_edit.setPlaceholderText("e.g. EPSG:32610")
        self.crs_edit.editingFinished.connect(self._update_crs)
        self.crs_edit.textChanged.connect(self._sync_map_crs_combo)
        form.addRow("Coordinate system", self.crs_edit)
        layout.addLayout(form)
        self.project_summary_label = self._info_label()
        layout.addWidget(self.project_summary_label)
        layout.addWidget(self._section_label("CONTROL POINTS"))
        self.project_points_list = self._make_list(self._on_detail_point_clicked)
        layout.addWidget(self.project_points_list, 1)
        layout.addWidget(self._section_label("LINKS"))
        self.project_links_list = self._make_list(self._on_detail_link_clicked)
        layout.addWidget(self.project_links_list, 1)
        return page

    def _build_image_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image_info_label = self._info_label()
        layout.addWidget(self.image_info_label)
        layout.addWidget(self._section_label("CONTROL POINTS IN THIS IMAGE"))
        self.image_points_list = self._make_list(self._on_detail_point_clicked)
        layout.addWidget(self.image_points_list, 1)
        layout.addWidget(self._section_label("LINKS"))
        self.image_links_list = self._make_list(self._on_detail_link_clicked)
        layout.addWidget(self.image_links_list, 1)
        return page

    def _build_point_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Optional canvas label")
        self.geo_x_edit = QLineEdit()
        self.geo_x_edit.setPlaceholderText("Optional")
        self.geo_y_edit = QLineEdit()
        self.geo_y_edit.setPlaceholderText("Optional")
        self.pixel_x_edit = QLineEdit()
        self.pixel_x_edit.setPlaceholderText("Point is not placed in an image")
        self.pixel_y_edit = QLineEdit()
        self.pixel_y_edit.setPlaceholderText("Point is not placed in an image")
        self.lock_observation_checkbox = QCheckBox("Lock position in image")
        self.lock_observation_checkbox.setEnabled(False)
        self.lock_observation_checkbox.toggled.connect(self._set_observation_lock)
        form.addRow("Point name", self.name_edit)
        form.addRow("Label", self.label_edit)
        form.addRow("Map X / longitude", self.geo_x_edit)
        form.addRow("Map Y / latitude", self.geo_y_edit)
        form.addRow("Pixel X", self.pixel_x_edit)
        form.addRow("Pixel Y", self.pixel_y_edit)
        form.addRow("", self.lock_observation_checkbox)
        layout.addLayout(form)
        layout.addWidget(self._action_button("update_point", "Update selected point", self.update_selected_point, primary=True))
        layout.addWidget(self._action_button("create_constraint", "Link selected point to...", self.create_constraint))
        layout.addWidget(self._action_button("delete_point", "Delete selected", self.delete_selected))
        layout.addWidget(self._section_label("IMAGE"))
        self.point_observations_list = self._make_list(self._on_detail_observation_clicked)
        layout.addWidget(self.point_observations_list, 1)
        layout.addWidget(self._section_label("LINKS"))
        self.point_links_list = self._make_list(self._on_detail_link_clicked)
        layout.addWidget(self.point_links_list, 1)
        return page

    def _build_selection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selection_info_label = self._info_label()
        layout.addWidget(self.selection_info_label)
        self.selection_list = QListWidget()
        layout.addWidget(self.selection_list, 1)
        return page

    def _build_constraint_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.constraint_first_edit = QComboBox()
        self.constraint_second_edit = QComboBox()
        self.constraint_kind_edit = QLineEdit("tie")
        form.addRow("First point", self.constraint_first_edit)
        form.addRow("Second point", self.constraint_second_edit)
        form.addRow("Constraint type", self.constraint_kind_edit)
        layout.addLayout(form)
        self.constraint_update_button = QPushButton("Update selected constraint")
        self.constraint_update_button.setObjectName("primaryButton")
        self.constraint_update_button.clicked.connect(self.update_selected_constraint)
        layout.addWidget(self.constraint_update_button)
        layout.addWidget(self._action_button("remove_constraint", "Remove link", self.remove_constraint))
        self.constraint_info_label = self._info_label()
        layout.addWidget(self.constraint_info_label)
        layout.addStretch(1)
        return page

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        for key in ("new_project", "open_project", "save_project", "save_project_as"):
            file_menu.addAction(self.shortcut_actions[key])
        file_menu.addSeparator()
        file_menu.addAction(self.shortcut_actions["import_images"])
        file_menu.addAction(self.shortcut_actions["export_images"])
        file_menu.addAction(self.shortcut_actions["remove_image"])
        file_menu.addSeparator()
        file_menu.addAction(self._register_shortcut_action("quit_app", "Exit", self.close))

        edit_menu = self.menuBar().addMenu("Edit")
        undo_action = self._register_shortcut_action("undo", "Undo", self.undo_stack.undo)
        redo_action = self._register_shortcut_action("redo", "Redo", self.undo_stack.redo)
        self.undo_stack.canUndoChanged.connect(undo_action.setEnabled)
        self.undo_stack.canRedoChanged.connect(redo_action.setEnabled)
        self.undo_stack.undoTextChanged.connect(lambda text: undo_action.setText(f"Undo {text}" if text else "Undo"))
        self.undo_stack.redoTextChanged.connect(lambda text: redo_action.setText(f"Redo {text}" if text else "Redo"))
        undo_action.setEnabled(False)
        redo_action.setEnabled(False)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.shortcut_actions["select_all"])
        edit_menu.addAction(self.shortcut_actions["deselect_all"])

        image_menu = self.menuBar().addMenu("Image")
        image_menu.addAction(self.shortcut_actions["arrange_images"])
        image_menu.addAction(self.shortcut_actions["simulate_alignment"])
        image_menu.addAction(self.shortcut_actions["georeference"])

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.shortcut_actions["fit_images"])
        view_menu.addAction(self.shortcut_actions["maximize_dock"])
        view_menu.addSeparator()
        view_menu.addAction(self.image_dock.toggleViewAction())
        view_menu.addAction(self.canvas_dock.toggleViewAction())
        view_menu.addAction(self.details_dock.toggleViewAction())
        view_menu.addAction(self.world_map_dock.toggleViewAction())
        view_menu.addAction(self.image_view_dock.toggleViewAction())
        view_menu.addSeparator()
        reset_layout_action = QAction("Reset panel layout", self)
        reset_layout_action.triggered.connect(self.reset_panel_layout)
        view_menu.addAction(reset_layout_action)

        control_points_menu = self.menuBar().addMenu("Control Points")
        for key in ("add_points", "update_point", "toggle_lock", "delete_point"):
            control_points_menu.addAction(self.shortcut_actions[key])

        constraints_menu = self.menuBar().addMenu("Constraints")
        constraints_menu.addAction(self.shortcut_actions["link_points"])
        constraints_menu.addSeparator()
        constraints_menu.addAction(self.shortcut_actions["create_constraint"])
        constraints_menu.addAction(self.shortcut_actions["remove_constraint"])

        settings_menu = self.menuBar().addMenu("Settings")
        self.shortcut_settings_action = QAction("Keyboard shortcuts...", self)
        self.shortcut_settings_action.triggered.connect(self.open_shortcut_settings)
        settings_menu.addAction(self.shortcut_settings_action)

    TOOLBAR_INFO = {
        "new_project": ("new", "Start an empty project"),
        "open_project": ("open", "Open a saved .gsp project"),
        "save_project": ("save", "Save the project, including canvas and map views"),
        "import_images": ("import", "Add images to the project canvas"),
        "export_images": ("export", "Write every georeferenced image to a GeoTIFF or world-file image"),
        "arrange_images": ("arrange", "Lay out all images in a grid on the canvas"),
        "fit_images": ("fit", "Zoom to the selected images, or all images; fits the map or image view when it is focused or under the cursor"),
        "add_points": ("add_points", "Toggle: click images to add control points continuously"),
        "link_points": ("link_points", "Toggle: click matching points in two images to link them"),
        "pick_gcp": ("pick_gcp", "Toggle: click the world map to set the selected control point's coordinates"),
        "delete_point": ("delete", "Delete the selected images, control points, or link"),
        "toggle_lock": ("lock", "Lock or unlock the position of the selected control points in their images"),
        "lasso_select": ("select", "Drag on the canvas to select items: rectangle, or freehand lasso when toggled on. Ctrl adds, Shift drags over images"),
        "simulate_alignment": ("alignment", "Align constraint-linked images relative to each other"),
        "georeference": ("georeference", "Solve world transforms from control points with geographic coordinates"),
        "undo": ("undo", "Undo the last change"),
        "toggle_alignment": ("aligned", "Switch the canvas between the arranged rest layout and the solver-aligned layout"),
        "redo": ("redo", "Redo the last undone change"),
    }

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main toolbar", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(24, 24))
        groups = (
            ("new_project", "open_project", "save_project"),
            ("import_images", "export_images", "arrange_images", "fit_images"),
            ("lasso_select", "add_points", "link_points", "pick_gcp", "toggle_lock", "delete_point"),
            ("simulate_alignment", "toggle_alignment", "georeference"),
            ("undo", "redo"),
        )
        for index, group in enumerate(groups):
            if index:
                toolbar.addSeparator()
            for key in group:
                action = self.shortcut_actions[key]
                action.setIcon(make_icon(self.TOOLBAR_INFO[key][0]))
                toolbar.addAction(action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.main_toolbar = toolbar

    def _refresh_action_tooltips(self) -> None:
        for key, (_icon, description) in self.TOOLBAR_INFO.items():
            action = self.shortcut_actions[key]
            shortcut = self._shortcut_text(key)
            title = f"{action.text()} ({shortcut})" if shortcut else action.text()
            action.setToolTip(f"{title}\n{description}")

    def _shortcut_text(self, key: str) -> str:
        return self.shortcut_actions[key].shortcut().toString(QKeySequence.SequenceFormat.NativeText)

    def _update_hints(self) -> None:
        if not hasattr(self, "hint_label") or not hasattr(self, "details_stack"):
            return
        project = self.project
        has_images = bool(project.images)
        point = self._selected_point()
        constraint = self._selected_constraint()
        has_image_selection = bool(self._selected_image_ids())
        has_geographic_points = any(
            item.geographic_x is not None and item.geographic_y is not None and item.observations
            for item in project.control_points
        )
        linkable = len(project.control_points) > 1

        enabled = {
            "add_points": has_images or self.add_points_mode,
            "link_points": linkable or self.link_points_mode,
            "simulate_alignment": bool(project.constraints),
            "georeference": has_geographic_points,
            "toggle_alignment": any(item.has_alignment for item in project.images),
            "create_constraint": point is not None and linkable,
            "remove_constraint": bool(self._selected_constraint_ids()),
            "delete_point": bool(self._selected_constraint_ids()) or bool(self._selected_point_ids()) or has_image_selection,
            "toggle_lock": bool(self._selected_point_ids()),
            "pick_gcp": point is not None,
            "update_point": point is not None,
            "remove_image": has_image_selection,
            "arrange_images": has_images,
            "fit_images": has_images,
            "export_images": any(item.world_x is not None and item.world_y is not None for item in project.images),
        }
        for key, value in enabled.items():
            self.shortcut_actions[key].setEnabled(value)
        self.toggle_alignment_action.blockSignals(True)
        self.toggle_alignment_action.setChecked(self._aligned_view_active())
        self.toggle_alignment_action.blockSignals(False)
        if point is None and self.pick_gcp_action.isChecked():
            self.pick_gcp_action.setChecked(False)

        hints: list[tuple[str, str]] = []
        if not has_images:
            hints.append(("import_images", "Import images"))
        else:
            hints.append(("add_points", "Stop adding points" if self.add_points_mode else "Add points"))
            if enabled["link_points"]:
                hints.append(("link_points", "Stop linking" if self.link_points_mode else "Link points"))
            if point is not None:
                hints.append(("pick_gcp", "Stop picking" if self.pick_gcp_action.isChecked() else "Pick map coordinate"))
                if enabled["create_constraint"]:
                    hints.append(("create_constraint", "Link to..."))
                hints.append(("delete_point", "Delete point"))
            elif constraint is not None:
                hints.append(("delete_point", "Remove link"))
            elif has_image_selection:
                hints.append(("fit_images", "Fit image"))
                hints.append(("delete_point", "Remove image"))
            if enabled["simulate_alignment"]:
                hints.append(("simulate_alignment", "Align"))
            if enabled["toggle_alignment"]:
                hints.append(("toggle_alignment", "Show rest layout" if self._aligned_view_active() else "Show aligned layout"))
            if enabled["georeference"]:
                hints.append(("georeference", "Georeference"))
        parts = []
        for key, label in hints:
            shortcut = self._shortcut_text(key)
            if shortcut:
                parts.append(f"[{shortcut}] {label}")
        self.hint_label.setText("   ".join(parts))
        self._refresh_action_tooltips()

    def export_georeferenced_images(self) -> None:
        georeferenced = [image for image in self.project.images if image.world_x is not None and image.world_y is not None]
        if not georeferenced:
            self._show_error("No images are georeferenced yet. Run the georeference solver first.")
            return
        labels = list(EXPORT_FORMATS)
        label, accepted = QInputDialog.getItem(self, "Export georeferenced images", "Format:", labels, 0, False)
        if not accepted:
            return
        directory = QFileDialog.getExistingDirectory(self, "Export to folder", self._last_directory())
        if not directory:
            return
        self._remember_directory(Path(directory) / "_")
        try:
            with self._progress("Exporting images…", 0) as update:
                result = export_project_images(
                    self.project,
                    directory,
                    EXPORT_FORMATS[label],
                    progress=lambda done, total, name: update(
                        done, total, f"Exporting {name} ({done + 1} of {total})" if name else None
                    ),
                )
        except (OSError, ValueError) as error:
            self._show_error(f"Export failed: {error}")
            return
        message = f"Exported {len(result.written)} of {len(georeferenced)} georeferenced images to {directory}."
        self.statusBar().showMessage(message)
        if result.failures:
            QMessageBox.warning(self, "Export", message + "\n\nFailed:\n" + "\n".join(result.failures))

    @contextmanager
    def _progress(self, text: str, maximum: int = 100):
        """Show a status bar progress bar; yields update(value, maximum=None, message=None)."""
        bar = self.statusBar()
        bar.showMessage(text)
        bar.begin_progress(maximum)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        clock = QElapsedTimer()
        clock.start()
        last_paint = [-1000]

        def update(value: float, maximum: int | None = None, message: str | None = None) -> None:
            if message:
                bar.showMessage(message)
            bar.set_progress(int(value), maximum)
            if clock.elapsed() - last_paint[0] >= 40:
                last_paint[0] = clock.elapsed()
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        try:
            update(0)
            yield update
        finally:
            QApplication.restoreOverrideCursor()
            bar.end_progress()

    def _create_panel_dock(self, object_name: str, title: str, content: QWidget) -> QDockWidget:
        dock = PanelDock(title, self)
        dock.setObjectName(object_name)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setWidget(content)
        return dock

    def _restore_window_layout(self) -> None:
        geometry = self._settings.value("window/geometry") or DEFAULT_GEOMETRY
        state = self._settings.value("window/dockState") or DEFAULT_DOCK_STATE
        self.restoreGeometry(geometry)
        self.restoreState(state, 3)

    def reset_panel_layout(self) -> None:
        self._settings.remove("window/dockState")
        self._settings.remove("window/geometry")
        self._restore_docks()
        self.restoreGeometry(DEFAULT_GEOMETRY)
        self.restoreState(DEFAULT_DOCK_STATE, 3)
        self._settings.sync()

    def closeEvent(self, event) -> None:
        self._restore_docks()
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/dockState", self.saveState(3))
        self._settings.sync()
        super().closeEvent(event)

    def _register_shortcut_action(self, key: str, text: str, callback) -> QAction:
        action = QAction(text, self)
        action.setObjectName(key)
        action.setShortcut(QKeySequence(self.SHORTCUTS[key][1]))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        if callback is not None:
            action.triggered.connect(callback)
        self.addAction(action)
        self.shortcut_actions[key] = action
        return action

    def _load_shortcuts(self) -> None:
        shortcuts = {}
        for key, (_label, default) in self.SHORTCUTS.items():
            stored = self._settings.value(f"shortcuts/{key}", default, type=str)
            shortcuts[key] = QKeySequence(stored)
        self._apply_shortcuts(shortcuts, persist=False)

    def _apply_shortcuts(self, shortcuts: dict[str, QKeySequence], persist: bool = True) -> bool:
        conflicts = find_shortcut_conflicts(shortcuts, self.shortcut_actions)
        if conflicts:
            return False
        for key, action in self.shortcut_actions.items():
            sequence = shortcuts.get(key, action.shortcut())
            action.setShortcut(sequence)
            if persist:
                self._settings.setValue(
                    f"shortcuts/{key}",
                    sequence.toString(QKeySequence.SequenceFormat.PortableText),
                )
        if persist:
            self._settings.sync()
        return True

    def open_shortcut_settings(self) -> None:
        dialog = ShortcutSettingsDialog(self.shortcut_actions, self.SHORTCUTS, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_shortcuts(dialog.shortcut_values())
            self._update_hints()

    def _begin_project_edit(self, label: str) -> dict:
        if self._active_edit_before is None:
            self._active_edit_before = self.project.to_dict()
            self._active_edit_label = label
        self._edit_depth += 1
        return self._active_edit_before

    def _finish_project_edit(self) -> None:
        if self._active_edit_before is None:
            return
        self._edit_depth -= 1
        if self._edit_depth > 0:
            return
        before = self._active_edit_before
        label = self._active_edit_label or "Edit project"
        self._active_edit_before = None
        self._active_edit_label = None
        self._edit_depth = 0
        after = self.project.to_dict()
        if before != after:
            self.undo_stack.push(ProjectSnapshotCommand(self, before, after, label))

    def _record_project_edit(self, before: dict, label: str) -> None:
        after = self.project.to_dict()
        if before != after:
            self.undo_stack.push(ProjectSnapshotCommand(self, before, after, label))

    def _restore_project_snapshot(self, snapshot: dict) -> None:
        selected_point_id = self._selected_point_id()
        canvas_state = self.project.canvas_state
        self.project = Project.from_dict(snapshot)
        self.project.canvas_state = canvas_state
        valid_image_ids = {image.id for image in self.project.images}
        self._image_pixmaps = {
            image_id: pixmap for image_id, pixmap in self._image_pixmaps.items()
            if image_id in valid_image_ids
        }
        if self.current_image_id not in valid_image_ids:
            self.current_image_id = self.project.images[0].id if self.project.images else None
        self.pending_point_id = None
        self.pending_constraint_point_id = None
        self.pending_constraint_image_id = None
        self.canvas.set_constraint_source(None)
        self._refresh_images()
        self._refresh_points(selected_point_id if self.project.point_by_id(selected_point_id) else None)
        self._refresh_constraints()
        self.crs_edit.setText(self.project.crs)
        self._refresh_world_map()

    def _apply_style(self) -> None:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#1b2223"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#e8efeb"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#141a1b"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#20292a"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#263232"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f0f5f1"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#e3ebe6"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#303b3c"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f5f1"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#267663"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#899994"))
        application = QApplication.instance()
        if application is not None:
            application.setStyle("Fusion")
            application.setPalette(palette)
            application.setStyleSheet("""
                QWidget { background-color: #1b2223; color: #e8efeb; font-family: 'Segoe UI'; font-size: 10pt; }
                QMainWindow, QDialog, QMessageBox { background-color: #1b2223; color: #e8efeb; }
                QMenuBar { background-color: #141a1b; color: #e8efeb; border-bottom: 1px solid #344140; }
                QMenuBar::item { background-color: transparent; color: #e8efeb; padding: 7px 10px; }
                QMenuBar::item:selected, QMenuBar::item:pressed { background-color: #267663; color: #ffffff; }
                QMenu { background-color: #20292a; color: #e8efeb; border: 1px solid #465553; padding: 4px; }
                QMenu::item { background-color: transparent; color: #e8efeb; padding: 6px 28px 6px 10px; }
                QMenu::item:selected { background-color: #267663; color: #ffffff; }
                QMenu::item:disabled { color: #82908b; }
                QMenu::separator { height: 1px; background-color: #465553; margin: 4px 8px; }
                QLabel#sectionTitle { color: #76cbb0; font-size: 9pt; font-weight: 700; }
                QLabel#instruction { color: #a8b8b1; padding: 5px 0; }
                QListWidget, QTableWidget, QLineEdit, QTreeWidget {
                    background-color: #141a1b; color: #e8efeb;
                    border: 1px solid #465553; border-radius: 3px;
                    selection-background-color: #267663; selection-color: #ffffff;
                }
                QListWidget::item { padding: 7px 5px; }
                QListWidget::item:selected { background-color: #245548; color: #ffffff; }
                QTreeWidget::item { padding: 5px 3px; }
                QTreeWidget::item:selected { background-color: #245548; color: #ffffff; }
                QTableWidget { alternate-background-color: #20292a; gridline-color: #344140; }
                QHeaderView::section { background-color: #293434; color: #dce7e1; border: 0; padding: 6px; font-weight: 600; }
                QPushButton {
                    background-color: #303b3c; color: #f0f5f1;
                    border: 1px solid #52615e; border-radius: 3px; padding: 7px;
                }
                QPushButton:hover { background-color: #3b4b49; border-color: #6c837a; }
                QPushButton:pressed { background-color: #202b2b; }
                QPushButton:disabled { background-color: #252d2e; color: #82908b; border-color: #394342; }
                QPushButton#primaryButton { background-color: #267663; color: #ffffff; border-color: #3b9b80; font-weight: 600; }
                QPushButton#primaryButton:hover { background-color: #318d75; }
                QLineEdit:focus { border: 1px solid #54b89a; }
                QStatusBar { background-color: #141a1b; color: #c7d3cd; }
                QStatusBar::item { border: 0; }
                QScrollBar:vertical { background: #1b2223; width: 12px; margin: 0; }
                QScrollBar::handle:vertical { background: #455451; min-height: 24px; border-radius: 4px; }
                QScrollBar::handle:vertical:hover { background: #61756e; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QToolTip { background-color: #263232; color: #f0f5f1; border: 1px solid #52615e; padding: 4px; }
            """)

    def _refresh_all(self) -> None:
        self._refresh_images()
        self._refresh_points()
        self._refresh_constraints()
        self.crs_edit.setText(self.project.crs)
        self._refresh_world_map()
        self._update_title()

    def _refresh_world_map(self, fit: bool = False) -> None:
        if not hasattr(self, "world_map"):
            return
        world_images = []
        try:
            to_mercator = Transformer.from_crs(
                CRS.from_user_input(self.project.crs),
                CRS.from_epsg(3857),
                always_xy=True,
            )
            to_geographic = Transformer.from_crs(
                CRS.from_user_input(self.project.crs),
                CRS.from_epsg(4326),
                always_xy=True,
            )
        except Exception as error:
            self.statusBar().showMessage(f"Cannot display world map for CRS '{self.project.crs}': {error}")
            self.world_map.set_world_images([])
            self.world_map.set_selected_coordinate(None, None)
            return

        for image in self.project.images:
            pixmap, _error = self._display_pixmap_for_image(image)
            corners = None
            if pixmap and not pixmap.isNull() and image.world_x is not None and image.world_y is not None:
                angle = radians(image.world_rotation)

                def to_world(pixel_x: float, pixel_y: float) -> tuple[float, float]:
                    pixel_y = -pixel_y  # image Y points down, world Y points north
                    return (
                        image.world_x + image.world_scale * (cos(angle) * pixel_x - sin(angle) * pixel_y),
                        image.world_y + image.world_scale * (sin(angle) * pixel_x + cos(angle) * pixel_y),
                    )

                corners = [
                    to_mercator.transform(*to_world(0.0, 0.0)),
                    to_mercator.transform(*to_world(float(pixmap.width()), 0.0)),
                    to_mercator.transform(*to_world(0.0, float(pixmap.height()))),
                ]
            world_images.append({
                "id": image.id,
                "pixmap": pixmap,
                "mercator_corners": corners,
            })
        self.world_map.set_world_images(world_images, fit=fit)
        point = self._selected_point()
        if point is not None and point.geographic_x is not None and point.geographic_y is not None:
            longitude, latitude = to_geographic.transform(point.geographic_x, point.geographic_y)
            self.world_map.set_selected_coordinate(longitude, latitude)
        else:
            self.world_map.set_selected_coordinate(None, None)
        self.world_map_dock.setWindowTitle(f"World Map · {self.project.crs}")
        self._refresh_map_points()

    def _image_world_point(self, image, pixel_x: float, pixel_y: float) -> tuple[float, float]:
        angle = radians(image.world_rotation)
        pixel_y = -pixel_y
        return (
            image.world_x + image.world_scale * (cos(angle) * pixel_x - sin(angle) * pixel_y),
            image.world_y + image.world_scale * (sin(angle) * pixel_x + cos(angle) * pixel_y),
        )

    def _refresh_map_points(self) -> None:
        if not hasattr(self, "world_map") or not hasattr(self, "map_points_checkbox"):
            return
        if not self.map_points_checkbox.isChecked():
            self.world_map.set_control_points([])
            return
        try:
            to_mercator = Transformer.from_crs(
                CRS.from_user_input(self.project.crs), CRS.from_epsg(3857), always_xy=True
            )
        except Exception:
            self.world_map.set_control_points([])
            return
        images = {image.id: image for image in self.project.images}
        selected_id = self._selected_point_id()
        overlay = []
        for point in self.project.control_points:
            observation = point.observations[0] if point.observations else None
            georeferenced = point.geographic_x is not None and point.geographic_y is not None
            if georeferenced:
                world = (point.geographic_x, point.geographic_y)
            else:
                image = images.get(observation.image_id) if observation is not None else None
                if image is None or image.world_x is None or image.world_y is None:
                    continue
                world = self._image_world_point(image, observation.x, observation.y)
            x, y = to_mercator.transform(*world)
            if not (isfinite(x) and isfinite(y)):
                continue
            overlay.append({
                "x": x,
                "y": y,
                "name": point.name,
                "georeferenced": georeferenced,
                "locked": bool(observation and observation.locked),
                "selected": point.id == selected_id,
            })
        self.world_map.set_control_points(overlay)

    def _set_map_pick_mode(self, enabled: bool) -> None:
        if enabled and self._selected_point() is None:
            self.pick_gcp_action.setChecked(False)
            self.statusBar().showMessage("Select a control point before picking its coordinate from the map.")
            return
        self.world_map.set_pick_mode(enabled)
        if enabled:
            self.statusBar().showMessage("Click the OSM map to assign coordinates to the selected control point.")
        else:
            self.statusBar().showMessage("Map coordinate picking is off.")

    def _on_world_coordinate_picked(self, longitude: float, latitude: float) -> None:
        point = self._selected_point()
        if point is None:
            self.pick_gcp_action.setChecked(False)
            self.statusBar().showMessage("Select a control point before picking its coordinate from the map.")
            return
        try:
            transformer = Transformer.from_crs(
                CRS.from_epsg(4326),
                CRS.from_user_input(self.project.crs),
                always_xy=True,
            )
            map_x, map_y = transformer.transform(longitude, latitude)
        except Exception as error:
            self._show_error(f"Could not convert picked map location to {self.project.crs}: {error}")
            return
        before = self.project.to_dict()
        point.geographic_x = map_x
        point.geographic_y = map_y
        self.geo_x_edit.setText(f"{map_x:.8f}")
        self.geo_y_edit.setText(f"{map_y:.8f}")
        self._record_project_edit(before, "Set control point map coordinate")
        self._refresh_points(point.id)
        self._refresh_world_map()
        self.statusBar().showMessage(f"Set '{point.name}' to {map_x:.6f}, {map_y:.6f} ({self.project.crs}).")

    def _refresh_layers(self, force: bool = False) -> None:
        points_by_image: dict[str, list[GroundControlPoint]] = {}
        for point in self.project.control_points:
            if point.image_id:
                points_by_image.setdefault(point.image_id, []).append(point)
        # Top row is the front-most layer, so the tree lists images in reverse z-order.
        images = list(reversed(self.project.images))
        signature = [
            (image.id, image.name, image.visible, [(p.id, p.name, p.visible) for p in points_by_image.get(image.id, [])])
            for image in images
        ]
        if not force and signature == self._layers_signature:
            return
        self._layers_signature = signature
        tree = self.image_list
        expanded = {
            tree.topLevelItem(row).item_id for row in range(tree.topLevelItemCount())
            if tree.topLevelItem(row).isExpanded()
        }
        selected_images = set(self._selected_image_ids())
        tree.blockSignals(True)
        tree.clear()
        for image in images:
            top = LayerItem(IMAGE_KIND, image.id, image.name)
            top.setToolTip(0, image.path)
            self._style_layer_item(top, image.visible, image.visible)
            for point in points_by_image.get(image.id, []):
                child = LayerItem(POINT_KIND, point.id, point.name)
                self._style_layer_item(child, point.visible, point.visible and image.visible)
                top.addChild(child)
            tree.addTopLevelItem(top)
            top.setExpanded(image.id in expanded)
            top.setSelected(image.id in selected_images)
        tree.blockSignals(False)
        self._sync_tree_points()

    @staticmethod
    def _style_layer_item(item: LayerItem, visible: bool, shown: bool) -> None:
        item.setIcon(LayerTree.EYE_COLUMN, make_icon("eye" if visible else "eye_off"))
        item.setToolTip(LayerTree.EYE_COLUMN, "Hide on canvas" if visible else "Show on canvas")
        if not shown:
            item.setForeground(0, QBrush(QColor("#6f7c78")))

    def _sync_tree_points(self) -> None:
        chosen = set(self._selected_point_ids())
        tree = self.image_list
        tree.blockSignals(True)
        for row in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(row)
            for index in range(top.childCount()):
                child = top.child(index)
                selected = child.item_id in chosen
                child.setSelected(selected)
                if selected:
                    top.setExpanded(True)
        tree.blockSignals(False)

    def _on_layer_visibility_toggled(self, item: LayerItem) -> None:
        if item.kind == IMAGE_KIND:
            target = next((image for image in self.project.images if image.id == item.item_id), None)
        else:
            target = self.project.point_by_id(item.item_id)
        if target is None:
            return
        before = self.project.to_dict()
        target.visible = not target.visible
        self._record_project_edit(before, "Show layer" if target.visible else "Hide layer")
        self._refresh_canvas()
        self._refresh_details()

    def _on_layers_reordered(self, top_to_bottom: list) -> None:
        by_id = {image.id: image for image in self.project.images}
        ordered = [by_id[image_id] for image_id in reversed(top_to_bottom) if image_id in by_id]
        if len(ordered) != len(self.project.images):
            return
        before = self.project.to_dict()
        self.project.images = ordered
        self._record_project_edit(before, "Reorder layers")
        self._refresh_images()
        self.statusBar().showMessage("Layer order updated. Top layers are drawn in front.")

    def _refresh_images(self) -> None:
        self._refresh_layers(force=True)
        if self.current_image_id not in {image.id for image in self.project.images}:
            self.current_image_id = self.project.images[0].id if self.project.images else None
        if not self._selected_image_ids() and self.current_image_id is not None:
            current = next(
                (self.image_list.item(row) for row in range(self.image_list.count())
                 if self.image_list.item(row).item_id == self.current_image_id),
                None,
            )
            self.image_list.blockSignals(True)
            self.image_list.setCurrentItem(current)
            self.image_list.blockSignals(False)
        self._refresh_canvas()
        self._refresh_details()

    def _refresh_points(self, selected_id: str | None = None) -> None:
        if selected_id is not None and self.project.point_by_id(selected_id) is not None:
            self.selected_point_id = selected_id
            self.selected_constraint_id = None
            self._clear_image_selection(keep_context=True)
        self._refresh_canvas()
        self._refresh_constraints()

    @property
    def selected_point_id(self) -> str | None:
        return self.selected_point_ids[0] if len(self.selected_point_ids) == 1 else None

    @selected_point_id.setter
    def selected_point_id(self, point_id: str | None) -> None:
        self.selected_point_ids = [point_id] if point_id else []

    def _selected_point_ids(self) -> list[str]:
        return [point_id for point_id in self.selected_point_ids if self.project.point_by_id(point_id) is not None]

    def _selected_image_ids(self) -> list[str]:
        if not isValid(self.image_list):
            return []
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.image_list.selectedItems()]

    def _selected_point_id(self) -> str | None:
        point_ids = self._selected_point_ids()
        return point_ids[0] if len(point_ids) == 1 else None

    def _selected_point(self) -> GroundControlPoint | None:
        point_id = self._selected_point_id()
        return self.project.point_by_id(point_id) if point_id else None

    def _on_image_selected(self) -> None:
        if self._syncing_selection or not isValid(self.image_list):
            return
        image_ids = self._selected_image_ids()
        tree_points = [item.item_id for item in self.image_list.selected_point_items()]
        current = self.image_list.currentItem()
        if current is not None and current.isSelected() and current.kind == IMAGE_KIND:
            self.current_image_id = current.item_id
        elif image_ids:
            self.current_image_id = image_ids[-1]
        elif len(tree_points) == 1:
            self.current_image_id = self.project.point_by_id(tree_points[0]).image_id or self.current_image_id
        else:
            self.current_image_id = None
        additive = bool(QApplication.keyboardModifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ))
        if (image_ids or tree_points) and not additive:
            self._clear_constraint_selection()
        if tree_points != self.selected_point_ids:
            self.selected_point_ids = tree_points
            self.pending_point_id = None
        self._refresh_points()

    def _clear_image_selection(self, keep_context: bool = False) -> None:
        self._syncing_selection = True
        self.image_list.blockSignals(True)
        self.image_list.clearSelection()
        self.image_list.setCurrentItem(None)
        self.image_list.blockSignals(False)
        self._syncing_selection = False
        if not keep_context:
            self.current_image_id = None
        self.canvas.set_active_image(None)
        if self.current_image_id:
            self.statusBar().showMessage("Drag images to move them. Use corner handles to scale and the top handle to rotate.")

    def _on_canvas_image_selected(self, image_id: str) -> None:
        self.current_image_id = image_id
        self._clear_point_selection()
        self._clear_constraint_selection()
        self.image_list.blockSignals(True)
        self.image_list.clearSelection()
        for row in range(self.image_list.count()):
            item = self.image_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == image_id:
                self.image_list.setCurrentItem(item)
                item.setSelected(True)
                break
        self.image_list.blockSignals(False)
        self._refresh_points()
        self.statusBar().showMessage("Drag images to move them. Use corner handles to scale and the top handle to rotate.")

    def _on_lasso_toggled(self, enabled: bool) -> None:
        self.canvas.set_selection_mode("lasso" if enabled else "rect")
        self.lasso_action.setIcon(make_icon("lasso" if enabled else "select"))
        self.statusBar().showMessage(
            "Lasso selection: drag a freehand outline on the canvas." if enabled
            else "Rectangle selection: drag a box on the canvas."
        )

    def _on_canvas_area_selected(self, image_ids: list, point_keys: list, constraint_indices: list, additive: bool) -> None:
        if self.add_points_mode or self.link_points_mode:
            return
        point_ids = [point_id for _image_id, point_id in point_keys]
        link_ids = [
            self.project.constraints[index].id for index in constraint_indices
            if 0 <= index < len(self.project.constraints)
        ]
        if additive:
            image_ids = [*self._selected_image_ids(), *(item for item in image_ids if item not in self._selected_image_ids())]
            point_ids = [*self._selected_point_ids(), *(item for item in point_ids if item not in self.selected_point_ids)]
            link_ids = [*self._selected_constraint_ids(), *(item for item in link_ids if item not in self.selected_constraint_ids)]
        if not (image_ids or point_ids or link_ids):
            self._on_canvas_background_clicked()
            return
        chosen_images = set(image_ids)
        self.image_list.blockSignals(True)
        self.image_list.clearSelection()
        last_item = None
        for row in range(self.image_list.count()):
            item = self.image_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in chosen_images:
                item.setSelected(True)
                last_item = item
        # NoUpdate keeps the multi-selection; a plain setCurrentItem would collapse it to one row.
        self.image_list.setCurrentItem(last_item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.image_list.blockSignals(False)
        if last_item is not None:
            self.current_image_id = last_item.data(Qt.ItemDataRole.UserRole)
        self.selected_point_ids = point_ids
        self.selected_constraint_ids = link_ids
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        if len(point_ids) == 1 and not image_ids:
            self.current_image_id = self.project.point_by_id(point_ids[0]).image_id or self.current_image_id
        self._refresh_points()
        self.statusBar().showMessage(
            f"Selected {len(image_ids)} image(s), {len(point_ids)} point(s), {len(link_ids)} link(s)."
        )

    def _on_canvas_image_toggled(self, image_id: str) -> None:
        self.image_list.blockSignals(True)
        for row in range(self.image_list.count()):
            item = self.image_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == image_id:
                item.setSelected(not item.isSelected())
                break
        self.image_list.blockSignals(False)
        remaining = self._selected_image_ids()
        self.current_image_id = image_id if image_id in remaining else (remaining[-1] if remaining else None)
        self._refresh_points()
        self.statusBar().showMessage(f"{len(remaining)} image(s) selected. Ctrl+click to add or remove images.")

    def _on_canvas_point_toggled(self, image_id: str, point_id: str) -> None:
        point = self.project.point_by_id(point_id)
        if point is None or point.observation_for(image_id) is None:
            return
        point_ids = self._selected_point_ids()
        if point_id in point_ids:
            point_ids.remove(point_id)
        else:
            point_ids.append(point_id)
        self.selected_point_ids = point_ids
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        self.canvas.set_selected_points(set(point_ids))
        if len(point_ids) == 1:
            self.current_image_id = self.project.point_by_id(point_ids[0]).image_id or self.current_image_id
        self._refresh_details()
        self.statusBar().showMessage(f"{len(point_ids)} point(s) selected. Ctrl+click to add or remove points.")

    def _clear_point_selection(self) -> None:
        self.selected_point_id = None
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        self.canvas.set_selected_points(set())

    def _clear_constraint_selection(self) -> None:
        self.selected_constraint_id = None

    @property
    def selected_constraint_id(self) -> str | None:
        return self.selected_constraint_ids[0] if len(self.selected_constraint_ids) == 1 else None

    @selected_constraint_id.setter
    def selected_constraint_id(self, constraint_id: str | None) -> None:
        self.selected_constraint_ids = [constraint_id] if constraint_id else []

    def _selected_constraint_ids(self) -> list[str]:
        existing = {item.id for item in self.project.constraints}
        return [constraint_id for constraint_id in self.selected_constraint_ids if constraint_id in existing]

    def _on_canvas_constraint_toggled(self, index: int) -> None:
        if not 0 <= index < len(self.project.constraints):
            return
        constraint_id = self.project.constraints[index].id
        constraint_ids = self._selected_constraint_ids()
        if constraint_id in constraint_ids:
            constraint_ids.remove(constraint_id)
        else:
            constraint_ids.append(constraint_id)
        self.selected_constraint_ids = constraint_ids
        self._refresh_details()
        self.statusBar().showMessage(f"{len(constraint_ids)} link(s) selected. Ctrl+click to add or remove links.")

    def _on_canvas_constraint_clicked(self, index: int) -> None:
        if 0 <= index < len(self.project.constraints):
            self._select_constraint(self.project.constraints[index].id)

    def _select_constraint(self, constraint_id: str) -> None:
        if not any(item.id == constraint_id for item in self.project.constraints):
            return
        self._clear_image_selection(keep_context=True)
        self._clear_point_selection()
        self.selected_constraint_id = constraint_id
        self._refresh_details()

    def _on_canvas_background_clicked(self) -> None:
        if self.add_points_mode or self.link_points_mode:
            return
        self.deselect_all()

    def select_all(self) -> None:
        if self.add_points_mode or self.link_points_mode:
            return
        shown = {image.id for image in self.project.images if image.visible}
        point_keys = [
            (point.image_id, point.id) for point in self.project.control_points
            if point.visible and point.image_id in shown
        ]
        visible_points = {point_id for _image_id, point_id in point_keys}
        link_indices = [
            index for index, item in enumerate(self.project.constraints)
            if item.first_point_id in visible_points and item.second_point_id in visible_points
        ]
        self._on_canvas_area_selected(
            [image.id for image in self.project.images if image.id in shown], point_keys, link_indices, False
        )

    def deselect_all(self) -> None:
        self.image_list.blockSignals(True)
        self.image_list.clearSelection()
        self.image_list.setCurrentItem(None)
        self.image_list.blockSignals(False)
        self.current_image_id = None
        self.canvas.set_active_image(None)
        self._clear_point_selection()
        self._clear_constraint_selection()
        self._refresh_details()
        self.statusBar().showMessage("Selection cleared.")

    def _on_image_transform_changed(self, image_id: str, x: float, y: float, rotation: float, scale: float) -> None:
        if self._aligned_view_active():
            return
        image = next((item for item in self.project.images if item.id == image_id), None)
        if image is None:
            return
        image.position_x = x
        image.position_y = y
        image.rotation = rotation
        image.scale = scale
        if self._details_image() is image:
            self.image_info_label.setText(self._image_info_text(image))

    def _aligned_view_active(self) -> bool:
        return self.project.canvas_state == "aligned" and any(image.has_alignment for image in self.project.images)

    def _canvas_pose(self, image) -> tuple[float | None, float, float, float]:
        if self._aligned_view_active() and image.has_alignment:
            return image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale
        return image.position_x, image.position_y, image.rotation, image.scale

    def _set_canvas_state(self, state: str) -> None:
        if state == "aligned" and not any(image.has_alignment for image in self.project.images):
            state = "rest"
        self.project.canvas_state = state
        self._refresh_canvas()
        self._refresh_details()
        self.statusBar().showMessage(
            "Showing the solver-aligned layout (read-only). Toggle back to arrange images."
            if state == "aligned" else "Showing the arranged rest layout."
        )

    def _on_toggle_alignment(self, checked: bool) -> None:
        wanted = "aligned" if checked else "rest"
        if self.project.canvas_state != wanted:
            self._set_canvas_state(wanted)

    def _refresh_canvas(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self._refresh_layers()
        layers = []
        load_errors = []
        for image in self.project.images:
            show_clip = not hasattr(self, "canvas_clip_checkbox") or self.canvas_clip_checkbox.isChecked()
            pixmap, error = (
                self._display_pixmap_for_image(image) if show_clip else self._pixmap_for_image(image)
            )
            if pixmap.isNull():
                load_errors.append(f"{image.name}: {error or 'file not found'}")
            points = []
            for point in self.project.control_points:
                observation = point.observation_for(image.id)
                if observation:
                    points.append((point.id, observation.x, observation.y, observation.locked, point.visible and image.visible))
            pose_x, pose_y, pose_rotation, pose_scale = self._canvas_pose(image)
            layers.append({
                "id": image.id,
                "visible": image.visible,
                "pixmap": pixmap,
                "position_x": pose_x,
                "position_y": pose_y,
                "rotation": pose_rotation,
                "scale": pose_scale,
                "points": points,
            })
        constraints = [(item.first_point_id, item.second_point_id) for item in self.project.constraints]
        self.canvas.set_read_only(self._aligned_view_active())
        self.canvas.set_layers(layers, constraints)
        selected_image_ids = self._selected_image_ids()
        self.canvas.set_selected_images(set(selected_image_ids))
        active_point = "__continuous_add__" if self.add_points_mode else self.pending_point_id
        self.canvas.set_active_point(active_point)
        self.canvas.set_constraint_mode(self.link_points_mode)
        self.canvas.set_constraint_source(self.pending_constraint_point_id)
        self.canvas.set_selected_points(set(self._selected_point_ids()))
        if load_errors:
            shown_errors = "; ".join(load_errors[:3])
            remaining = len(load_errors) - 3
            suffix = f"; and {remaining} more" if remaining > 0 else ""
            self.statusBar().showMessage(f"Could not display image(s): {shown_errors}{suffix}")

    @staticmethod
    def _read_image_pixmap(path: str | Path) -> tuple[QPixmap, str]:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        reader.setDecideFormatFromContent(True)
        image = reader.read()
        if not image.isNull():
            return QPixmap.fromImage(image), ""

        qt_error = reader.errorString()
        try:
            return MainWindow._read_image_with_pillow(path), ""
        except Exception as error:
            return QPixmap(), f"{qt_error}; Pillow fallback: {error}"

    @staticmethod
    def _read_image_with_pillow(path: str | Path) -> QPixmap:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            oriented = ImageOps.exif_transpose(source)
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            converted = oriented.convert("RGBA" if has_alpha else "RGB")
            image_format = QImage.Format.Format_RGBA8888 if has_alpha else QImage.Format.Format_RGB888
            bytes_per_pixel = 4 if has_alpha else 3
            image = QImage(
                converted.tobytes(),
                converted.width,
                converted.height,
                converted.width * bytes_per_pixel,
                image_format,
            ).copy()
        return QPixmap.fromImage(image)

    def _pixmap_for_image(self, image) -> tuple[QPixmap, str]:
        cached = self._image_pixmaps.get(image.id)
        if cached is not None:
            return cached, ""
        pixmap, error = self._read_image_pixmap(image.path)
        if not pixmap.isNull():
            self._image_pixmaps[image.id] = pixmap
        return pixmap, error

    def _refresh_constraints(self) -> None:
        if not hasattr(self, "details_stack"):
            return
        self.selected_constraint_ids = self._selected_constraint_ids()
        self.canvas.set_constraints([(item.first_point_id, item.second_point_id) for item in self.project.constraints])
        self._refresh_details()

    def _selected_constraint(self) -> PointConstraint | None:
        constraint_id = self.selected_constraint_id
        if not constraint_id:
            return None
        return next((constraint for constraint in self.project.constraints if constraint.id == constraint_id), None)

    def _details_image(self):
        image_ids = self._selected_image_ids()
        if len(image_ids) != 1:
            return None
        return next((image for image in self.project.images if image.id == image_ids[0]), None)

    def _refresh_details(self) -> None:
        if not hasattr(self, "details_stack") or not hasattr(self, "image_list"):
            return
        constraint = self._selected_constraint()
        constraint_ids = self._selected_constraint_ids()
        point = self._selected_point()
        point_ids = self._selected_point_ids()
        image_ids = self._selected_image_ids()
        image = self._details_image()
        chosen_constraints = set(constraint_ids)
        self.canvas.set_selected_constraints({
            index for index, item in enumerate(self.project.constraints) if item.id in chosen_constraints
        })
        self._sync_tree_points()
        total = len(image_ids) + len(point_ids) + len(constraint_ids)
        kinds = sum(bool(ids) for ids in (image_ids, point_ids, constraint_ids))
        if total > 1:
            titles = {0: f"{len(image_ids)} IMAGES", 1: f"{len(point_ids)} CONTROL POINTS", 2: f"{len(constraint_ids)} LINKS"}
            only_kind = next(index for index, ids in enumerate((image_ids, point_ids, constraint_ids)) if ids) if kinds == 1 else None
            self.details_title.setText(titles[only_kind] if only_kind is not None else f"{total} ITEMS SELECTED")
            self.details_stack.setCurrentIndex(4)
            self._show_multi_selection_details(image_ids, point_ids, constraint_ids)
        elif constraint is not None:
            self.details_title.setText("LINK")
            self.details_stack.setCurrentIndex(3)
            self._show_constraint_details(constraint)
        elif point is not None:
            self.details_title.setText(f"CONTROL POINT · {point.name}")
            self.details_stack.setCurrentIndex(2)
            self._show_point_details(point)
        elif image is not None:
            self.details_title.setText(f"IMAGE · {image.name}")
            self.details_stack.setCurrentIndex(1)
            self._show_image_details(image)
        else:
            self.details_title.setText("PROJECT")
            self.details_stack.setCurrentIndex(0)
            self._show_project_details()
        self._refresh_image_view()
        self._refresh_map_points()
        self._update_hints()

    def _build_image_view_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.mask_draw_button = QPushButton("Draw clip polygon")
        self.mask_draw_button.setCheckable(True)
        self.mask_draw_button.setToolTip(
            "Click to add vertices; double-click, Enter or click the first vertex to finish. "
            "Right-click or Backspace removes the last vertex, Esc cancels."
        )
        self.mask_draw_button.toggled.connect(self._on_mask_draw_toggled)
        self.mask_clear_button = QPushButton("Clear polygon")
        self.mask_clear_button.clicked.connect(self._clear_mask)
        self.mask_mode_combo = QComboBox()
        self.mask_mode_combo.addItem("Show inside polygon", "inside")
        self.mask_mode_combo.addItem("Show outside polygon", "outside")
        self.mask_mode_combo.currentIndexChanged.connect(self._on_mask_mode_changed)
        self.mask_enable_checkbox = QCheckBox("Apply clip")
        self.mask_enable_checkbox.setToolTip("Hide the clipped part wherever this image is shown or exported")
        self.mask_enable_checkbox.toggled.connect(self._on_mask_enabled_toggled)
        fit_button = QPushButton("Fit")
        fit_button.clicked.connect(lambda: self.image_viewer.fit_image())
        for widget in (self.mask_draw_button, self.mask_clear_button, self.mask_mode_combo, self.mask_enable_checkbox):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(fit_button)
        layout.addLayout(row)
        self.image_viewer = ImageViewer()
        self.image_viewer.polygon_drawn.connect(self._on_mask_polygon_drawn)
        self.image_viewer.point_clicked.connect(self._select_point)
        layout.addWidget(self.image_viewer, 1)
        return panel

    def _display_pixmap_for_image(self, image) -> tuple[QPixmap, str]:
        """Pixmap with the image's clip polygon applied when it is enabled."""
        pixmap, error = self._pixmap_for_image(image)
        if pixmap.isNull() or not image.mask_active:
            return pixmap, error
        key = (tuple(tuple(point) for point in image.mask_polygon), image.mask_mode)
        cached = self._masked_pixmaps.get(image.id)
        if cached is not None and cached[0] == key and cached[1] is pixmap:
            return cached[2], error
        masked = masked_pixmap(pixmap, image.mask_polygon, image.mask_mode)
        self._masked_pixmaps[image.id] = (key, pixmap, masked)
        return masked, error

    def _current_image_record(self):
        """The image shown in the preview: only when a single image (or a single point on it) is selected."""
        if self._selected_constraint_ids():
            return None
        image_ids = self._selected_image_ids()
        point_ids = self._selected_point_ids()
        if len(image_ids) == 1 and not point_ids:
            image_id = image_ids[0]
        elif len(point_ids) == 1 and not image_ids:
            image_id = self.current_image_id
        else:
            return None
        return next((item for item in self.project.images if item.id == image_id), None)

    def _refresh_image_view(self) -> None:
        if not hasattr(self, "image_viewer"):
            return
        image = self._current_image_record()
        pixmap = self._pixmap_for_image(image)[0] if image is not None else None
        points = []
        if image is not None:
            for point in self.project.control_points:
                observation = point.observation_for(image.id)
                if observation is not None:
                    points.append((point.id, point.name, observation.x, observation.y))
        self.image_viewer.set_image(
            image.id if image is not None else None,
            pixmap,
            points,
            image.mask_polygon if image is not None else [],
            image.mask_mode if image is not None else "inside",
            image.mask_enabled if image is not None else False,
            self._selected_point_id(),
        )
        has_image = image is not None and pixmap is not None and not pixmap.isNull()
        widgets = (self.mask_mode_combo, self.mask_enable_checkbox, self.mask_clear_button, self.mask_draw_button)
        for widget in widgets:
            widget.blockSignals(True)
        self.mask_mode_combo.setCurrentIndex(max(0, self.mask_mode_combo.findData(image.mask_mode)) if image else 0)
        self.mask_enable_checkbox.setChecked(bool(image and image.mask_enabled))
        if not has_image:
            self.mask_draw_button.setChecked(False)
        for widget in widgets:
            widget.blockSignals(False)
        self.mask_draw_button.setEnabled(has_image)
        self.mask_mode_combo.setEnabled(has_image)
        self.mask_enable_checkbox.setEnabled(has_image and len(image.mask_polygon) >= 3)
        self.mask_clear_button.setEnabled(has_image and bool(image.mask_polygon))
        self.image_view_dock.setWindowTitle(f"Image View · {image.name}" if image is not None else "Image View")

    def _refresh_mask_displays(self) -> None:
        self._refresh_canvas()
        self._refresh_world_map()
        self._refresh_image_view()

    def _on_mask_draw_toggled(self, enabled: bool) -> None:
        self.image_viewer.set_drawing(enabled)
        if enabled:
            self.statusBar().showMessage("Click the image to outline the clip polygon. Double-click or press Enter to finish.")

    def _on_mask_polygon_drawn(self, polygon: list) -> None:
        self.mask_draw_button.setChecked(False)
        image = self._current_image_record()
        if image is None:
            return
        before = self.project.to_dict()
        image.mask_polygon = [[float(x), float(y)] for x, y in polygon]
        image.mask_enabled = True
        self._record_project_edit(before, "Define clip polygon")
        self._refresh_mask_displays()
        self.statusBar().showMessage(f"Clip polygon set for {image.name}.")

    def _clear_mask(self) -> None:
        image = self._current_image_record()
        if image is None or not image.mask_polygon:
            return
        before = self.project.to_dict()
        image.mask_polygon = []
        image.mask_enabled = False
        self._record_project_edit(before, "Clear clip polygon")
        self._refresh_mask_displays()

    def _on_mask_mode_changed(self, _index: int) -> None:
        image = self._current_image_record()
        mode = self.mask_mode_combo.currentData()
        if image is None or image.mask_mode == mode:
            return
        before = self.project.to_dict()
        image.mask_mode = mode
        self._record_project_edit(before, "Change clip mode")
        self._refresh_mask_displays()

    def _on_mask_enabled_toggled(self, enabled: bool) -> None:
        image = self._current_image_record()
        if image is None or image.mask_enabled == enabled:
            return
        before = self.project.to_dict()
        image.mask_enabled = enabled
        self._record_project_edit(before, "Toggle clip polygon")
        self._refresh_mask_displays()

    @staticmethod
    def _link_text(constraint: PointConstraint, point_names: dict[str, str]) -> str:
        first = point_names.get(constraint.first_point_id, "?")
        second = point_names.get(constraint.second_point_id, "?")
        return f"{first}  ↔  {second}  ·  {constraint.kind}"

    def _fill_point_list(self, widget: QListWidget, points: list[GroundControlPoint], image_id: str | None = None) -> None:
        widget.clear()
        for point in points:
            parts = [point.name]
            if point.label:
                parts.append(f'"{point.label}"')
            observation = point.observation_for(image_id) if image_id else None
            if observation is not None:
                parts.append(f"pixel ({observation.x:g}, {observation.y:g})")
            if point.geographic_x is not None and point.geographic_y is not None:
                parts.append(f"map ({point.geographic_x:g}, {point.geographic_y:g})")
            item = QListWidgetItem("  ·  ".join(parts))
            item.setData(Qt.ItemDataRole.UserRole, point.id)
            widget.addItem(item)

    def _fill_link_list(self, widget: QListWidget, constraints: list[PointConstraint]) -> None:
        widget.clear()
        point_names = {point.id: point.name for point in self.project.control_points}
        for constraint in constraints:
            item = QListWidgetItem(self._link_text(constraint, point_names))
            item.setData(Qt.ItemDataRole.UserRole, constraint.id)
            widget.addItem(item)

    def _show_project_details(self) -> None:
        project = self.project
        georeferenced = sum(image.world_x is not None for image in project.images)
        self.project_summary_label.setText(
            f"{len(project.images)} images ({georeferenced} georeferenced)  ·  "
            f"{len(project.control_points)} control points  ·  {len(project.constraints)} links"
        )
        self._fill_point_list(self.project_points_list, project.control_points)
        self._fill_link_list(self.project_links_list, project.constraints)

    def _image_info_text(self, image) -> str:
        pixmap = self._image_pixmaps.get(image.id)
        size = f"{pixmap.width()} x {pixmap.height()} px" if pixmap is not None and not pixmap.isNull() else "unknown size"
        pose_x, pose_y, pose_rotation, pose_scale = self._canvas_pose(image)
        lines = [
            image.path,
            size,
            f"Canvas ({'aligned' if self._aligned_view_active() and image.has_alignment else 'rest'}): "
            f"position ({pose_x or 0.0:.1f}, {pose_y:.1f}), "
            f"rotation {pose_rotation:.2f}°, scale {pose_scale:.4f}",
        ]
        if image.world_x is not None and image.world_y is not None:
            lines.append(
                f"World ({self.project.crs}): origin ({image.world_x:.4f}, {image.world_y:.4f}), "
                f"rotation {image.world_rotation:.3f}°, scale {image.world_scale:.6g}"
            )
        else:
            lines.append("Not georeferenced yet")
        return "\n".join(lines)

    def _show_multi_selection_details(
        self, image_ids: list[str], point_ids: list[str], constraint_ids: list[str]
    ) -> None:
        chosen_images, chosen_points, chosen_links = set(image_ids), set(point_ids), set(constraint_ids)
        images = [item for item in self.project.images if item.id in chosen_images]
        points = [item for item in self.project.control_points if item.id in chosen_points]
        links = [item for item in self.project.constraints if item.id in chosen_links]
        summary = []
        if images:
            georeferenced = sum(image.world_x is not None for image in images)
            summary.append(f"{len(images)} images ({georeferenced} georeferenced)")
        if points:
            mapped = sum(point.geographic_x is not None and point.geographic_y is not None for point in points)
            summary.append(f"{len(points)} control points ({mapped} with map coordinates)")
        if links:
            summary.append(f"{len(links)} links")
        self.selection_info_label.setText("  ·  ".join(summary))
        image_names = {image.id: image.name for image in self.project.images}
        point_names = {point.id: point.name for point in self.project.control_points}
        self.selection_list.clear()
        for image in images:
            self.selection_list.addItem(f"Image  ·  {image.name}")
        for point in points:
            self.selection_list.addItem(f"Point  ·  {point.name}  ·  {image_names.get(point.image_id, '?')}")
        for link in links:
            self.selection_list.addItem(f"Link  ·  {self._link_text(link, point_names)}")

    def _show_image_details(self, image) -> None:
        self.image_info_label.setText(self._image_info_text(image))
        points = [point for point in self.project.control_points if point.observation_for(image.id) is not None]
        point_ids = {point.id for point in points}
        self._fill_point_list(self.image_points_list, points, image.id)
        links = [
            constraint for constraint in self.project.constraints
            if constraint.first_point_id in point_ids or constraint.second_point_id in point_ids
        ]
        self._fill_link_list(self.image_links_list, links)

    def _show_point_details(self, point: GroundControlPoint) -> None:
        self.name_edit.setText(point.name)
        self.label_edit.setText(point.label or "")
        self.geo_x_edit.setText("" if point.geographic_x is None else str(point.geographic_x))
        self.geo_y_edit.setText("" if point.geographic_y is None else str(point.geographic_y))
        observation = point.observations[0] if point.observations else None
        self.pixel_x_edit.setText("" if observation is None else f"{observation.x:g}")
        self.pixel_y_edit.setText("" if observation is None else f"{observation.y:g}")
        self.lock_observation_checkbox.blockSignals(True)
        self.lock_observation_checkbox.setEnabled(observation is not None)
        self.lock_observation_checkbox.setChecked(observation.locked if observation else False)
        self.lock_observation_checkbox.blockSignals(False)
        image_names = {image.id: image.name for image in self.project.images}
        self.point_observations_list.clear()
        for item_observation in point.observations:
            text = f"{image_names.get(item_observation.image_id, '?')}: ({item_observation.x:g}, {item_observation.y:g})"
            if item_observation.locked:
                text += "  [locked]"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, item_observation.image_id)
            self.point_observations_list.addItem(item)
        self._fill_link_list(
            self.point_links_list,
            [item for item in self.project.constraints if point.id in (item.first_point_id, item.second_point_id)],
        )

    def _show_constraint_details(self, constraint: PointConstraint) -> None:
        for combo in (self.constraint_first_edit, self.constraint_second_edit):
            combo.blockSignals(True)
            combo.clear()
            for point in self.project.control_points:
                combo.addItem(point.name, point.id)
        self.constraint_first_edit.setCurrentIndex(self.constraint_first_edit.findData(constraint.first_point_id))
        self.constraint_second_edit.setCurrentIndex(self.constraint_second_edit.findData(constraint.second_point_id))
        self.constraint_first_edit.blockSignals(False)
        self.constraint_second_edit.blockSignals(False)
        self.constraint_kind_edit.setText(constraint.kind)
        image_names = {image.id: image.name for image in self.project.images}
        lines = []
        for point_id in (constraint.first_point_id, constraint.second_point_id):
            point = self.project.point_by_id(point_id)
            if point is not None:
                where = ", ".join(image_names.get(item.image_id, "?") for item in point.observations) or "no images"
                lines.append(f"{point.name}: {where}")
        if self._alignment_result is not None and constraint.id in self._alignment_result.constraint_residuals:
            lines.append(f"Alignment residual: {self._alignment_result.constraint_residuals[constraint.id]:.4f}")
        self.constraint_info_label.setText("\n".join(lines))

    def _on_detail_point_clicked(self, item: QListWidgetItem) -> None:
        self._select_point(item.data(Qt.ItemDataRole.UserRole))

    def _on_detail_link_clicked(self, item: QListWidgetItem) -> None:
        self._select_constraint(item.data(Qt.ItemDataRole.UserRole))

    def _on_detail_observation_clicked(self, item: QListWidgetItem) -> None:
        image_id = item.data(Qt.ItemDataRole.UserRole)
        if any(image.id == image_id for image in self.project.images):
            self.current_image_id = image_id
            self.canvas.fit_images({image_id})
            self._refresh_details()

    def _select_point(self, point_id: str | None) -> None:
        if self.add_points_mode:
            self.add_points_action.setChecked(False)
        if self.link_points_mode:
            self.link_points_action.setChecked(False)
        point = self.project.point_by_id(point_id) if point_id else None
        if point is None:
            self._clear_point_selection()
            self._refresh_details()
            return
        self._clear_image_selection(keep_context=True)
        self._clear_constraint_selection()
        self.selected_point_id = point.id
        if point.image_id is not None:
            self.current_image_id = point.image_id
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        self.canvas.set_selected_point(point.id)
        self._refresh_details()

    def toggle_selected_locks(self) -> None:
        points = [self.project.point_by_id(point_id) for point_id in self._selected_point_ids()]
        observations = [item for point in points for item in point.observations]
        if not observations:
            return
        # Unlock only when everything is already locked; otherwise lock all.
        lock = not all(item.locked for item in observations)
        before = self.project.to_dict()
        for point in points:
            for item in point.observations:
                item.locked = lock
                self.canvas.set_observation_locked(item.image_id, point.id, lock)
        self._record_project_edit(before, "Lock point position" if lock else "Unlock point position")
        self.statusBar().showMessage(f"{len(points)} point(s) {'locked' if lock else 'unlocked'}.")
        self._refresh_details()

    def _set_observation_lock(self, locked: bool) -> None:
        point = self._selected_point()
        observation = point.observations[0] if point and point.observations else None
        if observation is None or observation.locked == locked:
            return
        before = self.project.to_dict()
        observation.locked = locked
        self.canvas.set_observation_locked(observation.image_id, point.id, locked)
        self._record_project_edit(before, "Lock point position" if locked else "Unlock point position")
        state = "locked" if locked else "unlocked"
        self.statusBar().showMessage(f"'{point.name}' position in this image is {state}.")
        self._refresh_map_points()

    def _begin_point_observation_drag(self, _image_id: str, _point_id: str) -> None:
        self._begin_project_edit("Move control point")

    def _on_point_observation_changed(self, image_id: str, point_id: str, x: float, y: float) -> None:
        point = self.project.point_by_id(point_id)
        observation = point.observation_for(image_id) if point else None
        if observation is not None and not observation.locked:
            point.set_observation(image_id, x, y)

    def _on_canvas_point_selected(self, image_id: str, point_id: str) -> None:
        point = self.project.point_by_id(point_id)
        if point is None or point.observation_for(image_id) is None:
            return
        self.current_image_id = image_id
        self._select_point(point_id)
        self.statusBar().showMessage(f"Selected '{point.name}'. Choose Arm selected point before placing or moving an observation.")

    def _set_add_points_mode(self, enabled: bool) -> None:
        if enabled and self.link_points_mode:
            self.link_points_action.setChecked(False)
        if enabled and not self.project.images:
            self.add_points_action.setChecked(False)
            self.statusBar().showMessage("Import an image before enabling continuous point placement.")
            return
        self.add_points_mode = enabled
        if enabled:
            self._clear_image_selection(keep_context=True)
            self._clear_point_selection()
            self._clear_constraint_selection()
            self.pending_point_id = None
            self.pending_constraint_point_id = None
            self.pending_constraint_image_id = None
            self.canvas.set_active_point("__continuous_add__")
            self.canvas.set_constraint_source(None)
            self._refresh_details()
            self.instruction_label.setText("Add points is on: click any image to create a point there. Toggle it off to move images.")
            self.statusBar().showMessage("Add points is on. Click an image to add points continuously.")
        else:
            self.canvas.set_active_point(self.pending_point_id)
            self.instruction_label.setText("Use Add points to click control points onto images, then Link points to join matches.")
            self.statusBar().showMessage("Add points is off. Drag images to move them.")

    def _set_link_points_mode(self, enabled: bool) -> None:
        if enabled and not any(point.observations for point in self.project.control_points):
            self.link_points_action.setChecked(False)
            self.statusBar().showMessage("Add control points to images before linking them.")
            return
        if enabled and self.add_points_mode:
            self.add_points_action.setChecked(False)
        self.link_points_mode = enabled
        self.pending_constraint_point_id = None
        self.pending_constraint_image_id = None
        self.canvas.set_constraint_source(None)
        self.canvas.set_constraint_mode(enabled)
        if enabled:
            self._clear_image_selection(keep_context=True)
            self._clear_point_selection()
            self._clear_constraint_selection()
            self.pending_point_id = None
            self.canvas.set_active_point("__continuous_add__")
            self._refresh_details()
            self.instruction_label.setText("Link points is on: click a point, then click its match in a different image.")
            self.statusBar().showMessage("Link points is on. Click a point marker to choose the first endpoint.")
        else:
            self.canvas.set_active_point("__continuous_add__" if self.add_points_mode else self.pending_point_id)
            self.instruction_label.setText("Use Add points to click control points onto images, then Link points to join matches.")
            self.statusBar().showMessage("Link points is off.")

    def _on_constraint_point_clicked(self, image_id: str, point_id: str) -> None:
        point = self.project.point_by_id(point_id)
        if point is None or point.observation_for(image_id) is None:
            self.statusBar().showMessage("That point has no observation in the clicked image.")
            return
        self.current_image_id = image_id
        if self.pending_constraint_point_id is None:
            self.pending_constraint_point_id = point_id
            self.pending_constraint_image_id = image_id
            self.canvas.set_constraint_source(point_id)
            image = next(item for item in self.project.images if item.id == image_id)
            self.statusBar().showMessage(f"First point: {point.name} in {image.name}. Click a point in another image.")
            return
        if image_id == self.pending_constraint_image_id:
            self.statusBar().showMessage("Choose the matching point in a different image.")
            return
        try:
            before = self.project.to_dict()
            constraint = PointConstraint.create(self.pending_constraint_point_id, point_id)
            self.project.add_constraint(constraint)
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return
        self._record_project_edit(before, "Link control points")
        self.pending_constraint_point_id = None
        self.pending_constraint_image_id = None
        self.canvas.set_constraint_source(None)
        self._refresh_constraints()
        self._select_constraint(constraint.id)
        self.statusBar().showMessage(f"Linked points. Choose the next first point to continue.")

    def _on_constraint_point_missed(self) -> None:
        self.statusBar().showMessage("Link points is on. Click directly on a control-point marker.")

    def _coordinates_from_form(self) -> tuple[float | None, float | None]:
        x_text = self.geo_x_edit.text().strip()
        y_text = self.geo_y_edit.text().strip()
        if bool(x_text) != bool(y_text):
            raise ValueError("Enter both map coordinates, or leave both empty.")
        if not x_text:
            return None, None
        try:
            return float(x_text), float(y_text)
        except ValueError as error:
            raise ValueError("Map coordinates must be numeric values.") from error

    def _on_canvas_clicked(self, image_id: str, x: float, y: float) -> None:
        if self.add_points_mode:
            try:
                name = self.project.next_control_point_name(image_id)
                point = GroundControlPoint.create(self.project.unique_control_point_name(name))
                before = self.project.to_dict()
                self.project.add_control_point(point)
            except ValueError as error:
                self._show_error(str(error))
                return
            point.set_observation(image_id, x, y)
            self.current_image_id = image_id
            self._record_project_edit(before, "Add control point")
            self._refresh_points(point.id)
            image = next(item for item in self.project.images if item.id == image_id)
            self.statusBar().showMessage(f"Added '{point.name}' to {image.name}. Click another image to continue.")
            return
        self.statusBar().showMessage("Turn on Add points to place control points. Drag a marker to move it.")

    def update_selected_point(self) -> None:
        point = self._selected_point()
        if point is None:
            self._show_error("Select a control point first.")
            return
        name = self.name_edit.text().strip()
        if not name:
            self._show_error("A control point needs a name.")
            return
        if any(other.id != point.id and other.name.casefold() == name.casefold() for other in self.project.control_points):
            self._show_error(f"A control point named '{name}' already exists.")
            return
        try:
            geographic_x, geographic_y = self._coordinates_from_form()
            observation = point.observations[0] if point.observations else None
            if observation is not None:
                pixel_x_text = self.pixel_x_edit.text().strip()
                pixel_y_text = self.pixel_y_edit.text().strip()
                if bool(pixel_x_text) != bool(pixel_y_text):
                    raise ValueError("Enter both pixel coordinates.")
                if pixel_x_text:
                    pixel_x = float(pixel_x_text)
                    pixel_y = float(pixel_y_text)
                    coordinates_changed = (pixel_x, pixel_y) != (observation.x, observation.y)
                    if coordinates_changed and observation.locked:
                        raise ValueError("Unlock this image observation before editing its pixel coordinates.")
        except ValueError as error:
            self._show_error(str(error))
            return
        before = self.project.to_dict()
        point.geographic_x = geographic_x
        point.geographic_y = geographic_y
        if coordinates_changed:
            point.set_observation(observation.image_id, pixel_x, pixel_y)
        point.name = name
        point.label = self.label_edit.text().strip() or None
        self._record_project_edit(before, "Edit control point")
        self._refresh_points(point.id)

    def update_selected_constraint(self) -> None:
        constraint = self._selected_constraint()
        if constraint is None:
            return
        first_id = self.constraint_first_edit.currentData()
        second_id = self.constraint_second_edit.currentData()
        kind = self.constraint_kind_edit.text().strip()
        if not first_id or not second_id:
            self._show_error("Choose both constraint endpoints.")
            return
        if first_id == second_id:
            self._show_error("A point cannot be constrained to itself.")
            return
        if not kind:
            self._show_error("Constraint type cannot be empty.")
            return
        duplicate = any(
            other.id != constraint.id
            and {other.first_point_id, other.second_point_id} == {first_id, second_id}
            and other.kind == kind
            for other in self.project.constraints
        )
        if duplicate:
            self._show_error("Those points are already connected by this constraint.")
            return
        before = self.project.to_dict()
        constraint.first_point_id = first_id
        constraint.second_point_id = second_id
        constraint.kind = kind
        self._record_project_edit(before, "Edit point constraint")
        self._refresh_constraints()
        self.statusBar().showMessage("Constraint properties updated.")

    def _on_map_crs_chosen(self, *_args) -> None:
        value = self.map_crs_combo.currentText().strip()
        if value and value != self.project.crs:
            self.crs_edit.setText(value)
            self._update_crs()

    def _sync_map_crs_combo(self, text: str) -> None:
        if self.map_crs_combo.currentText() != text:
            self.map_crs_combo.setCurrentText(text)

    def _update_crs(self) -> None:
        value = self.crs_edit.text().strip()
        if value and value != self.project.crs:
            try:
                transformer = Transformer.from_crs(
                    CRS.from_user_input(self.project.crs),
                    CRS.from_user_input(value),
                    always_xy=True,
                )
            except Exception as error:
                self._show_error(f"Cannot switch to coordinate system '{value}': {error}")
                self.crs_edit.setText(self.project.crs)
                return
            before = self.project.to_dict()
            self._reproject_project(transformer)
            self.project.crs = value
            self._resolve_world_transforms()
            self._record_project_edit(before, "Change coordinate system")
            self._refresh_points(self._selected_point().id if self._selected_point() else None)
            self._refresh_world_map(fit=True)

    def _resolve_world_transforms(self) -> None:
        """Refit image transforms to the reprojected GCPs, since reprojection alone is only a similarity approximation."""
        initial = {
            image.id: (image.world_x, image.world_y, image.world_rotation, image.world_scale)
            for image in self.project.images
            if image.world_x is not None and image.world_y is not None
        }
        try:
            with self._progress("Refitting image transforms…") as update:
                result = simulate_georeference(
                    self.project, initial_transforms=initial, progress=lambda fraction: update(fraction * 100)
                )
        except ValueError:
            return
        for image in self.project.images:
            transform = result.transforms.get(image.id)
            if transform is not None:
                image.world_x, image.world_y, image.world_rotation, image.world_scale = transform

    def _reproject_project(self, transformer: Transformer) -> None:
        for point in self.project.control_points:
            if point.geographic_x is not None and point.geographic_y is not None:
                point.geographic_x, point.geographic_y = transformer.transform(point.geographic_x, point.geographic_y)
        reference_length = 100.0
        for image in self.project.images:
            if image.world_x is None or image.world_y is None:
                continue
            angle = radians(image.world_rotation)
            reach = image.world_scale * reference_length
            new_x, new_y = transformer.transform(image.world_x, image.world_y)
            axis_x, axis_y = transformer.transform(
                image.world_x + reach * cos(angle),
                image.world_y + reach * sin(angle),
            )
            delta_x, delta_y = axis_x - new_x, axis_y - new_y
            image.world_x, image.world_y = new_x, new_y
            image.world_rotation = degrees(atan2(delta_y, delta_x))
            image.world_scale = hypot(delta_x, delta_y) / reference_length

    def create_constraint(self) -> None:
        first_id = self._selected_point_id()
        if first_id is None:
            self._show_error("Select one source control point first.")
            return
        source = self.project.point_by_id(first_id)
        target_points = [
            point for point in self.project.control_points
            if point.id != first_id and point.image_id != source.image_id
        ]
        if not target_points:
            self._show_error("Add a control point in another image before creating a link.")
            return
        target_names = [point.name for point in target_points]
        target_name, accepted = QInputDialog.getItem(
            self,
            "Link control points",
            "Target control point:",
            target_names,
            0,
            False,
        )
        if not accepted:
            return
        second_id = next(point.id for point in target_points if point.name == target_name)
        try:
            constraint = PointConstraint.create(first_id, second_id)
            before = self.project.to_dict()
            self.project.add_constraint(constraint)
        except ValueError as error:
            self._show_error(str(error))
            return
        self._record_project_edit(before, "Link control points")
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        self._refresh_constraints()
        self._select_constraint(constraint.id)
        self.statusBar().showMessage("Control points linked by a tie constraint.")

    def delete_selected(self) -> None:
        point_ids = self._selected_point_ids()
        constraint_ids = self._selected_constraint_ids()
        if self._selected_image_ids():
            if not self.remove_selected_image():
                return
            # Removing images clears the point and link selection; restore what survived.
            self.selected_point_ids = point_ids
            self.selected_constraint_ids = constraint_ids
        if self._selected_constraint_ids():
            self.remove_constraint()
        if self._selected_point_ids():
            self.remove_selected_point()

    def remove_constraint(self) -> None:
        constraint_ids = set(self._selected_constraint_ids())
        if not constraint_ids:
            return
        before = self.project.to_dict()
        self.project.constraints = [item for item in self.project.constraints if item.id not in constraint_ids]
        self._record_project_edit(before, "Remove point constraint")
        self.pending_point_id = None
        self.selected_constraint_id = None
        self.canvas.set_active_point(None)
        self._refresh_constraints()

    def arrange_images(self) -> None:
        if self._aligned_view_active():
            self._set_canvas_state("rest")
        layout = []
        for image in self.project.images:
            pixmap, _error = self._pixmap_for_image(image)
            if pixmap.isNull():
                continue
            bounds = QTransform().rotate(image.rotation).mapRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
            layout.append((image, bounds, bounds.width() * image.scale, bounds.height() * image.scale))
        if not layout:
            return

        column_count = ceil(sqrt(len(layout)))
        row_count = ceil(len(layout) / column_count)
        column_widths = [0.0] * column_count
        row_heights = [0.0] * row_count
        for index, (_image, _bounds, width, height) in enumerate(layout):
            row, column = divmod(index, column_count)
            column_widths[column] = max(column_widths[column], width)
            row_heights[row] = max(row_heights[row], height)

        column_x = []
        offset_x = 0.0
        for width in column_widths:
            column_x.append(offset_x)
            offset_x += width + 40.0
        row_y = []
        offset_y = 0.0
        for height in row_heights:
            row_y.append(offset_y)
            offset_y += height + 40.0

        self._begin_project_edit("Arrange images")
        for index, (image, bounds, _width, _height) in enumerate(layout):
            row, column = divmod(index, column_count)
            self.canvas.set_image_position(
                image.id,
                column_x[column] - bounds.left() * image.scale,
                row_y[row] - bounds.top() * image.scale,
            )
        self._finish_project_edit()
        self.canvas.fit_images()

    def import_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import images",
            self._last_directory(),
            f"Images ({' '.join('*' + extension for extension in sorted(self.IMAGE_EXTENSIONS))});;All files (*)",
        )
        if not paths:
            return
        self._remember_directory(paths[0])
        self._import_image_paths(paths)

    def _import_image_paths(self, paths: list[str]) -> None:
        before = self._begin_project_edit("Import images")
        added_image_ids = set()
        known_image_paths = {image.path for image in self.project.images}
        failures = []
        for path in paths:
            resolved_path = str(Path(path).expanduser().resolve())
            if resolved_path in known_image_paths:
                continue
            pixmap, error = self._read_image_pixmap(path)
            if pixmap.isNull():
                failures.append(f"{Path(path).name}: {error}")
                continue
            image = self.project.add_image(path)
            self._image_pixmaps[image.id] = pixmap
            added_image_ids.add(image.id)
            known_image_paths.add(resolved_path)
        if not added_image_ids:
            message = "No images imported. " + "; ".join(failures[:3]) if failures else "No new images to import."
            self.statusBar().showMessage(message)
            self._finish_project_edit()
            return
        self._refresh_images()
        self.arrange_images()
        message = f"Imported {len(added_image_ids)} image(s). Project contains {len(self.project.images)} image(s)."
        if failures:
            message += " Skipped: " + "; ".join(failures[:3])
            if len(failures) > 3:
                message += f"; and {len(failures) - 3} more"
        self.statusBar().showMessage(message)
        self._finish_project_edit()

    @classmethod
    def _local_file_paths(cls, mime_data) -> list[str]:
        return [
            url.toLocalFile()
            for url in mime_data.urls()
            if url.isLocalFile()
        ]

    def dragEnterEvent(self, event) -> None:
        if self._local_file_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._local_file_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._local_file_paths(event.mimeData())
        if paths:
            self._import_image_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def remove_selected_image(self) -> bool:
        image_ids = set(self._selected_image_ids())
        images = [item for item in self.project.images if item.id in image_ids]
        if not images:
            return False
        names = f"'{images[0].name}'" if len(images) == 1 else f"{len(images)} images"
        answer = QMessageBox.question(self, "Remove image", f"Remove {names} and their control points?")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        before = self.project.to_dict()
        self.project.images = [item for item in self.project.images if item.id not in image_ids]
        for image_id in image_ids:
            self._image_pixmaps.pop(image_id, None)
        removed_point_ids = {point.id for point in self.project.control_points if point.image_id in image_ids}
        self.project.control_points = [
            point for point in self.project.control_points if point.id not in removed_point_ids
        ]
        self.project.constraints = [
            constraint for constraint in self.project.constraints
            if not {constraint.first_point_id, constraint.second_point_id} & removed_point_ids
        ]
        self.selected_point_id = None
        self.selected_constraint_id = None
        self._record_project_edit(before, "Remove image")
        self.current_image_id = None
        self._refresh_all()
        return True

    def remove_selected_point(self) -> None:
        point_ids = set(self._selected_point_ids())
        if not point_ids:
            return
        before = self.project.to_dict()
        self.project.control_points = [item for item in self.project.control_points if item.id not in point_ids]
        self.project.constraints = [
            constraint for constraint in self.project.constraints
            if not {constraint.first_point_id, constraint.second_point_id} & point_ids
        ]
        self._record_project_edit(before, "Delete control point")
        self.pending_point_id = None
        self.canvas.set_active_point(None)
        self.selected_point_id = None
        self._refresh_points()

    def new_project(self) -> None:
        self.undo_stack.clear()
        self.project = Project()
        self._image_pixmaps.clear()
        self.project_path = None
        self.current_image_id = None
        self.pending_point_id = None
        self.selected_point_id = None
        self.selected_constraint_id = None
        self._refresh_all()
        self.statusBar().showMessage("New project created.")

    def _last_directory(self) -> str:
        return str(self._settings.value("last_directory", "") or "")

    def _remember_directory(self, path: str | Path) -> None:
        directory = Path(path).parent
        self._settings.setValue("last_directory", str(directory))

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", self._last_directory(), "GeoStitch project (*.gsp);;JSON files (*.json)"
        )
        if not path:
            return
        self._open_project_path(path)

    def _open_project_path(self, path: str, quiet: bool = False) -> None:
        try:
            project = load_project(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            if not quiet:
                self._show_error(f"Could not open project: {error}")
            return
        self._remember_directory(path)
        self._settings.setValue("last_project", str(Path(path).resolve()))
        self.undo_stack.clear()
        self.project = project
        self._image_pixmaps.clear()
        self.project_path = Path(path)
        self.current_image_id = project.images[0].id if project.images else None
        self.pending_point_id = None
        self.selected_point_id = None
        self.selected_constraint_id = None
        self._refresh_all()
        self._restore_views()
        self.statusBar().showMessage(f"Opened {path}")

    def _restore_views(self) -> None:
        if "canvas" in self.project.views:
            self.canvas.restore_view_state(self.project.views["canvas"])
        if "map" in self.project.views:
            self.world_map.restore_view_state(self.project.views["map"])

    def save_project_as(self) -> None:
        self.save_current_project(save_as=True)

    def save_current_project(self, _checked: bool = False, save_as: bool = False) -> None:
        if self.project_path is None or save_as:
            start = str(self.project_path) if self.project_path else self._last_directory()
            path, _ = QFileDialog.getSaveFileName(self, "Save project", start, "GeoStitch project (*.gsp)")
            if not path:
                return
            self._remember_directory(path)
            self.project_path = Path(path)
            if self.project_path.suffix.lower() != ".gsp":
                self.project_path = self.project_path.with_suffix(".gsp")
        try:
            self._update_crs()
            self.project.views = {"canvas": self.canvas.view_state(), "map": self.world_map.view_state()}
            save_project(self.project, self.project_path)
        except OSError as error:
            self._show_error(f"Could not save project: {error}")
            return
        self.statusBar().showMessage(f"Saved {self.project_path}")
        self._settings.setValue("last_project", str(self.project_path.resolve()))
        self._update_title()

    def _panel_under_focus_or_cursor(self):
        """The dock under the cursor, else the focused or last active one (menu use leaves the cursor outside any panel)."""
        return (
            self._dock_containing(QApplication.widgetAt(QCursor.pos()))
            or self._dock_under_focus_or_cursor()
            or self._last_active_dock
        )

    def _remember_active_dock(self, _old, new) -> None:
        dock = self._dock_containing(new)
        if dock is not None:
            self._last_active_dock = dock

    def _all_docks(self) -> tuple[QDockWidget, ...]:
        return (self.image_dock, self.canvas_dock, self.details_dock, self.world_map_dock, self.image_view_dock)

    def _dock_containing(self, widget) -> QDockWidget | None:
        for dock in self._all_docks():
            if widget is not None and (widget is dock or dock.isAncestorOf(widget)):
                return dock
        return None

    def _dock_under_focus_or_cursor(self) -> QDockWidget | None:
        for widget in (QApplication.focusWidget(), QApplication.widgetAt(QCursor.pos())):
            dock = self._dock_containing(widget)
            if dock is not None:
                return dock
        return None

    def toggle_maximize_dock(self) -> None:
        if self._maximized_dock is not None:
            self._restore_docks()
            self.statusBar().showMessage("Panels restored.")
            return
        dock = (
            self._dock_containing(QApplication.widgetAt(QCursor.pos()))
            or self._dock_under_focus_or_cursor()
            or self.canvas_dock
        )
        self._layout_before_maximize = self.saveState(3)
        self._hidden_docks = [other for other in self._all_docks() if other is not dock and other.isVisible()]
        for other in self._hidden_docks:
            other.hide()
        self._maximized_dock = dock
        self.statusBar().showMessage(f"Maximized '{dock.windowTitle()}'. Press the shortcut again to restore.")

    def _restore_docks(self) -> None:
        layout, self._layout_before_maximize = self._layout_before_maximize, None
        for dock in self._hidden_docks:
            dock.show()
        if layout is not None:
            self.restoreState(layout, 3)
        self._hidden_docks = []
        self._maximized_dock = None

    def _fit_canvas(self) -> None:
        panel = self._panel_under_focus_or_cursor()
        if panel is self.image_view_dock:
            self.image_viewer.fit_image()
            return
        if panel is self.world_map_dock:
            self.world_map.fit_world()
            return
        selected_image_ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.image_list.selectedItems()
        }
        point_ids = set(self._selected_point_ids())
        chosen_links = set(self._selected_constraint_ids())
        link_indices = {
            index for index, item in enumerate(self.project.constraints) if item.id in chosen_links
        }
        if point_ids or link_indices:
            self.canvas.fit_items(selected_image_ids, point_ids, link_indices)
            return
        self.canvas.fit_images(selected_image_ids or None)

    def simulate_alignment_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Simulate image alignment")
        dialog.resize(540, 480)
        layout = QVBoxLayout(dialog)

        settings_form = QFormLayout()
        tolerance_spin = QDoubleSpinBox()
        tolerance_spin.setRange(0.01, 10000.0)
        tolerance_spin.setDecimals(2)
        tolerance_spin.setValue(0.5)
        tolerance_spin.setSuffix(" scene units")
        iterations_spin = QSpinBox()
        iterations_spin.setRange(1, 10000)
        iterations_spin.setValue(100)
        settings_form.addRow("Target maximum residual", tolerance_spin)
        settings_form.addRow("Maximum iterations", iterations_spin)
        layout.addLayout(settings_form)

        report = QLabel("No simulation run yet.")
        report.setWordWrap(True)
        report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(report, 1)

        result: AlignmentResult | None = None
        preview_transforms: dict[str, tuple[float, float, float, float]] | None = None

        def run_simulation() -> None:
            nonlocal result, preview_transforms
            self._set_canvas_state("rest")
            try:
                with self._progress("Running alignment solver…") as update:
                    result = simulate_alignment(
                        self.project,
                        tolerance=tolerance_spin.value(),
                        max_iterations=iterations_spin.value(),
                        initial_transforms={
                            image.id: self.canvas.image_transform(image.id)
                            for image in self.project.images
                            if self.canvas.image_transform(image.id) is not None
                        },
                        progress=lambda fraction: update(fraction * 100),
                    )
            except ValueError as error:
                QMessageBox.warning(dialog, "Cannot simulate alignment", str(error))
                return
            self._alignment_result = result
            preview_transforms = result.transforms
            anchor_names = [
                next(image.name for image in self.project.images if image.id == image_id)
                for image_id in result.anchor_image_ids
            ]
            lines = [
                f"Iterations: {result.iterations}",
                f"Maximum constraint residual: {result.maximum_residual:.3f} scene units",
                f"Status: {'target reached' if result.converged else 'target not reached'}",
                f"Fixed reference image(s): {', '.join(anchor_names)}",
                "",
                "Residuals by constraint:",
            ]
            constraint_names = {item.id: item for item in self.project.constraints}
            point_names = {point.id: point.name for point in self.project.control_points}
            for constraint_id, residual in result.constraint_residuals.items():
                constraint = constraint_names[constraint_id]
                first_name = point_names[constraint.first_point_id]
                second_name = point_names[constraint.second_point_id]
                lines.append(f"{first_name} ↔ {second_name}: {residual:.3f} scene units")
            report.setText("\n".join(lines))
            apply_button.setEnabled(True)
            georeference_button.setEnabled(True)

        def apply_result() -> None:
            if result is None or preview_transforms is None:
                return
            self._apply_alignment_result(result)
            dialog.accept()

        def run_second_stage() -> None:
            if result is not None:
                dialog.accept()
                self.simulate_georeference_dialog()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        simulate_button = buttons.addButton("Run simulation", QDialogButtonBox.ButtonRole.ActionRole)
        apply_button = buttons.addButton("Apply transforms", QDialogButtonBox.ButtonRole.ApplyRole)
        georeference_button = buttons.addButton("Step 2: Georeference", QDialogButtonBox.ButtonRole.ActionRole)
        apply_button.setEnabled(False)
        georeference_button.setEnabled(False)
        simulate_button.clicked.connect(run_simulation)
        apply_button.clicked.connect(apply_result)
        georeference_button.clicked.connect(run_second_stage)
        buttons.rejected.connect(dialog.reject)
        dialog_layout = buttons
        layout.addWidget(dialog_layout)
        dialog.exec()

    def _apply_alignment_result(self, result: AlignmentResult) -> None:
        before = self.project.to_dict()
        for image in self.project.images:
            transform = result.transforms.get(image.id)
            if transform is None:
                continue
            image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale = transform
        self.project.canvas_state = "aligned"
        self._record_project_edit(before, "Apply simulated alignment")
        self._refresh_canvas()

    def simulate_georeference_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Step 2: Georeference ({self.project.crs})")
        dialog.resize(560, 500)
        layout = QVBoxLayout(dialog)

        settings_form = QFormLayout()
        tolerance_spin = QDoubleSpinBox()
        tolerance_spin.setRange(1e-8, 1_000_000.0)
        tolerance_spin.setDecimals(6)
        tolerance_spin.setValue(1.0)
        tolerance_spin.setSuffix(" CRS units")
        iterations_spin = QSpinBox()
        iterations_spin.setRange(1, 10000)
        iterations_spin.setValue(1000)
        settings_form.addRow("Target maximum GCP residual", tolerance_spin)
        settings_form.addRow("Maximum iterations", iterations_spin)
        layout.addLayout(settings_form)

        report = QLabel("This step uses control points with geographic X/Y coordinates in the project CRS.")
        report.setWordWrap(True)
        report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(report, 1)

        result: GeoreferenceResult | None = None

        def run_georeference() -> None:
            nonlocal result
            initial = {
                image.id: (image.world_x or 0.0, image.world_y or 0.0, image.world_rotation, image.world_scale)
                for image in self.project.images
                if image.world_x is not None and image.world_y is not None
            }
            try:
                with self._progress("Running georeference solver…") as update:
                    result = simulate_georeference(
                        self.project,
                        tolerance=tolerance_spin.value(),
                        max_iterations=iterations_spin.value(),
                        initial_transforms=initial,
                        progress=lambda fraction: update(fraction * 100),
                    )
            except ValueError as error:
                QMessageBox.warning(dialog, "Cannot georeference", str(error))
                return

            image_names = {image.id: image.name for image in self.project.images}
            point_names = {point.id: point.name for point in self.project.control_points}
            residual_lines = [
                f"{image_names[image_id]} / {point_names[point_id]}: {residual:.4f} {result.crs} units"
                for (image_id, point_id), residual in result.observation_residuals.items()
            ]
            solved_images = set(result.transforms)
            unsolved_names = [image.name for image in self.project.images if image.id not in solved_images]
            lines = [
                f"CRS: {result.crs}",
                f"Images georeferenced: {len(solved_images)} / {len(self.project.images)}",
                f"Iterations: {result.iterations}",
                f"Maximum GCP residual: {result.maximum_residual:.4f} CRS units",
                f"Status: {'target reached' if result.converged else 'target not reached'}",
                "",
                "Residuals by image / control point:",
                *residual_lines,
            ]
            if unsolved_names:
                lines.extend(("", "Images without usable geographic control points:", *unsolved_names))
            lines.extend(("", "These are world-coordinate transforms only; no raster mosaic is generated."))
            report.setText("\n".join(lines))
            apply_button.setEnabled(True)

        def apply_georeference() -> None:
            if result is None:
                return
            self._apply_georeference_result(result)
            dialog.accept()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        run_button = buttons.addButton("Run GCP solver", QDialogButtonBox.ButtonRole.ActionRole)
        apply_button = buttons.addButton("Apply world transforms", QDialogButtonBox.ButtonRole.ApplyRole)
        apply_button.setEnabled(False)
        run_button.clicked.connect(run_georeference)
        apply_button.clicked.connect(apply_georeference)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _apply_georeference_result(self, result: GeoreferenceResult) -> None:
        before = self.project.to_dict()
        self.project.crs = result.crs
        for image in self.project.images:
            transform = result.transforms.get(image.id)
            if transform is None:
                continue
            image.world_x, image.world_y, image.world_rotation, image.world_scale = transform
        self._record_project_edit(before, "Apply GCP georeferencing")
        self.crs_edit.setText(self.project.crs)
        self._refresh_world_map(fit=True)

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else self.project.name
        self.setWindowTitle(f"GeoStitch | {name}")

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "GeoStitch", message)


def main() -> int:
    if sys.platform == "win32":
        import ctypes

        # A distinct AppUserModelID makes Windows show the app icon on the taskbar instead of python.exe's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GeoStitch.Control")
    app = QApplication(sys.argv)
    app.setApplicationName("GeoStitch")
    app.setWindowIcon(app_icon())
    # Qt's default 256 MB cap rejects large survey images before the Pillow fallback is tried.
    QImageReader.setAllocationLimit(2048)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

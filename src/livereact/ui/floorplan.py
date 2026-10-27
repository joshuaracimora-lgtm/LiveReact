"""Scaled venue floor plan: the rig drawn in real space (feet on screen,
meters underneath -- fixtures/geometry.py's convention).

Two uses (2026-09-18 UI redesign):

  * ``editable=True``  -- the Venue tab: drag fixtures and named targets
    around the room (snapping to half a foot), which writes real positions
    onto the live ``Fixture``/``Venue`` objects and calls ``on_changed``.
  * ``editable=False`` -- the Show tab's live mini-map: same drawing, nothing
    movable, so a stray click mid-set can't move anything.

Drawing: the booth wall (y = 0) is at the BOTTOM of the view, the crowd
extends upward, x runs to the right (the DJ's right). Moving heads draw the
beam where the current pan/tilt actually lands on the floor (via the head's
captured calibration); lasers draw their fan wedge.

Legibility (2026-10-03, Josh's garage plan -- pars on the booth directly
under the heads, two targets on one spot, captions piling up on the booth
row):

  * every fixture type has its own glyph (head = disc with a facing tick,
    par = rounded square, wash bar = long rectangle, laser = triangle whose
    apex points the way the fan goes, target = diamond; a ceiling target is
    a larger hollow diamond), so a pile is still readable;
  * fixtures drawn on one spot form a STACK: the highest-mounted one keeps
    its glyph on top, each one beneath it is a progressively larger dashed
    outline around it -- all of them stay visible and clickable;
  * captions (title + muted "@address · height" line) are separate items
    laid out by ``_layout_labels``: each
    starts under its body and moves to the nearest free slot around it when
    it would overlap a body, another caption or the axis text, with a
    dotted leader back to its body when it had to move away.

Body positions are never nudged for looks: a drag still writes exactly the
snapped position of the body, and ``item_position_m`` reads it back.

All geometry math is in fixtures/geometry.py; this module only paints.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import Shiboken

from ..fixtures.geometry import (
    Point3,
    Venue,
    beam_floor_hit,
    ft_to_m,
    m_to_ft,
    pan_tilt_to_angles,
    ray_to_rect_edge,
)
from ..fixtures.profiles import Laser, MovingHead, WashBar
from ..fixtures.rig import Rig

PX_PER_M = 40.0                 # scene pixels per meter
SNAP_M = ft_to_m(0.5)           # drag snap: half a foot
DEFAULT_VENUE_FT = (30.0, 40.0, 12.0)   # width, length, ceiling -- when no venue is declared
_MARGIN = 28                    # scene margin around the room for edge labels
_LEGEND_H = 16                  # extra band above the room for the glyph legend

# Where an unplaced fixture is drawn, as fractions of the room (x by side, y
# by kind: everything hugs the booth wall until the operator places it).
_ROW_Y_FRAC = {"spot": 0.16, "laser": 0.26, "par": 0.36, "bar": 0.46}
_BODY_R_M = 0.35                # drawn head radius (m)
_PAR_W_M = 0.55                 # par: rounded square side (m)
_LASER_W_M = 0.70               # laser: triangle footprint (m)
_BAR_W_M, _BAR_H_M = 1.2, 0.25  # wash bar footprint (m)
_TARGET_W_M = 0.45              # target diamond (m)
_CEILING_TARGET_W_M = 0.62      # a ceiling target: bigger and hollow, so it reads under a floor target
_STACK_RADIUS_M = 0.3           # bodies closer than this are drawn as one stack
_STACK_STEP = 0.45              # each layer beneath the top one grows by this much

_GRID_LIGHT = QtGui.QColor(34, 38, 46)
_GRID_BOLD = QtGui.QColor(52, 58, 70)
_ROOM_EDGE = QtGui.QColor(120, 128, 140)
_TEXT = QtGui.QColor(150, 155, 165)
_TITLE = QtGui.QColor(222, 226, 234)
_LEADER = QtGui.QColor(120, 126, 138)
_IDLE = QtGui.QColor(60, 60, 70)
_TARGET = QtGui.QColor(245, 166, 35)

_LEGEND = "○ head    □ par    ▭ wash bar    ▲ laser    ◆ target    ◇ ceiling target    dashed ring = fixture underneath"


def _default_venue() -> Venue:
    w, l, h = (ft_to_m(v) for v in DEFAULT_VENUE_FT)
    return Venue(w, l, h, Venue.default_targets(w, l, h))


def _scaled(p: QtCore.QPointF, k: float) -> QtCore.QPointF:
    return QtCore.QPointF(p.x() * k, p.y() * k)


class PlanItem(QtWidgets.QGraphicsObject):
    """A fixture body or a target marker: paints itself as one object so it
    moves as one, and reports drops back to the view. Its caption is a
    separate ``_Caption`` (laid out by the view), not part of this item."""

    def __init__(self, key: str, kind: str, label: str, w_m: float, h_m: float,
                 shape: str = "head") -> None:
        super().__init__()
        self.key = key
        self.kind = kind            # "fixture" | "target"
        self.label = label
        self.glyph = shape          # head | par | bar | laser | target | ceiling_target
        self.w_px, self.h_px = w_m * PX_PER_M, h_m * PX_PER_M
        # > 1.0 for the lower member(s) of a stack: the glyph becomes a bigger
        # dashed outline around the one drawn on top of it.
        self.glyph_scale = 1.0
        self.facing_deg = 0.0
        self.fill = QtGui.QColor(0, 0, 0)
        self.edge = _IDLE
        self.edge_w = 1.0
        self.setZValue(10)

    def glyph_rect(self) -> QtCore.QRectF:
        w, h = self.w_px * self.glyph_scale, self.h_px * self.glyph_scale
        return QtCore.QRectF(-w / 2, -h / 2, w, h)

    def set_glyph_scale(self, k: float) -> None:
        if k != self.glyph_scale:
            self.prepareGeometryChange()
            self.glyph_scale = k
            self.update()

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802 (Qt override)
        pad = 6
        return self.glyph_rect().adjusted(-pad, -pad, pad, pad)

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:  # noqa: ARG002
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.glyph_rect()
        pen = QtGui.QPen(self.edge, self.edge_w)
        if self.glyph_scale > 1.0:
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(self.fill))
        r = rect.width() / 2
        a = math.radians(self.facing_deg)
        # screen y is flipped, so "into the crowd" (az 0) is straight UP
        fwd = QtCore.QPointF(math.sin(a), -math.cos(a))
        right = QtCore.QPointF(-fwd.y(), fwd.x())
        if self.glyph in ("target", "ceiling_target"):
            path = QtGui.QPainterPath()
            path.moveTo(0, -r); path.lineTo(r, 0); path.lineTo(0, r); path.lineTo(-r, 0); path.closeSubpath()
            if self.glyph == "ceiling_target":
                hollow = QtGui.QPen(self.edge, self.edge_w)
                hollow.setStyle(QtCore.Qt.PenStyle.DashLine)
                painter.setPen(hollow)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif self.glyph == "bar":
            painter.drawRect(rect)
        elif self.glyph == "par":
            painter.drawRoundedRect(rect, 3, 3)
        elif self.glyph == "laser":
            apex = _scaled(fwd, r * 1.15)
            base = _scaled(fwd, -r * 0.75)
            wing = _scaled(right, r * 0.85)
            path = QtGui.QPainterPath()
            path.moveTo(apex); path.lineTo(base + wing); path.lineTo(base - wing); path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawEllipse(rect)
            # facing tick: which way the body's pan=0.5 points
            painter.setPen(QtGui.QPen(_TEXT, 2))
            painter.drawLine(QtCore.QPointF(0, 0), _scaled(fwd, r * 1.4))
        if self.isSelected():
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1, QtCore.Qt.PenStyle.DashLine))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(-3, -3, 3, 3))


class _Caption:
    """A body's caption: a title line and a muted sub-line, plus a dotted
    leader used when the caption had to move away from its body.
    Positioned by ``FloorPlanView._layout_labels``."""

    def __init__(self, scene: QtWidgets.QGraphicsScene, item: PlanItem) -> None:
        self.item = item
        self.title = scene.addSimpleText("")
        self.title.setFont(QtGui.QFont("Arial", 8, QtGui.QFont.Weight.DemiBold))
        self.title.setBrush(_TITLE)
        self.sub = scene.addSimpleText("")
        self.sub.setFont(QtGui.QFont("Arial", 7))
        self.sub.setBrush(_TEXT)
        for t in (self.title, self.sub):
            t.setZValue(12)
        self.leader = scene.addLine(0, 0, 0, 0, QtGui.QPen(_LEADER, 1, QtCore.Qt.PenStyle.DotLine))
        self.leader.setZValue(8)
        self.leader.setVisible(False)

    def set_text(self, title: str, sub: str = "") -> None:
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

    def _lines(self) -> list[QtWidgets.QGraphicsSimpleTextItem]:
        return [t for t in (self.title, self.sub) if t.text()]

    def size(self) -> tuple[float, float]:
        lines = self._lines()
        w = max((t.boundingRect().width() for t in lines), default=0.0)
        h = sum(t.boundingRect().height() for t in lines) + max(0, len(lines) - 1)
        return w + 4, h + 2

    def place(self, rect: QtCore.QRectF) -> None:
        y = rect.top() + 1
        cx = rect.center().x()
        for t in self._lines():
            br = t.boundingRect()
            t.setPos(cx - br.width() / 2, y)
            y += br.height() + 1

    def leader_to(self, rect: QtCore.QRectF | None) -> None:
        """Dotted line from the body's centre to the nearest point of the
        caption's rectangle; hidden when the caption sits next to its body."""
        if rect is None:
            self.leader.setVisible(False)
            return
        a = self.item.pos()
        bx = min(max(a.x(), rect.left()), rect.right())
        by = min(max(a.y(), rect.top()), rect.bottom())
        self.leader.setLine(a.x(), a.y(), bx, by)
        self.leader.setVisible(True)


class FloorPlanView(QtWidgets.QGraphicsView):
    fixture_selected = QtCore.Signal(str)   # slot_id, or "" when nothing is selected

    # Caption slots around a body, tried in order: (dx, dy) in units of
    # "one caption away", y positive = below (screen). The first eight hug
    # the body; the rest step further out and get a leader line.
    _SLOTS = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1),
              (0, 2), (0, -2), (1, 2), (-1, 2), (1, -2), (-1, -2), (2, 0), (-2, 0),
              (2, 1), (-2, 1), (2, -1), (-2, -1), (0, 3), (0, -3)]

    def __init__(self, rig: Rig, editable: bool = False,
                 on_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.rig = rig
        self.editable = editable
        self.on_changed = on_changed
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QtGui.QColor(12, 12, 16))
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid: list[QtWidgets.QGraphicsItem] = []
        self._bodies: dict[str, PlanItem] = {}
        self._targets: dict[str, PlanItem] = {}
        self._beams: dict[str, QtWidgets.QGraphicsLineItem] = {}
        self._wedges: dict[str, QtWidgets.QGraphicsPolygonItem] = {}
        self._captions: dict[str, _Caption] = {}
        self._layout_dirty = False
        self._dragging: set[str] = set()
        self._press_pos: dict[int, QtCore.QPointF] = {}
        self._scene.selectionChanged.connect(self._on_selection)
        self.refresh_layout()

    # ------------------------------------------------------------ geometry
    def venue(self) -> Venue:
        return self.rig.venue if self.rig.venue is not None else _default_venue()

    def to_scene(self, x_m: float, y_m: float) -> QtCore.QPointF:
        """Meters -> scene px; the booth wall (y=0) sits at the bottom."""
        return QtCore.QPointF(x_m * PX_PER_M, (self.venue().length_m - y_m) * PX_PER_M)

    def from_scene(self, p: QtCore.QPointF) -> tuple[float, float]:
        return p.x() / PX_PER_M, self.venue().length_m - p.y() / PX_PER_M

    def _fallback_xy(self, fx) -> tuple[float, float]:
        """Where to draw a fixture that has no real position yet."""
        v = self.venue()
        if fx.layout_x is not None and fx.layout_y is not None:
            return fx.layout_x * v.width_m, (1.0 - fx.layout_y) * v.length_m
        if isinstance(fx, MovingHead):
            row = "spot"
        elif isinstance(fx, Laser):
            row = "laser"
        elif isinstance(fx, WashBar):
            row = "bar"
        else:
            row = "par"
        x = v.width_m * (0.3 if fx.side == "left" else 0.7)
        return x, v.length_m * _ROW_Y_FRAC[row]

    def fixture_origin(self, fx) -> Point3:
        """The fixture's emitter point in meters (drawn position + height)."""
        if fx.position is not None:
            return fx.position
        x, y = self._fallback_xy(fx)
        return Point3(x, y, 1.0)

    @staticmethod
    def _glyph(fx) -> tuple[str, float, float]:
        """(shape, width_m, height_m) of a fixture's body glyph."""
        if isinstance(fx, MovingHead):
            return "head", _BODY_R_M * 2, _BODY_R_M * 2
        if isinstance(fx, Laser):
            return "laser", _LASER_W_M, _LASER_W_M
        if isinstance(fx, WashBar):
            return "bar", _BAR_W_M, _BAR_H_M
        return "par", _PAR_W_M, _PAR_W_M

    # ------------------------------------------------------------ build
    def refresh_layout(self) -> None:
        """(Re)build every static item from the rig + venue. Call after any
        venue/position edit; cheap enough for that (a few dozen items)."""
        self._scene.blockSignals(True)
        self._scene.clear()
        self._grid.clear(); self._bodies.clear(); self._targets.clear()
        self._beams.clear(); self._wedges.clear(); self._captions.clear()
        v = self.venue()
        w_px, l_px = v.width_m * PX_PER_M, v.length_m * PX_PER_M
        self._scene.setSceneRect(-_MARGIN, -_MARGIN - _LEGEND_H,
                                 w_px + 2 * _MARGIN, l_px + 2 * _MARGIN + _LEGEND_H)

        # grid: every foot light, every 5 ft bold
        step = ft_to_m(1.0) * PX_PER_M
        n_x, n_y = int(v.width_m / ft_to_m(1.0)) + 1, int(v.length_m / ft_to_m(1.0)) + 1
        for i in range(n_x + 1):
            x = i * step
            if x > w_px + 0.5:
                break
            pen = QtGui.QPen(_GRID_BOLD if i % 5 == 0 else _GRID_LIGHT, 1)
            self._grid.append(self._scene.addLine(x, 0, x, l_px, pen))
            if i % 5 == 0:
                t = self._scene.addSimpleText(f"{i}′"); t.setBrush(_TEXT); t.setPos(x - 6, l_px + 4)
        for j in range(n_y + 1):
            y = l_px - j * step
            if y < -0.5:
                break
            pen = QtGui.QPen(_GRID_BOLD if j % 5 == 0 else _GRID_LIGHT, 1)
            self._grid.append(self._scene.addLine(0, y, w_px, y, pen))
            if j % 5 == 0:
                t = self._scene.addSimpleText(f"{j}′"); t.setBrush(_TEXT); t.setPos(-_MARGIN + 2, y - 7)
        self._scene.addRect(0, 0, w_px, l_px, QtGui.QPen(_ROOM_EDGE, 2))
        # the wall title goes under the x-axis numbers (not across them); the
        # scene grows to fit it
        booth = self._scene.addSimpleText("DJ BOOTH WALL")
        booth.setBrush(_ROOM_EDGE)
        tick_h = QtGui.QFontMetricsF(booth.font()).height()
        booth.setPos(w_px / 2 - booth.boundingRect().width() / 2, l_px + 4 + tick_h)
        r = self._scene.sceneRect()
        r.setBottom(max(r.bottom(), booth.sceneBoundingRect().bottom() + 2))
        self._scene.setSceneRect(r)
        # the legend wraps onto as many lines as the scene's width needs (one
        # line was cropped on rooms under ~33 ft); the top band grows to fit
        legend = self._scene.addSimpleText(_LEGEND)
        legend.setFont(QtGui.QFont("Arial", 7)); legend.setBrush(_TEXT)
        fm = QtGui.QFontMetricsF(legend.font())
        lines, cur = [], ""
        for part in _LEGEND.split("    "):
            cand = cur + "    " + part if cur else part
            if cur and fm.horizontalAdvance(cand) > r.width() - 4:
                lines.append(cur)
                cand = part
            cur = cand
        legend.setText("\n".join(lines + [cur]))
        extra = legend.boundingRect().height() - (_LEGEND_H - 2)
        if extra > 0:
            r.setTop(r.top() - extra)
            self._scene.setSceneRect(r)
        legend.setPos(r.left() + 2, r.top() + 2)
        self._legend = legend

        movable = (QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                   | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

        # named targets
        ceiling = v.height_m if v.height_m is not None else 3.6
        for name, p in v.targets.items():
            is_ceiling = name.startswith("ceiling") or p.z >= ceiling - 0.05
            size = _CEILING_TARGET_W_M if is_ceiling else _TARGET_W_M
            item = PlanItem(name, "target", name, size, size,
                            shape="ceiling_target" if is_ceiling else "target")
            item.fill = QtGui.QColor(0, 0, 0); item.edge = _TARGET; item.edge_w = 2
            item.setPos(self.to_scene(p.x, p.y))
            item.setZValue(4.5 if is_ceiling else 5)      # the hollow ceiling diamond sits under a floor one
            item.setToolTip(f"{name}\n{m_to_ft(p.x):.1f} ft along the booth wall, "
                            f"{m_to_ft(p.y):.1f} ft out, {m_to_ft(p.z):.1f} ft up")
            if self.editable:
                item.setFlags(movable)
                item.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self._scene.addItem(item)
            self._targets[name] = item
            cap = _Caption(self._scene, item)
            cap.set_text(name, f"{m_to_ft(p.z):.0f}′ up")
            self._captions["target:" + name] = cap

        # fixtures
        for fx in self.rig.fixtures:
            key = self._key(fx)
            shape, w_m, h_m = self._glyph(fx)
            item = PlanItem(key, "fixture", fx.label, w_m, h_m, shape=shape)
            item.facing_deg = fx.facing_deg
            o = self.fixture_origin(fx)
            placed = fx.position is not None
            item.setPos(self.to_scene(o.x, o.y))
            where = (f"{m_to_ft(o.x):.1f} ft along the booth wall, {m_to_ft(o.y):.1f} ft out, "
                     f"{m_to_ft(o.z):.1f} ft up" if placed else "not placed yet -- drag it to where it hangs")
            item.setToolTip(f"{fx.label}\n{fx.type_name} @ DMX {fx.address}\n{where}")
            if self.editable:
                item.setFlags(movable | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
                item.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self._scene.addItem(item)
            self._bodies[key] = item
            if isinstance(fx, MovingHead):
                beam = self._scene.addLine(0, 0, 0, 0, QtGui.QPen(QtGui.QColor(80, 80, 90), 2))
                beam.setZValue(3)
                self._beams[key] = beam
            elif isinstance(fx, Laser):
                wedge = self._scene.addPolygon(QtGui.QPolygonF(), QtGui.QPen(_IDLE, 1),
                                               QtGui.QBrush(QtGui.QColor(60, 60, 70, 40)))
                wedge.setZValue(2)
                self._wedges[key] = wedge
            cap = _Caption(self._scene, item)
            cap.set_text(fx.label, f"@{fx.address} · {m_to_ft(o.z):.0f}′ up" if placed
                         else f"@{fx.address} · not placed yet")
            self._captions[key] = cap
        self._mark_stacks()
        self._scene.blockSignals(False)
        self._layout_dirty = True
        self._fit()
        self.update_from_universe(self.rig.boot_universe())

    @staticmethod
    def _key(fx) -> str:
        return getattr(fx, "slot_id", fx.label)

    def _mark_stacks(self) -> None:
        """Fixtures drawn within ``_STACK_RADIUS_M`` of each other (a par on
        the booth right under a head, say) would hide one another as
        look-alike discs. The highest-mounted one keeps its normal glyph on
        top; each one beneath it becomes a progressively larger dashed
        outline around it, so every member of the pile stays visible and
        clickable. Positions are untouched."""
        entries = [(self._key(fx), self.fixture_origin(fx)) for fx in self.rig.fixtures]
        for key, _o in entries:
            item = self._bodies.get(key)
            if item is not None:
                item.set_glyph_scale(1.0)
                item.setZValue(10)
        grouped: set[str] = set()
        for i, (ki, oi) in enumerate(entries):
            if ki in grouped:
                continue
            group = [(ki, oi)]
            for kj, oj in entries[i + 1:]:
                if kj not in grouped and math.hypot(oi.x - oj.x, oi.y - oj.y) < _STACK_RADIUS_M:
                    group.append((kj, oj))
            if len(group) < 2:
                continue
            group.sort(key=lambda e: -e[1].z)
            for rank, (k, _o) in enumerate(group):
                grouped.add(k)
                item = self._bodies[k]
                item.set_glyph_scale(1.0 + _STACK_STEP * rank)
                item.setZValue(10 - 0.1 * rank)

    def _layout_labels(self) -> None:
        """Keep every caption legible: each starts centred under its body and
        moves to the first free slot around it (``_SLOTS`` order) when it
        would overlap a body, an already-placed caption, the axis numbers or
        the legend. Captions that end up away from their body get a dotted
        leader. Greedy and deterministic (bodies sorted along the booth wall)."""
        self._layout_dirty = False
        v = self.venue()
        w_px, l_px = v.width_m * PX_PER_M, v.length_m * PX_PER_M
        scene_rect = self._scene.sceneRect()
        reserved = [
            QtCore.QRectF(-_MARGIN, l_px + 1, w_px + 2 * _MARGIN, _MARGIN + 40),     # x axis + "DJ BOOTH WALL"
            QtCore.QRectF(-_MARGIN - 2, -_MARGIN - _LEGEND_H, _MARGIN + 1, l_px + 2 * _MARGIN + _LEGEND_H),  # y axis
            self._legend.sceneBoundingRect(),                                                            # legend
        ]
        obstacles = [c.item.sceneBoundingRect() for c in self._captions.values()]
        placed: list[QtCore.QRectF] = []
        order = sorted(self._captions.values(), key=lambda c: (c.item.pos().x(), c.item.pos().y()))
        gap = 4.0
        for cap in order:
            a = cap.item.pos()
            br = cap.item.boundingRect()
            bw, bh = br.width() / 2, br.height() / 2
            lw, lh = cap.size()

            def slot_rect(sx: int, sy: int) -> QtCore.QRectF:
                if sx == 0:
                    x = a.x() - lw / 2
                elif sx > 0:
                    x = a.x() + bw + gap + (sx - 1) * lw * 0.6
                else:
                    x = a.x() - bw - gap - lw - (-sx - 1) * lw * 0.6
                if sy == 0:
                    y = a.y() - lh / 2
                elif sy > 0:
                    y = a.y() + bh + gap + (sy - 1) * (lh + 6)
                else:
                    y = a.y() - bh - gap - lh - (-sy - 1) * (lh + 6)
                return QtCore.QRectF(x, y, lw, lh)

            def free(r: QtCore.QRectF) -> bool:
                if not scene_rect.contains(r):
                    return False
                return not any(r.intersects(o) for o in obstacles + placed + reserved)

            chosen: QtCore.QRectF | None = None
            far = True
            for sx, sy in self._SLOTS:
                r = slot_rect(sx, sy)
                if free(r):
                    chosen = r
                    far = abs(sx) + abs(sy) >= 2
                    break
            if chosen is None:
                # nothing fits in the near slots: widen the search to every
                # slot within a few caption-widths/heights, nearest first,
                # still refusing the bodies, the captions already placed AND
                # the reserved axis / wall-title / legend bands. (A body
                # hugging the booth wall -- the wash bar -- has no room
                # below, and the booth's centre line is crowded above, so
                # the caption steps sideways.) A leader points back.
                far = True
                ring = [(sx, sy) for sx in range(-6, 7) for sy in range(-8, 9)
                        if (sx, sy) not in self._SLOTS and (sx or sy)]
                ring.sort(key=lambda p_: (slot_rect(*p_).center() - a).manhattanLength())
                for sx, sy in ring:
                    r = slot_rect(sx, sy)
                    if free(r):
                        chosen = r
                        break
            if chosen is None:
                # last resort (room smaller than its labels): below, ignoring the bands
                chosen = slot_rect(0, 1)
                for _ in range(40):
                    if not any(chosen.intersects(o) for o in obstacles + placed):
                        break
                    chosen.translate(0, lh + 4)
            cap.place(chosen)
            cap.leader_to(chosen if far else None)
            placed.append(chosen.adjusted(-3, -2, 3, 2))

    def _fit(self) -> None:
        self.fitInView(self._scene.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit()

    # ------------------------------------------------------------ selection / drag
    def _on_selection(self) -> None:
        # the scene being destroyed (tab closed, app exit) deletes the items
        # first and then reports the selection change: nothing to select then
        if not all(Shiboken.isValid(item) for item in self._bodies.values()):
            return
        for key, item in self._bodies.items():
            if item.isSelected():
                self.fixture_selected.emit(key)
                return
        self.fixture_selected.emit("")

    def select(self, slot_id: str) -> None:
        self._scene.blockSignals(True)
        for key, item in self._bodies.items():
            item.setSelected(key == slot_id)
        self._scene.blockSignals(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # where everything sat before this press: only what the drag moved is committed
        self._press_pos = {id(i): i.pos() for i in (*self._bodies.values(), *self._targets.values())}
        super().mousePressEvent(event)

    def _moved(self, item: PlanItem) -> bool:
        before = self._press_pos.get(id(item))
        return item.isSelected() and before is not None and item.pos() != before

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        if not self.editable:
            return
        changed = False
        for key, item in self._bodies.items():
            if self._moved(item):
                changed |= self._commit_fixture_drop(key, item)
        for name, item in self._targets.items():
            if self._moved(item):
                changed |= self._commit_target_drop(name, item)
        self._press_pos = {}
        if changed:
            self.rig.refresh_geometry()
            self._mark_stacks()
            self._layout_labels()
            if self.on_changed is not None:
                self.on_changed()

    def _snap(self, x_m: float, y_m: float) -> tuple[float, float]:
        v = self.venue()
        x_m = min(v.width_m, max(0.0, round(x_m / SNAP_M) * SNAP_M))
        y_m = min(v.length_m, max(0.0, round(y_m / SNAP_M) * SNAP_M))
        return x_m, y_m

    def _commit_fixture_drop(self, key: str, item: PlanItem) -> bool:
        fx = self.rig.fixture(key)
        x_m, y_m = self._snap(*self.from_scene(item.pos()))
        old = fx.position
        z = old.z if old is not None else 1.0
        fx.position = Point3(x_m, y_m, z)
        item.setPos(self.to_scene(x_m, y_m))
        cap = self._captions.get(key)
        if cap is not None:
            cap.set_text(fx.label, f"@{fx.address} · {m_to_ft(z):.0f}′ up")
        self._layout_dirty = True
        return old != fx.position

    def _commit_target_drop(self, name: str, item: PlanItem) -> bool:
        v = self.rig.venue
        if v is None:
            return False
        x_m, y_m = self._snap(*self.from_scene(item.pos()))
        old = v.targets.get(name)
        z = old.z if old is not None else 1.0
        v.targets[name] = Point3(x_m, y_m, z)
        item.setPos(self.to_scene(x_m, y_m))
        self._layout_dirty = True
        return old != v.targets[name]

    def set_fixture_position(self, slot_id: str, p: Point3) -> None:
        """Move a body to a position set from the property panel (no snap)."""
        item = self._bodies.get(slot_id)
        if item is not None:
            item.setPos(self.to_scene(p.x, p.y))
            cap = self._captions.get(slot_id)
            if cap is not None:
                cap.set_text(cap.title.text(), f"@{self.rig.fixture(slot_id).address} · {m_to_ft(p.z):.0f}′ up")
            self._mark_stacks()
            self._layout_labels()

    # ------------------------------------------------------------ live frame
    def update_from_universe(self, uni: bytes) -> None:
        v = self.venue()
        for fx in self.rig.fixtures:
            key = self._key(fx)
            item = self._bodies.get(key)
            if item is None:
                continue
            vs = fx.visual_state(uni)
            b = vs.brightness
            color = QtGui.QColor(int(vs.r * b), int(vs.g * b), int(vs.b * b))
            item.fill = color
            item.edge = QtGui.QColor(vs.r, vs.g, vs.b) if vs.active else _IDLE
            item.edge_w = 2 if vs.active else 1
            item.facing_deg = fx.facing_deg
            item.update()
            origin = self.fixture_origin(fx)
            center = item.pos()

            beam = self._beams.get(key)
            if beam is not None and isinstance(fx, MovingHead):
                az, el = pan_tilt_to_angles(vs.pan, vs.tilt, fx.facing_deg, fx.calibration,
                                            fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
                hit = beam_floor_hit(origin, az, el)
                edge = ray_to_rect_edge((origin.x, origin.y), az, v.width_m, v.length_m)
                if hit is not None and math.hypot(hit.x - origin.x, hit.y - origin.y) <= edge:
                    end = self.to_scene(hit.x, hit.y)
                    pen = QtGui.QPen(color if vs.active else QtGui.QColor(50, 50, 60), 2)
                else:  # aimed up or past the wall: draw to the wall, faded
                    dx, dy = math.sin(math.radians(az)), math.cos(math.radians(az))
                    end = self.to_scene(origin.x + dx * edge, origin.y + dy * edge)
                    faded = QtGui.QColor(color); faded.setAlpha(90)
                    pen = QtGui.QPen(faded if vs.active else QtGui.QColor(40, 40, 50), 1, QtCore.Qt.PenStyle.DashLine)
                beam.setLine(center.x(), center.y(), end.x(), end.y())
                beam.setPen(pen)

            wedge = self._wedges.get(key)
            if wedge is not None and isinstance(fx, Laser):
                # display assumption (UNVERIFIED CH7 0-127): the static offset
                # shifts the fan's centre within its own half-angle.
                centre_az = fx.facing_deg + (fx.h_offset01 - 0.5) * 2 * fx.fan_half_angle_deg
                pts = [center]
                for k in (-1.0, -0.5, 0.0, 0.5, 1.0):
                    az = centre_az + k * fx.fan_half_angle_deg
                    throw = ray_to_rect_edge((origin.x, origin.y), az, v.width_m, v.length_m)
                    dx, dy = math.sin(math.radians(az)), math.cos(math.radians(az))
                    pts.append(self.to_scene(origin.x + dx * throw, origin.y + dy * throw))
                wedge.setPolygon(QtGui.QPolygonF(pts))
                fill = QtGui.QColor(color) if vs.active else QtGui.QColor(60, 60, 70)
                fill.setAlpha(70 if vs.active else 40)
                wedge.setPen(QtGui.QPen(QtGui.QColor(vs.r, vs.g, vs.b) if vs.active else _IDLE, 1))
                wedge.setBrush(QtGui.QBrush(fill))
        if self._layout_dirty:
            self._layout_labels()

    # ------------------------------------------------------------ queries (tests / panel)
    def item_position_m(self, slot_id: str) -> tuple[float, float]:
        return self.from_scene(self._bodies[slot_id].pos())

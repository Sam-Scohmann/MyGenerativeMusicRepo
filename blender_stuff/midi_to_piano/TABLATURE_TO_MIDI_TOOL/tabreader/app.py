"""Tab Reader UI: drop a tablature image, check the detection row by row,
play rows to a MIDI port, stream everything or export a .mid file."""
import os
import sys
import traceback

import cv2
import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from . import drums, midi_file, midi_out, timing, vision

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
TUNING_CHOICES = [("Auto (by string count)", None)] + [
    (timing.TUNING_NAMES[n], timing.TUNINGS[n]) for n in sorted(timing.TUNINGS)]

STYLE = """
QMainWindow, QWidget#central { background: #1e1f22; }
QLabel { color: #d8d8d8; }
QLabel#hint { color: #8a8d93; font-size: 15px; }
QLabel#summary { color: #b9bcc2; }
QFrame#row { background: #2a2c30; border: 1px solid #3a3d42; border-radius: 6px; }
QFrame#row[active="true"] { border: 1px solid #4fa3ff; background: #263241; }
QFrame#drop { border: 2px dashed #4a4e55; border-radius: 10px; background: #24262a; }
QPushButton { background: #3a3d42; color: #e6e6e6; border: 1px solid #4a4e55;
              border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { background: #45494f; }
QPushButton:disabled { color: #6b6f75; }
QPushButton#primary { background: #2f6fd0; border-color: #3b7ee0; }
QPushButton#primary:hover { background: #3b7ee0; }
QPushButton#play { min-width: 34px; max-width: 34px; font-size: 14px; padding: 6px 0; }
QComboBox, QSpinBox { background: #2a2c30; color: #e6e6e6; border: 1px solid #4a4e55;
                      border-radius: 4px; padding: 3px 6px; }
QPlainTextEdit { background: #24262a; color: #e0b050; border: 1px solid #3a3d42; }
QCheckBox { color: #d8d8d8; }
QFrame#drumpanel { background: #25272b; border: 1px solid #3a3d42; border-radius: 6px; }
QLabel#panelTitle { font-weight: bold; font-size: 13px; }
QLabel#headIcon { background: #e2e2e2; border-radius: 3px; }
QLabel#muted { color: #8a8d93; }
QPushButton#mini { min-width: 26px; max-width: 26px; padding: 3px 0; }
QScrollArea { border: none; }
"""


def to_pixmap(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


# ---------------------------------------------------------------- background work

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class AnalyzeJob(QRunnable):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.signals = _Signals()

    def run(self):
        try:
            data = np.fromfile(self.path, np.uint8)      # handles non-ASCII paths
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError("could not read image")
            page = vision.analyze(img)
            if not page.staffs:
                raise RuntimeError("no tab staffs found in this image")
            overlay = vision.draw_overlay(img, page)
            self.signals.done.emit((self.path, img, overlay, page))
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class PlayerBridge(QObject):
    position = Signal(object, float)   # (player, beat)
    finished = Signal(object)          # player


# ---------------------------------------------------------------- row widget

class RowImage(QLabel):
    """Cropped staff image with a playhead line."""

    def __init__(self):
        super().__init__()
        self.base = None
        self.playhead = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def set_image(self, pix):
        self.base = pix
        self.setMinimumWidth(min(pix.width(), 400))
        self._refresh()

    def set_playhead(self, x):
        self.playhead = x
        self._refresh()

    def _refresh(self):
        if self.base is None:
            return
        avail = max(200, self.width())
        pix = self.base
        if pix.width() > avail:
            pix = pix.scaledToWidth(avail, Qt.SmoothTransformation)
        if self.height() != pix.height():
            self.setFixedHeight(pix.height())
        if self.playhead is not None:
            pix = pix.copy()
            p = QPainter(pix)
            k = pix.width() / self.base.width()
            x = int(self.playhead * k)
            p.setPen(QPen(QColor(255, 80, 60, 220), 2))
            p.drawLine(x, 0, x, pix.height())
            p.end()
        super().setPixmap(pix)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()


class RowWidget(QFrame):
    def __init__(self, index, title, status, ok, play_cb):
        super().__init__()
        self.setObjectName("row")
        self.index = index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        left = QVBoxLayout()
        self.btn = QPushButton("▶")
        self.btn.setObjectName("play")
        self.btn.setToolTip("Play this row to the MIDI port")
        self.btn.clicked.connect(lambda: play_cb(self.index))
        left.addWidget(self.btn)
        left.addStretch(1)
        lay.addLayout(left)
        right = QVBoxLayout()
        head = QLabel(f"<b>{title}</b>  <span style='color:{'#6fbf73' if ok else '#e0b050'}'>{status}</span>")
        right.addWidget(head)
        self.image = RowImage()
        right.addWidget(self.image)
        lay.addLayout(right, 1)

    def set_active(self, on):
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.btn.setText("■" if on else "▶")


# ---------------------------------------------------------------- drum map panel

class DrumMapPanel(QFrame):
    """One row per notehead type found in the image: example crop, where it sits,
    how many, and the General MIDI drum it plays."""

    def __init__(self, on_change, on_audition):
        super().__init__()
        self.setObjectName("drumpanel")
        self.setFixedWidth(370)
        self.on_change, self.on_audition = on_change, on_audition
        lay = QVBoxLayout(self)
        title = QLabel("Drum map")
        title.setObjectName("panelTitle")
        lay.addWidget(title)
        sub = QLabel("Each notehead shape + staff position is one kit piece.\n"
                     "Pick the MIDI note it should play (saved for next time).")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(8)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.grid_host)
        lay.addWidget(sc, 1)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        lay.addWidget(reset)
        self.map = drums.load_map()
        self.keys = []

    def populate(self, img, page):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        found = {}
        for st in page.staffs:
            for bar in st.bars:
                for h in bar.hits:
                    found.setdefault(h.key, []).append(h)
        # top of the staff first, like reading a drum legend
        self.keys = sorted(found, key=lambda k: (int(k.split("@")[1]), k))
        notes = sorted(drums.GM_DRUMS)
        for r, k in enumerate(self.keys):
            hits = found[k]
            ex = next((h for h in hits if not h.ghost), hits[0])
            x, y, w, h = ex.box
            s = int(max(w, h) * 0.9)
            crop = img[max(0, y - s):y + h + s, max(0, x - s):x + w + s]
            icon = QLabel()
            icon.setObjectName("headIcon")
            icon.setPixmap(to_pixmap(np.ascontiguousarray(crop)).scaledToHeight(44, Qt.SmoothTransformation))
            icon.setFixedSize(52, 48)
            icon.setAlignment(Qt.AlignCenter)
            shape, pos = k.split("@")
            ghosts = sum(1 for hh in hits if hh.ghost)
            extra = f" · {ghosts} ghost" if ghosts else ""
            desc = QLabel(f"<b>{drums.SHAPE_SYMBOL.get(shape, shape)}</b> {drums.describe(int(pos))}"
                          f"<br><span style='color:#8a8d93'>{len(hits)} hits{extra} · {k}</span>")
            combo = QComboBox()
            for n in notes:
                combo.addItem(f"{n}  {drums.GM_DRUMS[n]}", n)
            cur = drums.note_for(self.map, k)
            if cur not in drums.GM_DRUMS:
                combo.addItem(f"{cur}", cur)
            combo.setCurrentIndex(combo.findData(cur))
            combo.currentIndexChanged.connect(lambda _i, k=k, c=combo: self._changed(k, c.currentData()))
            play = QPushButton("▶")
            play.setObjectName("mini")
            play.setToolTip("Audition this drum on the MIDI port")
            play.clicked.connect(lambda _=False, c=combo: self.on_audition(c.currentData()))
            self.grid.addWidget(icon, r * 2, 0, 2, 1)
            self.grid.addWidget(desc, r * 2, 1, 1, 2)
            self.grid.addWidget(combo, r * 2 + 1, 1)
            self.grid.addWidget(play, r * 2 + 1, 2)
        self.grid.setRowStretch(len(self.keys) * 2, 1)

    def _changed(self, k, note):
        self.map[k] = int(note)
        drums.save_map(self.map)
        self.on_change()

    def _reset(self):
        self.map = dict(drums.DEFAULT_MAP)
        drums.save_map(self.map)
        self.on_change(repopulate=True)


# ---------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, initial=None):
        super().__init__()
        self.setWindowTitle("Tab Reader — tablature image to MIDI")
        self.resize(1500, 950)
        self.setAcceptDrops(True)
        self.pool = QThreadPool.globalInstance()
        self.img = self.overlay = self.page = self.song = None
        self.path = None
        self.rows = []
        self.out = None
        self.player = None
        self.playing_row = None   # row index, or "all"
        self.bridge = PlayerBridge()
        self.bridge.position.connect(self._on_position)
        self.bridge.finished.connect(self._on_finished)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- top bar
        top = QHBoxLayout()
        open_btn = QPushButton("Open image…")
        open_btn.clicked.connect(self._open_dialog)
        top.addWidget(open_btn)
        top.addSpacing(12)
        self.tuning_label = QLabel("Tuning")
        top.addWidget(self.tuning_label)
        self.tuning = QComboBox()
        for name, _ in TUNING_CHOICES:
            self.tuning.addItem(name)
        self.tuning.currentIndexChanged.connect(self._rebuild_song)
        top.addWidget(self.tuning)
        self.transpose_label = QLabel("Transpose")
        top.addWidget(self.transpose_label)
        self.transpose = QSpinBox()
        self.transpose.setRange(-48, 48)
        self.transpose.valueChanged.connect(self._rebuild_song)
        top.addWidget(self.transpose)
        top.addWidget(QLabel("Tempo"))
        self.tempo = QSpinBox()
        self.tempo.setRange(20, 400)
        self.tempo.setValue(120)
        self.tempo.setSuffix(" bpm")
        self.tempo.valueChanged.connect(self._rebuild_song)
        top.addWidget(self.tempo)
        self.strum_label = QLabel("Strum spread")
        top.addWidget(self.strum_label)
        self.strum_ms = QSpinBox()
        self.strum_ms.setRange(0, 80)
        self.strum_ms.setValue(timing.STRUM_MS)
        self.strum_ms.setSuffix(" ms/string")
        self.strum_ms.setToolTip("Delay between strings of a strummed chord. The arrows in the tab set\n"
                                 "the order: arrowhead at the top = low strings first (downstroke).")
        self.strum_ms.valueChanged.connect(self._rebuild_song)
        top.addWidget(self.strum_ms)
        for wdg in (self.strum_label, self.strum_ms):
            wdg.hide()
        self.show_det = QCheckBox("Show detections")
        self.show_det.toggled.connect(self._refresh_images)
        top.addWidget(self.show_det)
        top.addStretch(1)
        top.addWidget(QLabel("MIDI port"))
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(220)
        self.port.setToolTip("Type a port name (partial match is fine) or pick one")
        top.addWidget(self.port)
        refresh = QPushButton("⟳")
        refresh.setToolTip("Refresh MIDI ports")
        refresh.clicked.connect(self._refresh_ports)
        top.addWidget(refresh)
        top.addWidget(QLabel("Channel"))
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        top.addWidget(self.channel)
        root.addLayout(top)

        self.summary = QLabel("")
        self.summary.setObjectName("summary")
        root.addWidget(self.summary)

        # --- rows / drop zone
        self.drop = QFrame()
        self.drop.setObjectName("drop")
        dl = QVBoxLayout(self.drop)
        hint = QLabel("Drop a tablature image here\n(bass / guitar tab screenshot — PNG or JPG)")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        dl.addWidget(hint)
        root.addWidget(self.drop, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.rows_host = QWidget()
        self.rows_host.setObjectName("central")
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.rows_host)
        self.scroll.hide()
        mid = QHBoxLayout()
        mid.addWidget(self.scroll, 1)
        self.drum_panel = DrumMapPanel(self._drum_map_changed, self._audition)
        self.drum_panel.hide()
        mid.addWidget(self.drum_panel)
        root.addLayout(mid, 1)

        self.warn_box = QPlainTextEdit()
        self.warn_box.setReadOnly(True)
        self.warn_box.setMaximumHeight(90)
        self.warn_box.hide()
        root.addWidget(self.warn_box)

        # --- bottom bar
        bottom = QHBoxLayout()
        self.status = QLabel("")
        bottom.addWidget(self.status, 1)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        bottom.addWidget(self.stop_btn)
        self.stream_btn = QPushButton("Stream everything to MIDI")
        self.stream_btn.setObjectName("primary")
        self.stream_btn.clicked.connect(self.stream_all)
        bottom.addWidget(self.stream_btn)
        self.export_btn = QPushButton("Export MIDI file…")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self.export)
        bottom.addWidget(self.export_btn)
        root.addLayout(bottom)
        self._set_loaded(False)

        self._refresh_ports()
        if initial:
            QTimer.singleShot(0, lambda: self.load(initial))

    # ------------------------------------------------------------ loading

    def _set_loaded(self, on):
        self.stream_btn.setEnabled(on)
        self.export_btn.setEnabled(on)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(IMAGE_EXT)
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(IMAGE_EXT):
                self.load(p)
                break

    def _open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open tablature image", self.path or "",
                                           "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        if p:
            self.load(p)

    def load(self, path):
        self.stop()
        self.status.setText(f"Reading {os.path.basename(path)} …")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        job = AnalyzeJob(path)
        job.signals.done.connect(self._loaded)
        job.signals.failed.connect(self._load_failed)
        self.pool.start(job)

    def _load_failed(self, msg):
        QApplication.restoreOverrideCursor()
        self.status.setText("Could not read that image.")
        QMessageBox.warning(self, "Tab Reader", msg)

    def _loaded(self, result):
        QApplication.restoreOverrideCursor()
        self.path, self.img, self.overlay, self.page = result
        is_drums = self.page.kind == "drums"
        for wdg in (self.tuning_label, self.tuning, self.transpose_label, self.transpose):
            wdg.setVisible(not is_drums)
        has_strums = any(b.strums for st in self.page.staffs for b in st.bars)
        for wdg in (self.strum_label, self.strum_ms):
            wdg.setVisible(has_strums)
        self.drum_panel.setVisible(is_drums)
        if is_drums:
            self.drum_panel.populate(self.img, self.page)
            # General MIDI drums live on channel 10
            if self.channel.value() == 1:
                self.channel.setValue(10)
        elif self.channel.value() == 10:
            self.channel.setValue(1)
        self.tempo.blockSignals(True)
        self.tempo.setValue(self.page.tempo or 120)
        self.tempo.blockSignals(False)
        self._rebuild_song()
        self.drop.hide()
        self.scroll.show()
        self._set_loaded(True)
        self.status.setText(f"Loaded {os.path.basename(self.path)}")

    def _drum_map_changed(self, repopulate=False):
        if repopulate:
            self.drum_panel.populate(self.img, self.page)
        pos = self.scroll.verticalScrollBar().value()
        self._rebuild_song()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(pos))

    def _audition(self, note):
        try:
            out = self._ensure_out()
        except RuntimeError as e:
            QMessageBox.warning(self, "MIDI", str(e))
            return
        ch = self.channel.value() - 1
        out.note_on(ch, int(note), drums.VEL_NORMAL)
        QTimer.singleShot(250, lambda: out.note_off(ch, int(note)))

    def _rebuild_song(self):
        if not self.page:
            return
        self.stop()
        tuning = TUNING_CHOICES[self.tuning.currentIndex()][1]
        self.song = timing.build_song(self.page, tuning=tuning, transpose=self.transpose.value(),
                                      tempo=self.tempo.value(), drum_map=self.drum_panel.map,
                                      strum_ms=self.strum_ms.value())
        self._build_rows()
        p, s = self.page, self.song
        strings = sorted({st.n_strings for st in p.staffs})
        bad = [b for b in s.bars if not b.ok]
        fixed = [b for b in s.bars if b.status == "repaired"]
        if p.kind == "drums":
            layout = f"drum notation, {len({n.drum for n in s.notes})} kit pieces"
        else:
            layout = f"{'/'.join(map(str, strings))} strings"
            arrows = [t for st in p.staffs for b in st.bars for t in b.strums]
            if arrows:
                down = sum(t.direction == "down" for t in arrows)
                layout += f" · {len(arrows)} strums ({down} ↓low→high, {len(arrows) - down} ↑high→low)"
            dead = sum(n.dead for n in s.notes)
            if dead:
                layout += f" · {dead} muted"
        tempo_src = (f"image says {p.tempo}" if p.tempo and p.tempo != s.tempo else
                     "read from image" if p.tempo else "not found in image")
        self.summary.setText(
            f"<b>{os.path.basename(self.path)}</b> — {len(p.staffs)} rows, {len(s.bars)} bars, "
            f"{len(s.notes)} notes · {layout} · "
            f"{p.time_sig[0]}/{p.time_sig[1]} · tempo {s.tempo:g} bpm ({tempo_src}) · "
            f"{len(s.bars) - len(bad)}/{len(s.bars)} bars rhythm-checked OK"
            + (f" ({len(fixed)} with inferred rests/lengths)" if fixed else ""))
        if s.warnings:
            self.warn_box.setPlainText("\n".join(s.warnings))
            self.warn_box.show()
        else:
            self.warn_box.hide()

    def _build_rows(self):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for i, st in enumerate(self.page.staffs):
            bars = [b for b in self.song.bars if b.staff == st.index]
            if not bars:
                continue
            bad = [b for b in bars if not b.ok]
            fixed = [b for b in bars if b.status == "repaired"]
            n_notes = sum(1 for n in self.song.notes if n.staff == st.index)
            title = f"Row {i + 1}  ·  bars {bars[0].number}–{bars[-1].number}"
            what = "hits" if self.song.kind == "drums" else "notes"
            status = (f"{n_notes} {what} ✓" if not bad else
                      f"{n_notes} {what} · ⚠ rhythm off in bar {', '.join(str(b.number) for b in bad)}")
            if fixed:
                status += f"  · inferred rests/lengths in bar {', '.join(str(b.number) for b in fixed)}"
            row = RowWidget(i, title, status, not bad, self.toggle_row)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self.rows.append(row)
        self._refresh_images()

    def _refresh_images(self):
        if self.img is None:
            return
        src = self.overlay if self.show_det.isChecked() else self.img
        for row in self.rows:
            x, y, w, h = self.page.staffs[row.index].crop
            row.image.set_image(to_pixmap(np.ascontiguousarray(src[y:y + h, x:x + w])))

    # ------------------------------------------------------------ MIDI

    def _refresh_ports(self):
        cur = self.port.currentText()
        self.port.clear()
        names = midi_out.output_names()
        self.port.addItems(names)
        if cur:
            self.port.setEditText(cur)
        elif names:
            prefer = next((n for n in names if "loop" in n.lower()),
                          next((n for n in names if not n.startswith("Microsoft")), names[0]))
            self.port.setEditText(prefer)

    def _ensure_out(self):
        name = self.port.currentText().strip()
        if not name:
            raise RuntimeError("Enter a MIDI port name first.")
        if self.out and self.out.name == midi_out.resolve(name)[1]:
            return self.out
        if self.out:
            self.out.close()
            self.out = None
        self.out = midi_out.MidiOut(name)
        return self.out

    def _play(self, start, end, which):
        self.stop()
        try:
            out = self._ensure_out()
        except RuntimeError as e:
            QMessageBox.warning(self, "MIDI", str(e))
            return
        self.playing_row = which
        player = midi_out.Player(out, self.channel.value() - 1, self.song.tempo,
                                 self.song.notes, start, end)
        player.on_position = lambda beat, p=player: self.bridge.position.emit(p, beat)
        player.on_done = lambda p=player: self.bridge.finished.emit(p)
        self.player = player.start()
        self.stop_btn.setEnabled(True)
        label = "everything" if which == "all" else f"row {which + 1}"
        self.status.setText(f"Streaming {label} → {out.name} (ch {self.channel.value()})")
        if which != "all":
            self.rows[which].set_active(True)

    def toggle_row(self, i):
        if self.playing_row == i:
            self.stop()
            return
        a, b = self.song.staff_range(self.page.staffs[i].index)
        self._play(a, b, i)

    def stream_all(self):
        if self.playing_row == "all":
            self.stop()
            return
        self._play(0.0, self.song.length, "all")

    def stop(self):
        if self.player:
            self.player.stop()
            self.player = None
        for r in self.rows:
            r.set_active(False)
            r.image.set_playhead(None)
        self.playing_row = None
        self.stop_btn.setEnabled(False)

    def _on_position(self, player, beat):
        if player is not self.player or not self.song:
            return
        bar = next((b for b in reversed(self.song.bars) if b.start <= beat), None)
        if bar is None:
            return
        for r in self.rows:
            st = self.page.staffs[r.index].index
            if st == bar.staff:
                if self.playing_row == "all" and r.property("active") != "true":
                    for o in self.rows:
                        o.set_active(False)
                        o.image.set_playhead(None)
                    r.set_active(True)
                    self.scroll.ensureWidgetVisible(r, 0, 60)
                x = self.song.beat_to_x(st, beat)
                if x is not None:
                    r.image.set_playhead(x - self.page.staffs[r.index].crop[0])

    def _on_finished(self, player):
        if player is self.player:
            self.stop()
            self.status.setText("Done.")

    # ------------------------------------------------------------ export

    def export(self):
        if not self.song:
            return
        base = os.path.splitext(self.path)[0] + ".mid"
        p, _ = QFileDialog.getSaveFileName(self, "Export MIDI file", base, "MIDI (*.mid)")
        if not p:
            return
        name = os.path.splitext(os.path.basename(p))[0]
        midi_file.write(p, self.song, channel=self.channel.value() - 1, track_name=name)
        self.status.setText(f"Exported {len(self.song.notes)} notes → {p}")

    def closeEvent(self, e):
        self.stop()
        if self.out:
            self.out.close()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    initial = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else None
    w = MainWindow(initial)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

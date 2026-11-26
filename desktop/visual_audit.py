"""Render each native QML pet expression in isolation, without emitting backend events."""
import argparse
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


STATES = ("IDLE", "LISTENING", "THINKING", "EXECUTING", "VERIFYING", "SPEAKING",
          "SUCCESS", "CLARIFYING", "REMINDER", "MUTED", "ERROR", "OFFLINE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args()
    app = QApplication([])
    pet = QQuickWidget()
    pet.setClearColor(Qt.GlobalColor.transparent)
    pet.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    pet.resize(100, 100)
    pet.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("Pet.qml").resolve())))
    root = pet.rootObject()
    if root is None:
        raise RuntimeError(str(pet.errors()))
    pet.show()
    root.setProperty("onDesktop", True)
    root.setProperty("level", .6)
    sheet = QImage(560, 390, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#292724"))
    painter = QPainter(sheet)
    painter.setPen(QColor("#ede9db"))
    rows = []
    for index, state in enumerate(STATES):
        root.setProperty("mood", state)
        QTest.qWait(210)
        pixels = pet.grabFramebuffer().convertToFormat(QImage.Format.Format_RGBA8888)
        if pixels.isNull():
            raise RuntimeError(f"No QML frame for {state}")
        x, y = 140 * (index % 4), 130 * (index // 4)
        painter.drawImage(QRect(x + 20, y, 100, 100), pixels)
        painter.drawText(QRect(x, y + 99, 140, 25), Qt.AlignmentFlag.AlignCenter, state)
        alpha = pixels.pixelColor(pixels.width() // 2, pixels.height() // 2).alpha()
        rows.append({"mood": state, "frame_sha256": hashlib.sha256(bytes(pixels.constBits())).hexdigest(),
                     "visible_center": alpha > 0, "state_applied": root.property("mood") == state})
    painter.end()
    if not sheet.save(str(args.image)):
        raise RuntimeError("Could not save native expression sheet")
    args.results.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf8")
    pet.close()
    app.processEvents()
    print(f"{sum(r['visible_center'] and r['state_applied'] for r in rows)}/{len(rows)} rendered states")


if __name__ == "__main__":
    main()

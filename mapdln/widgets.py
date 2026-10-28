"""Shared desktop controls."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QSizePolicy, QStyledItemDelegate


class FlatCombo(QComboBox):
    def __init__(self):
        super().__init__()
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Use a scrolling list rather than a menu aligned to the selected row.
        self.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.setMaxVisibleItems(10)
        self.view().setSpacing(3)
        popup = self.view().window()
        popup.setObjectName('comboPopup')
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def showPopup(self):
        width = max(self.width(), max((self.fontMetrics().horizontalAdvance(self.itemText(i))
                                      for i in range(self.count())), default=0) + 48)
        self.view().setMinimumWidth(width)
        # Qt's animation captures the old popup position before we can anchor it.
        animate = QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        try:
            super().showPopup()
        finally:
            QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, animate)
        popup = self.view().window()
        if not self.count():
            return
        anchor = self.mapToGlobal(QPoint(0, self.height()))
        available = self.screen().availableGeometry()
        below = available.bottom() - anchor.y() + 1
        if below > 0:
            # Preserve the control's bottom edge when the list needs scrolling.
            popup.resize(min(popup.width(), available.width()), min(popup.height(), below))
            self.view().scrollTo(self.view().currentIndex())
            popup.move(QPoint(max(available.left(), min(anchor.x(), available.right() - popup.width() + 1)),
                              anchor.y()))

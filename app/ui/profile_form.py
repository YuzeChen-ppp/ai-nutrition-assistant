"""个人资料表单（首次启动的引导页与设置页共用）。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.database.models import UserProfile
from app.i18n import tr
from app.nutrition.targets import (
    ACTIVITY_LABEL_KEYS,
    ACTIVITY_LEVELS,
    GOAL_LABEL_KEYS,
    GOALS,
    SEX_LABEL_KEYS,
    SEX_OPTIONS,
    calculate_targets,
)
from app.ui.widgets import Card, SectionTitle, muted

__all__ = ["ProfileForm", "ProfileSetupPage", "preview_text"]


class ProfileForm(Card):
    """年龄 / 性别 / 身高 / 体重 / 活动水平 / 目标。"""

    changed = Signal()

    def __init__(self, *, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(title if title is not None else tr("profile.card_title"), parent=parent)
        body = self.body()

        form = QFormLayout()
        form.setLabelAlignment(form.labelAlignment())

        self.age_spin = QSpinBox()
        self.age_spin.setRange(10, 120)
        self.age_spin.setValue(25)
        self.age_spin.setSuffix(" 岁")
        form.addRow(tr("profile.age"), self.age_spin)

        self.sex_combo = QComboBox()
        for value in SEX_OPTIONS:
            self.sex_combo.addItem(tr(SEX_LABEL_KEYS[value]), value)
        form.addRow(tr("profile.sex"), self.sex_combo)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(100.0, 250.0)
        self.height_spin.setDecimals(1)
        self.height_spin.setValue(170.0)
        self.height_spin.setSuffix(" cm")
        form.addRow(tr("profile.height"), self.height_spin)

        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(25.0, 300.0)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setValue(65.0)
        self.weight_spin.setSuffix(" kg")
        form.addRow(tr("profile.weight"), self.weight_spin)

        self.activity_combo = QComboBox()
        for value in ACTIVITY_LEVELS:
            self.activity_combo.addItem(tr(ACTIVITY_LABEL_KEYS[value]), value)
        form.addRow(tr("profile.activity"), self.activity_combo)

        self.goal_combo = QComboBox()
        for value in GOALS:
            self.goal_combo.addItem(tr(GOAL_LABEL_KEYS[value]), value)
        form.addRow(tr("profile.goal"), self.goal_combo)

        body.addLayout(form)

        self.targets_label = QLabel("")
        self.targets_label.setWordWrap(True)
        self.targets_label.setObjectName("Muted")
        body.addWidget(self.targets_label)
        body.addWidget(muted(tr("profile.disclaimer")))

        for widget in self._inputs():
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self._on_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_changed)
        self._update_preview()

    def _inputs(self) -> list[QWidget]:
        return [
            self.age_spin,
            self.sex_combo,
            self.height_spin,
            self.weight_spin,
            self.activity_combo,
            self.goal_combo,
        ]

    def _on_changed(self, *_: object) -> None:
        self._update_preview()
        self.changed.emit()

    def _update_preview(self) -> None:
        targets = self.profile().calculate_targets()
        energy = tr(
            "profile.preview_energy",
            bmr=f"{targets.bmr:,.0f}",
            tdee=f"{targets.tdee:,.0f}",
            target=f"{targets.calorie_target:,.0f}",
        )
        macros = tr(
            "profile.preview_macros",
            protein=f"{targets.protein_g:,.0f}",
            carbs=f"{targets.carbs_g:,.0f}",
            fat=f"{targets.fat_g:,.0f}",
            fibre=f"{targets.fibre_g:,.0f}",
        )
        self.targets_label.setText(f"{energy}\n{macros}")

    def profile(self) -> UserProfile:
        return UserProfile(
            age=int(self.age_spin.value()),
            sex=str(self.sex_combo.currentData()),
            height_cm=float(self.height_spin.value()),
            weight_kg=float(self.weight_spin.value()),
            activity_level=str(self.activity_combo.currentData()),
            goal=str(self.goal_combo.currentData()),
        )

    def set_profile(self, profile: UserProfile | None) -> None:
        if profile is None:
            return
        self.age_spin.setValue(int(profile.age))
        for combo, value in (
            (self.sex_combo, profile.sex),
            (self.activity_combo, profile.activity_level),
            (self.goal_combo, profile.goal),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(max(0, index))
        self.height_spin.setValue(float(profile.height_cm))
        self.weight_spin.setValue(float(profile.weight_kg))
        self._update_preview()


class ProfileSetupPage(QWidget):
    """首次启动：设置个人资料后才会进入主界面。"""

    saved = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        heading = SectionTitle(tr("profile.setup_title"))
        heading.setStyleSheet("font-size: 20px;")
        layout.addWidget(heading)
        layout.addWidget(muted(tr("profile.setup_hint")))

        self.form = ProfileForm()
        layout.addWidget(self.form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton(tr("profile.save"))
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.saved.emit(self.form.profile()))
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def set_profile(self, profile: UserProfile | None) -> None:
        self.form.set_profile(profile)


def preview_text(profile: UserProfile) -> str:
    """简要文本，例如「2,200 kcal/天」。"""
    targets = calculate_targets(
        age=profile.age,
        sex=profile.sex,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        activity_level=profile.activity_level,
        goal=profile.goal,
    )
    return f"{targets.calorie_target:,.0f} kcal"

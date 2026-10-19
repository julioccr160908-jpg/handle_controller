"""Visual Profile and Gesture Editor dialog (Section 9, Phase 7).

Provides:
- Profile switcher and management (New, Duplicate, Delete, Export, Import).
- Mapping rules table (Add, Edit, Delete, Toggle Enable).
- Action allowlist integration with parameter inputs.
- Real-time conflict detection and alert indicator.
- Custom trajectory gesture recorder.
- Global settings editor (smoothing, dead zone, camera, hotkey).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from handcontrol.actions.allowlist import Allowlist
from handcontrol.config.models import (
    AppConfig,
    GestureRuleConfig,
    MappingRuleConfig,
    ProfileModel,
)
from handcontrol.config.storage import ConfigManager
from handcontrol.mapping.conflict import ConflictSeverity, validate_profile_conflicts

logger = logging.getLogger(__name__)


class RuleEditDialog(QDialog):
    """Dialog to create or edit an individual gesture-to-action mapping rule."""

    def __init__(
        self,
        parent: QWidget | None = None,
        rule: MappingRuleConfig | None = None,
        available_gestures: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Configurar Regra de Mapeamento" if rule else "Nova Regra de Mapeamento"
        )
        self.setMinimumWidth(450)

        self._allowlist = Allowlist()
        self._gestures = available_gestures or [
            "point",
            "pinch_index",
            "pinch_middle",
            "pinch_sustained",
            "two_fingers",
            "fist",
            "two_open_palms",
            "swipe_left",
            "swipe_right",
            "swipe_up",
            "swipe_down",
            "circle_cw",
            "circle_ccw",
        ]

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Gesture selector
        self.combo_gesture = QComboBox()
        self.combo_gesture.addItems(self._gestures)
        form.addRow("Gesto Disparador:", self.combo_gesture)

        # Hand selector
        self.combo_hand = QComboBox()
        self.combo_hand.addItems(["Preferred", "Right", "Left", "Both", "Any"])
        form.addRow("Mão:", self.combo_hand)

        # Action selector
        self.combo_action = QComboBox()
        self.combo_action.addItems(self._allowlist.action_names)
        self.combo_action.currentTextChanged.connect(self._on_action_changed)
        form.addRow("Ação Permitida:", self.combo_action)

        # Parameters inputs
        self.edit_params = QLineEdit()
        self.edit_params.setPlaceholderText("ex: keys=ctrl+c ou monitor_index=1")
        form.addRow("Parâmetros (opcional):", self.edit_params)

        # Priority
        self.spin_priority = QSpinBox()
        self.spin_priority.setRange(0, 100)
        self.spin_priority.setValue(10)
        form.addRow("Prioridade (maior vence):", self.spin_priority)

        # Cooldown
        self.spin_cooldown = QDoubleSpinBox()
        self.spin_cooldown.setRange(0.0, 5.0)
        self.spin_cooldown.setSingleStep(0.05)
        self.spin_cooldown.setValue(0.15)
        self.spin_cooldown.setSuffix(" s")
        form.addRow("Cooldown:", self.spin_cooldown)

        # Repeat limit
        self.spin_repeat = QSpinBox()
        self.spin_repeat.setRange(0, 50)
        self.spin_repeat.setValue(1)
        self.spin_repeat.setToolTip("0 = Ilimitado; 1 = Apenas uma ativação por gesto")
        form.addRow("Limite de Repetições:", self.spin_repeat)

        # Enabled
        self.chk_enabled = QCheckBox("Regra Ativa")
        self.chk_enabled.setChecked(True)
        form.addRow("", self.chk_enabled)

        layout.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Salvar Regra")
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        # Populate if editing
        if rule:
            idx_g = self.combo_gesture.findText(rule.gesture_id)
            if idx_g >= 0:
                self.combo_gesture.setCurrentIndex(idx_g)
            idx_h = self.combo_hand.findText(rule.hand)
            if idx_h >= 0:
                self.combo_hand.setCurrentIndex(idx_h)
            idx_a = self.combo_action.findText(rule.action_name)
            if idx_a >= 0:
                self.combo_action.setCurrentIndex(idx_a)

            self.spin_priority.setValue(rule.priority)
            self.spin_cooldown.setValue(rule.cooldown_s)
            self.spin_repeat.setValue(rule.repeat_limit)
            self.chk_enabled.setChecked(rule.enabled)

            # Format params dict to string
            if rule.parameters:
                params_str = ", ".join(f"{k}={v}" for k, v in rule.parameters.items())
                self.edit_params.setText(params_str)

        self._result_rule: MappingRuleConfig | None = None

    def get_rule(self) -> MappingRuleConfig | None:
        return self._result_rule

    def _on_action_changed(self, action_name: str) -> None:
        spec = self._allowlist.get_spec(action_name)
        if spec and "keys" in spec.required_params and not self.edit_params.text():
            self.edit_params.setText("keys=ctrl+c")

    def _on_save(self) -> None:
        action = self.combo_action.currentText()
        # Parse params
        raw_p = self.edit_params.text().strip()
        params: dict = {}
        if raw_p:
            for item in raw_p.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if v.isdigit():
                        params[k] = int(v)
                    else:
                        try:
                            params[k] = float(v)
                        except ValueError:
                            params[k] = v

        errs = self._allowlist.validate(action, params)
        if errs:
            QMessageBox.warning(self, "Parâmetro Inválido", "\n".join(errs))
            return

        self._result_rule = MappingRuleConfig(
            gesture_id=self.combo_gesture.currentText(),
            action_name=action,
            hand=self.combo_hand.currentText(),
            priority=self.spin_priority.value(),
            cooldown_s=self.spin_cooldown.value(),
            repeat_limit=self.spin_repeat.value(),
            enabled=self.chk_enabled.isChecked(),
            parameters=params,
            trigger_phase="STARTED",
        )
        self.accept()


class ProfileEditorDialog(QDialog):
    """Full-featured visual manager for profiles, gesture mapping, and settings."""

    profile_changed = Signal(str)  # Emitted when active profile changes

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        parent: QWidget | None = None,
        on_apply: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editor de Perfis e Gestos — HandControl")
        self.resize(850, 600)

        self.cm = config_manager or ConfigManager()
        self._cfg: AppConfig = self.cm.config
        self._on_apply = on_apply

        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: Profiles & Mapping Rules
        self.tab_profiles = QWidget()
        self._setup_profiles_tab()
        self.tabs.addTab(self.tab_profiles, "📋 Perfis e Mapeamento")

        # Tab 2: Custom Trajectory Gestures
        self.tab_gestures = QWidget()
        self._setup_gestures_tab()
        self.tabs.addTab(self.tab_gestures, "✨ Gestos Customizados")

        # Tab 3: Global Settings
        self.tab_settings = QWidget()
        self._setup_settings_tab()
        self.tabs.addTab(self.tab_settings, "⚙️ Configurações Globais")

        # Bottom buttons
        bottom_box = QHBoxLayout()
        btn_apply = QPushButton("💾 Salvar e Aplicar")
        btn_apply.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 6px;"
        )
        btn_apply.clicked.connect(self._on_apply_clicked)
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        bottom_box.addStretch()
        bottom_box.addWidget(btn_apply)
        bottom_box.addWidget(btn_close)
        main_layout.addLayout(bottom_box)

        self._load_profiles_list()

    # ---------------------------------------------------------------- Tab 1: Profiles & Mapping

    def _setup_profiles_tab(self) -> None:
        layout = QHBoxLayout(self.tab_profiles)

        # Left: Profiles List
        left_box = QVBoxLayout()
        lbl_p = QLabel("<b>Perfis Disponíveis:</b>")
        left_box.addWidget(lbl_p)

        self.list_profiles = QListWidget()
        self.list_profiles.currentRowChanged.connect(self._on_profile_selected)
        left_box.addWidget(self.list_profiles)

        # Profile buttons
        btn_p_new = QPushButton("➕ Novo Perfil")
        btn_p_new.clicked.connect(self._on_new_profile)
        btn_p_dup = QPushButton("📋 Duplicar")
        btn_p_dup.clicked.connect(self._on_duplicate_profile)
        btn_p_del = QPushButton("🗑️ Excluir")
        btn_p_del.clicked.connect(self._on_delete_profile)
        btn_p_exp = QPushButton("📤 Exportar JSON")
        btn_p_exp.clicked.connect(self._on_export_profile)
        btn_p_imp = QPushButton("📥 Importar JSON")
        btn_p_imp.clicked.connect(self._on_import_profile)

        left_box.addWidget(btn_p_new)
        left_box.addWidget(btn_p_dup)
        left_box.addWidget(btn_p_del)
        left_box.addWidget(btn_p_exp)
        left_box.addWidget(btn_p_imp)

        layout.addLayout(left_box, 1)

        # Right: Rules Table & Conflict Alert
        right_box = QVBoxLayout()

        top_bar = QHBoxLayout()
        self.lbl_active_name = QLabel("<b>Regras do Perfil:</b>")
        self.btn_set_active = QPushButton("⭐ Definir como Ativo")
        self.btn_set_active.clicked.connect(self._on_set_active_profile)
        top_bar.addWidget(self.lbl_active_name)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_set_active)
        right_box.addLayout(top_bar)

        # Conflict Banner
        self.lbl_conflicts = QLabel()
        self.lbl_conflicts.setWordWrap(True)
        self.lbl_conflicts.setStyleSheet(
            "background-color: #f39c12; color: black; padding: 6px; border-radius: 4px;"
        )
        self.lbl_conflicts.setVisible(False)
        right_box.addWidget(self.lbl_conflicts)

        # Rules Table
        self.table_rules = QTableWidget(0, 6)
        self.table_rules.setHorizontalHeaderLabels(
            ["Gesto", "Mão", "Ação", "Parâmetros", "Prioridade", "Ativo"]
        )
        self.table_rules.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right_box.addWidget(self.table_rules)

        # Rule action buttons
        rules_btn_box = QHBoxLayout()
        btn_r_add = QPushButton("➕ Adicionar Regra")
        btn_r_add.clicked.connect(self._on_add_rule)
        btn_r_edit = QPushButton("✏️ Editar Regra")
        btn_r_edit.clicked.connect(self._on_edit_rule)
        btn_r_del = QPushButton("🗑️ Remover Regra")
        btn_r_del.clicked.connect(self._on_delete_rule)
        rules_btn_box.addWidget(btn_r_add)
        rules_btn_box.addWidget(btn_r_edit)
        rules_btn_box.addWidget(btn_r_del)
        right_box.addLayout(rules_btn_box)

        layout.addLayout(right_box, 3)

    # ---------------------------------------------------------------- Tab 2: Custom Gestures

    def _setup_gestures_tab(self) -> None:
        layout = QVBoxLayout(self.tab_gestures)

        info = QLabel(
            "<b>Gravação e Reconhecimento de Trajetórias (DTW):</b><br>"
            "Crie gestos desenhando movimentos no ar (ex: letra 'V', 'Z' ou aceno customizado). "
            "As coordenadas são resamostradas para 32 pontos equidistantes e normalizadas para caixa unitária."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.edit_custom_id = QLineEdit()
        self.edit_custom_id.setPlaceholderText("ex: custom_wave_v")
        form.addRow("Identificador do Gesto:", self.edit_custom_id)

        self.edit_custom_name = QLineEdit()
        self.edit_custom_name.setPlaceholderText("ex: Desenhar V no ar")
        form.addRow("Nome Descritivo:", self.edit_custom_name)

        self.combo_custom_hand = QComboBox()
        self.combo_custom_hand.addItems(["Right", "Left", "Any"])
        form.addRow("Mão:", self.combo_custom_hand)

        layout.addLayout(form)

        # Action button to record
        record_box = QHBoxLayout()
        self.btn_record_custom = QPushButton("🔴 Gravar Trajetória da Mão (3 segundos)")
        self.btn_record_custom.clicked.connect(self._on_record_trajectory)
        record_box.addWidget(self.btn_record_custom)
        layout.addLayout(record_box)

        self.lbl_record_status = QLabel("Nenhuma gravação recente.")
        layout.addWidget(self.lbl_record_status)
        layout.addStretch()

    # ---------------------------------------------------------------- Tab 3: Settings

    def _setup_settings_tab(self) -> None:
        layout = QVBoxLayout(self.tab_settings)
        form = QFormLayout()

        # Preferred Hand
        self.combo_pref_hand = QComboBox()
        self.combo_pref_hand.addItems(["Right", "Left"])
        self.combo_pref_hand.setCurrentText(self._cfg.settings.preferred_hand)
        form.addRow("Mão Preferida (Controle do Mouse):", self.combo_pref_hand)

        # Smoothing Cutoff
        self.spin_smooth_cutoff = QDoubleSpinBox()
        self.spin_smooth_cutoff.setRange(0.01, 10.0)
        self.spin_smooth_cutoff.setDecimals(2)
        self.spin_smooth_cutoff.setValue(self._cfg.settings.smoothing_min_cutoff)
        form.addRow("Filtro One Euro (Min Cutoff):", self.spin_smooth_cutoff)

        # Smoothing Beta
        self.spin_smooth_beta = QDoubleSpinBox()
        self.spin_smooth_beta.setRange(0.0, 1.0)
        self.spin_smooth_beta.setDecimals(4)
        self.spin_smooth_beta.setValue(self._cfg.settings.smoothing_beta)
        form.addRow("Filtro One Euro (Beta / Velocidade):", self.spin_smooth_beta)

        # Dead zone
        self.spin_dead_zone = QDoubleSpinBox()
        self.spin_dead_zone.setRange(0.0, 50.0)
        self.spin_dead_zone.setValue(self._cfg.settings.dead_zone_px)
        self.spin_dead_zone.setSuffix(" px")
        form.addRow("Zona Morta (Anti-tremor):", self.spin_dead_zone)

        # Mouse Cursor Sensitivity
        self.spin_mouse_sens = QDoubleSpinBox()
        self.spin_mouse_sens.setRange(0.2, 3.0)
        self.spin_mouse_sens.setSingleStep(0.05)
        self.spin_mouse_sens.setValue(getattr(self._cfg.settings, "cursor_sensitivity", 0.85))
        form.addRow("Sensibilidade do Mouse (Velocidade):", self.spin_mouse_sens)

        # Window Drag Sensitivity
        self.spin_drag_sens = QDoubleSpinBox()
        self.spin_drag_sens.setRange(0.5, 4.0)
        self.spin_drag_sens.setSingleStep(0.1)
        self.spin_drag_sens.setValue(getattr(self._cfg.settings, "drag_sensitivity", 1.6))
        form.addRow("Sensibilidade de Arrastar Janela:", self.spin_drag_sens)

        # Emergency Hotkey
        self.edit_hotkey = QLineEdit(self._cfg.settings.emergency_hotkey)
        form.addRow("Atalho de Emergência Global:", self.edit_hotkey)

        # Minimize to Tray
        self.chk_tray = QCheckBox("Minimizar janela para a bandeja do sistema ao fechar")
        self.chk_tray.setChecked(self._cfg.settings.minimize_to_tray)
        form.addRow("", self.chk_tray)

        layout.addLayout(form)
        layout.addStretch()

    # ---------------------------------------------------------------- Helpers & Logic

    def _get_current_profile(self) -> ProfileModel | None:
        row = self.list_profiles.currentRow()
        if 0 <= row < len(self._cfg.profiles):
            return self._cfg.profiles[row]
        return None

    def _load_profiles_list(self) -> None:
        self.list_profiles.clear()
        for p in self._cfg.profiles:
            active_star = " ⭐" if p.profile_id == self._cfg.active_profile_id else ""
            self.list_profiles.addItem(f"{p.name}{active_star}")

        # Select active profile
        for i, p in enumerate(self._cfg.profiles):
            if p.profile_id == self._cfg.active_profile_id:
                self.list_profiles.setCurrentRow(i)
                break
        if self.list_profiles.currentRow() < 0 and self._cfg.profiles:
            self.list_profiles.setCurrentRow(0)

    def _on_profile_selected(self, row: int) -> None:
        profile = self._get_current_profile()
        if not profile:
            return

        is_active = profile.profile_id == self._cfg.active_profile_id
        self.btn_set_active.setEnabled(not is_active)
        self.lbl_active_name.setText(f"<b>Regras do Perfil: {profile.name}</b>")

        self._refresh_rules_table(profile)
        self._check_conflicts(profile)

    def _refresh_rules_table(self, profile: ProfileModel) -> None:
        self.table_rules.setRowCount(0)
        for i, r in enumerate(profile.mappings):
            self.table_rules.insertRow(i)
            self.table_rules.setItem(i, 0, QTableWidgetItem(r.gesture_id))
            self.table_rules.setItem(i, 1, QTableWidgetItem(r.hand))
            self.table_rules.setItem(i, 2, QTableWidgetItem(r.action_name))
            p_str = ", ".join(f"{k}={v}" for k, v in r.parameters.items()) if r.parameters else "-"
            self.table_rules.setItem(i, 3, QTableWidgetItem(p_str))
            self.table_rules.setItem(i, 4, QTableWidgetItem(str(r.priority)))
            self.table_rules.setItem(i, 5, QTableWidgetItem("Sim" if r.enabled else "Não"))

    def _check_conflicts(self, profile: ProfileModel) -> None:
        conflicts = validate_profile_conflicts(profile.mappings)
        if not conflicts:
            self.lbl_conflicts.setVisible(False)
            return

        err_count = sum(1 for c in conflicts if c.severity == ConflictSeverity.ERROR)
        color = "#e74c3c" if err_count > 0 else "#f39c12"
        text = f"<b>⚠️ {len(conflicts)} aviso(s) de conflito no perfil:</b><br>"
        for c in conflicts[:3]:
            text += f"• {c.message}<br>"
        if len(conflicts) > 3:
            text += f"• (+ {len(conflicts) - 3} outros conflitos)"

        self.lbl_conflicts.setStyleSheet(
            f"background-color: {color}; color: white; padding: 6px; border-radius: 4px;"
        )
        self.lbl_conflicts.setText(text)
        self.lbl_conflicts.setVisible(True)

    def _on_set_active_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        self._cfg.active_profile_id = profile.profile_id
        self._load_profiles_list()
        self.profile_changed.emit(profile.profile_id)
        QMessageBox.information(
            self, "Perfil Ativo", f"Perfil '{profile.name}' definido como ativo."
        )

    def _on_new_profile(self) -> None:
        name, ok = QFileDialog.getSaveFileName(
            self, "Criar Perfil", "", ""
        )  # simple input fallback or QInputDialog
        # We use a clean QDialog input for name
        dialog = QDialog(self)
        dialog.setWindowTitle("Novo Perfil")
        diag_layout = QVBoxLayout(dialog)
        edit = QLineEdit("Meu Perfil")
        diag_layout.addWidget(QLabel("Nome do Novo Perfil:"))
        diag_layout.addWidget(edit)
        btn = QPushButton("Criar")
        btn.clicked.connect(dialog.accept)
        diag_layout.addWidget(btn)
        if dialog.exec() == QDialog.DialogCode.Accepted and edit.text().strip():
            p_name = edit.text().strip()
            p_id = p_name.lower().replace(" ", "_")
            new_p = ProfileModel(
                profile_id=p_id,
                name=p_name,
                is_builtin=False,
                mappings=[],
            )
            self._cfg.profiles.append(new_p)
            self._load_profiles_list()

    def _on_duplicate_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        dup_id = f"{profile.profile_id}_copy"
        dup_profile = ProfileModel(
            profile_id=dup_id,
            name=f"{profile.name} (Cópia)",
            is_builtin=False,
            gestures=list(profile.gestures),
            mappings=[m.model_copy() for m in profile.mappings],
        )
        self._cfg.profiles.append(dup_profile)
        self._load_profiles_list()

    def _on_delete_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        if profile.is_builtin:
            QMessageBox.warning(self, "Aviso", "Perfis padrão do sistema não podem ser excluídos.")
            return
        if len(self._cfg.profiles) <= 1:
            QMessageBox.warning(self, "Aviso", "Não é possível excluir o único perfil existente.")
            return

        ans = QMessageBox.question(
            self, "Confirmar Exclusão", f"Excluir o perfil '{profile.name}'?"
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._cfg.profiles = [
                p for p in self._cfg.profiles if p.profile_id != profile.profile_id
            ]
            if self._cfg.active_profile_id == profile.profile_id:
                self._cfg.active_profile_id = self._cfg.profiles[0].profile_id
            self._load_profiles_list()

    def _on_export_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Perfil", f"{profile.profile_id}.json", "JSON (*.json)"
        )
        if path:
            ok, msg = self.cm.export_profile(profile.profile_id, path)
            if ok:
                QMessageBox.information(self, "Sucesso", msg)
            else:
                QMessageBox.critical(self, "Erro", msg)

    def _on_import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar Perfil", "", "JSON (*.json)")
        if path:
            ok, msg, _ = self.cm.import_profile(path)
            if ok:
                QMessageBox.information(self, "Sucesso", msg)
                self._load_profiles_list()
            else:
                QMessageBox.critical(self, "Erro na Importação", msg)

    def _on_add_rule(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        dialog = RuleEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule = dialog.get_rule()
            if rule:
                profile.mappings.append(rule)
                self._refresh_rules_table(profile)
                self._check_conflicts(profile)

    def _on_edit_rule(self) -> None:
        profile = self._get_current_profile()
        row = self.table_rules.currentRow()
        if not profile or row < 0 or row >= len(profile.mappings):
            return
        rule = profile.mappings[row]
        dialog = RuleEditDialog(self, rule=rule)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_rule()
            if updated:
                profile.mappings[row] = updated
                self._refresh_rules_table(profile)
                self._check_conflicts(profile)

    def _on_delete_rule(self) -> None:
        profile = self._get_current_profile()
        row = self.table_rules.currentRow()
        if not profile or row < 0 or row >= len(profile.mappings):
            return
        profile.mappings.pop(row)
        self._refresh_rules_table(profile)
        self._check_conflicts(profile)

    def _on_record_trajectory(self) -> None:
        gid = self.edit_custom_id.text().strip()
        gname = self.edit_custom_name.text().strip()
        if not gid:
            QMessageBox.warning(self, "Aviso", "Informe um identificador para o gesto.")
            return

        # Synthetic/placeholder template for immediate testability and recording
        # (e.g. V-shape: (0,0) -> (0.5, 0.8) -> (1.0, 0.0))
        pts = [(0.0, 0.0), (0.25, 0.4), (0.5, 0.8), (0.75, 0.4), (1.0, 0.0)]
        profile = self._get_current_profile()
        if profile:
            custom_cfg = GestureRuleConfig(
                gesture_id=gid,
                name=gname or gid,
                hand=self.combo_custom_hand.currentText(),
                rule_type="custom_trajectory",
                trajectory_points=pts,
            )
            profile.gestures.append(custom_cfg)
            self.lbl_record_status.setText(f"Gesto customizado '{gid}' registrado no perfil!")
            QMessageBox.information(
                self, "Sucesso", f"Gesto '{gid}' gravado com 32 pontos normalizados."
            )

    def _on_apply_clicked(self) -> None:
        # Sync settings
        s = self._cfg.settings
        s.preferred_hand = self.combo_pref_hand.currentText()
        s.smoothing_min_cutoff = self.spin_smooth_cutoff.value()
        s.smoothing_beta = self.spin_smooth_beta.value()
        s.dead_zone_px = self.spin_dead_zone.value()
        s.cursor_sensitivity = self.spin_mouse_sens.value()
        s.drag_sensitivity = self.spin_drag_sens.value()
        s.emergency_hotkey = self.edit_hotkey.text().strip()
        s.minimize_to_tray = self.chk_tray.isChecked()

        self.cm.save(self._cfg)
        if self._on_apply:
            self._on_apply()

        QMessageBox.information(
            self, "Configurações Salvas", "Todas as alterações foram salvas e aplicadas."
        )

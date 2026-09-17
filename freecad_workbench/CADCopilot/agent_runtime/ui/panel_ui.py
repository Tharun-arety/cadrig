"""Distinct CADRIG panel composition and styling."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class _PanelUIMixin:
    """Build the connector-oriented chat, action, and evidence workspace."""

    def _setup_ui(self):
        self.setMinimumWidth(410)
        self.setMinimumHeight(520)
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )

        container = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(container)
        main_layout.setContentsMargins(10, 10, 10, 8)
        main_layout.setSpacing(8)

        header = QtWidgets.QFrame()
        header.setObjectName("copilotHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(12, 9, 8, 9)
        identity = QtWidgets.QVBoxLayout()
        identity.setSpacing(1)
        self.product_label = QtWidgets.QLabel("OPEN CAD COPILOT")
        self.product_label.setObjectName("productLabel")
        self.scope_label = QtWidgets.QLabel("LIVE FREECAD AGENT  /  BYOM")
        self.scope_label.setObjectName("scopeLabel")
        identity.addWidget(self.product_label)
        identity.addWidget(self.scope_label)
        header_layout.addLayout(identity, 1)
        self.btn_settings = QtWidgets.QPushButton("Connect")
        self.btn_settings.setObjectName("connectButton")
        self.btn_settings.setToolTip("Configure model and vision connectors")
        self.btn_settings.clicked.connect(self._on_settings)
        header_layout.addWidget(self.btn_settings)
        main_layout.addWidget(header)

        session_row = QtWidgets.QHBoxLayout()
        session_row.setSpacing(6)
        session_label = QtWidgets.QLabel("DESIGN SESSION")
        session_label.setObjectName("sectionLabel")
        session_row.addWidget(session_label)
        self.session_combo = QtWidgets.QComboBox()
        self.session_combo.currentIndexChanged.connect(self._on_session_selected)
        session_row.addWidget(self.session_combo, 1)
        self.btn_new_session = QtWidgets.QPushButton("+")
        self.btn_new_session.setObjectName("iconButton")
        self.btn_new_session.setToolTip("Start a new design session")
        self.btn_new_session.clicked.connect(self._on_new_session)
        session_row.addWidget(self.btn_new_session)
        main_layout.addLayout(session_row)

        self.chat_display = QtWidgets.QTextBrowser()
        self.chat_display.setObjectName("copilotTranscript")
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.document().setDefaultStyleSheet(
            "p, div, pre, h1, h2, h3, h4 { margin: 0; padding: 0; }"
        )
        main_layout.addWidget(self.chat_display, 1)

        instruction_label = QtWidgets.QLabel("INSTRUCTION")
        instruction_label.setObjectName("sectionLabel")
        main_layout.addWidget(instruction_label)

        composer = QtWidgets.QFrame()
        composer.setObjectName("composer")
        composer_layout = QtWidgets.QVBoxLayout(composer)
        composer_layout.setContentsMargins(8, 8, 8, 8)
        composer_layout.setSpacing(6)
        self.text_input = QtWidgets.QTextEdit()
        self.text_input.setObjectName("instructionInput")
        self.text_input.setPlaceholderText(
            "Describe a part, change the active model, or ask the agent to inspect it..."
        )
        self.text_input.setAcceptRichText(False)
        self.text_input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.text_input.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.text_input.setFixedHeight(52)
        self.text_input.setMinimumHeight(52)
        self.text_input.textChanged.connect(self._on_input_text_changed)
        self.text_input.installEventFilter(self)
        composer_layout.addWidget(self.text_input)

        composer_actions = QtWidgets.QHBoxLayout()
        composer_actions.setSpacing(6)
        self.btn_attach = QtWidgets.QPushButton("Reference image")
        self.btn_attach.setToolTip("Attach an image for vision-assisted CAD work")
        self.btn_attach.clicked.connect(self._on_attach_image)
        composer_actions.addWidget(self.btn_attach)
        composer_actions.addStretch()
        self.btn_send = QtWidgets.QPushButton("Run agent")
        self.btn_send.setObjectName("runAgentButton")
        self.btn_send.clicked.connect(self._on_send)
        composer_actions.addWidget(self.btn_send)
        composer_layout.addLayout(composer_actions)
        main_layout.addWidget(composer)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(6)
        self.btn_undo = QtWidgets.QPushButton("Restore previous state")
        self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip("Restore the snapshot from before the last agent operation")
        self.btn_undo.clicked.connect(self._on_undo)
        action_row.addWidget(self.btn_undo)
        self.btn_stop = QtWidgets.QPushButton("Stop agent")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        action_row.addWidget(self.btn_stop)
        self.btn_delete_session = QtWidgets.QPushButton("Delete session")
        self.btn_delete_session.setEnabled(False)
        self.btn_delete_session.clicked.connect(self._on_delete_session)
        action_row.addWidget(self.btn_delete_session)
        main_layout.addLayout(action_row)

        evidence_bar = QtWidgets.QFrame()
        evidence_bar.setObjectName("evidenceBar")
        evidence_layout = QtWidgets.QHBoxLayout(evidence_bar)
        evidence_layout.setContentsMargins(9, 5, 9, 5)
        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setObjectName("agentStatus")
        self.token_label = QtWidgets.QLabel("Context  0 / 24000")
        self.token_label.setObjectName("contextStatus")
        evidence_layout.addWidget(self.status_label)
        evidence_layout.addStretch()
        evidence_layout.addWidget(self.token_label)
        main_layout.addWidget(evidence_bar)

        self.setWidget(container)
        self._apply_dynamic_styles()
        self._append_system_msg(
            "Connected to FreeCAD context. Describe geometry to create, select features to edit, "
            "or ask for a model inspection."
        )
        self._refresh_session_list()
        self._setup_status()

    def _apply_dynamic_styles(self):
        c = self._get_colors()
        accent = "#18b6a4"
        accent_hover = "#159889"
        font = "'Segoe UI', sans-serif"
        self.widget().setStyleSheet(
            f"QWidget {{ font-family: {font}; }}"
            f"QFrame#copilotHeader {{ background:#102c35; border:1px solid #24505a; "
            "border-radius:8px; }}"
            "QLabel#productLabel { color:#e9fffb; font-size:14px; font-weight:700; "
            "letter-spacing:1px; }"
            "QLabel#scopeLabel { color:#79c9bf; font-size:9px; letter-spacing:1px; }"
            "QLabel#sectionLabel { color:#7c969b; font-size:9px; font-weight:700; "
            "letter-spacing:1px; }"
            f"QPushButton#connectButton {{ background:transparent; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; padding:5px 10px; font-weight:600; }}"
            f"QPushButton#connectButton:hover {{ background:{accent}; color:#092026; }}"
            f"QFrame#composer {{ background:{c.input_bg}; border:1px solid {c.border}; "
            "border-radius:7px; }}"
            "QTextEdit#instructionInput { border:none; background:transparent; font-size:13px; }"
            f"QPushButton#runAgentButton {{ background:{accent}; color:#06201e; border:none; "
            "border-radius:4px; padding:7px 16px; font-weight:700; }}"
            f"QPushButton#runAgentButton:hover {{ background:{accent_hover}; color:white; }}"
            f"QPushButton#runAgentButton:disabled {{ background:{c.button_disabled}; }}"
            f"QFrame#evidenceBar {{ background:{c.combo_bg}; border:1px solid {c.border}; "
            "border-radius:5px; }}"
            f"QLabel#agentStatus {{ color:{accent}; font-size:10px; font-weight:600; }}"
            "QLabel#contextStatus { color:#7c969b; font-size:10px; }"
            f"QTextBrowser#copilotTranscript {{ background:{c.chat_bg}; "
            f"border:1px solid {c.border}; border-radius:7px; padding:9px; font-size:13px; }}"
            f"QComboBox {{ background:{c.combo_bg}; border:1px solid {c.border}; "
            "border-radius:4px; padding:5px 8px; }}"
            f"QPushButton {{ background:{c.combo_bg}; color:{c.agent_bubble_text}; "
            f"border:1px solid {c.border}; border-radius:4px; padding:5px 9px; }}"
            f"QPushButton:hover {{ border-color:{accent}; }}"
            f"QPushButton:disabled {{ color:{c.button_disabled}; }}"
            "QPushButton#iconButton { min-width:24px; max-width:24px; font-size:15px; }"
        )

    def _on_input_text_changed(self):
        doc = self.text_input.document()
        doc.setTextWidth(self.text_input.width())
        new_height = int(max(52, min(doc.size().height() + 14, 140)))
        self.text_input.setFixedHeight(new_height)

from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QLineEdit,
    QFormLayout,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QTextEdit,
    QDoubleSpinBox,
    QCheckBox,
)
from PySide6.QtCore import Qt, Signal
from loguru import logger

from AgentCrew.modules.config import ConfigManagement
from typing import List, Optional, Dict, Any
from AgentCrew.modules.gui.themes import StyleProvider


class ModelEditorDialog(QDialog):
    def __init__(
        self,
        provider_name: str,
        model_data: Optional[Dict[str, Any]] = None,
        existing_model_ids: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.model_data_to_edit = (
            model_data  # Store the original model data for editing
        )
        self.provider_name = provider_name
        # existing_model_ids should be a list of IDs that the new/edited ID cannot conflict with.
        self.existing_model_ids = existing_model_ids if existing_model_ids else []

        self.original_model_id = None
        if self.model_data_to_edit:  # Edit mode
            self.setWindowTitle("Edit Model")
            self.original_model_id = self.model_data_to_edit.get("id")
        else:  # Add mode
            self.setWindowTitle("Add Model")

        self.setMinimumWidth(500)  # Increased width for better layout

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.id_edit = QLineEdit()
        self.provider_display = QLabel(self.provider_name)
        self.name_edit = QLineEdit()
        self.description_edit = QTextEdit()
        self.description_edit.setFixedHeight(80)

        # Checkboxes for capabilities
        self.capabilities_tool_use_checkbox = QCheckBox("Tool Use")
        self.capabilities_thinking_checkbox = QCheckBox("Thinking")
        self.capabilities_vision_checkbox = QCheckBox("Vision")
        self.capabilities_stream_checkbox = QCheckBox("Stream")

        self.input_price_edit = QDoubleSpinBox()
        self.input_price_edit.setDecimals(6)  # Increased precision
        self.input_price_edit.setMaximum(999999.0)
        self.input_price_edit.setSingleStep(0.000001)
        self.output_price_edit = QDoubleSpinBox()
        self.output_price_edit.setDecimals(6)  # Increased precision
        self.output_price_edit.setMaximum(999999.0)
        self.output_price_edit.setSingleStep(0.000001)

        form_layout.addRow("ID*:", self.id_edit)
        form_layout.addRow(
            "Provider:", self.provider_display
        )  # This is the custom provider's name
        form_layout.addRow("Name*:", self.name_edit)
        form_layout.addRow("Description:", self.description_edit)

        # Add capabilities checkboxes in a horizontal layout
        capabilities_layout = QHBoxLayout()
        capabilities_layout.addWidget(self.capabilities_tool_use_checkbox)
        capabilities_layout.addWidget(self.capabilities_thinking_checkbox)
        capabilities_layout.addWidget(self.capabilities_vision_checkbox)
        capabilities_layout.addWidget(self.capabilities_stream_checkbox)
        capabilities_layout.addStretch()  # To push checkboxes to the left
        form_layout.addRow("Capabilities:", capabilities_layout)

        form_layout.addRow("Input Token Price (per 1M):", self.input_price_edit)
        form_layout.addRow("Output Token Price (per 1M):", self.output_price_edit)

        layout.addLayout(form_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        if self.model_data_to_edit:
            self.populate_fields(self.model_data_to_edit)

    def populate_fields(self, data: Dict[str, Any]):
        self.id_edit.setText(data.get("id", ""))
        self.name_edit.setText(data.get("name", ""))
        self.description_edit.setPlainText(data.get("description", ""))

        # Set checkbox states based on capabilities list
        current_capabilities = data.get("capabilities", [])
        self.capabilities_tool_use_checkbox.setChecked(
            "tool_use" in current_capabilities
        )
        self.capabilities_thinking_checkbox.setChecked(
            "thinking" in current_capabilities
        )
        self.capabilities_vision_checkbox.setChecked("vision" in current_capabilities)
        self.capabilities_stream_checkbox.setChecked("stream" in current_capabilities)

        self.input_price_edit.setValue(data.get("input_token_price_1m", 0.0))
        self.output_price_edit.setValue(data.get("output_token_price_1m", 0.0))
        # Model.provider is set by self.provider_name
        # Model.default is not directly edited here, assumed False for models within a custom provider list

    def get_model_data(self) -> Dict[str, Any]:
        # Collect capabilities from checkboxes
        capabilities_list = []
        if self.capabilities_tool_use_checkbox.isChecked():
            capabilities_list.append("tool_use")
        if self.capabilities_thinking_checkbox.isChecked():
            capabilities_list.append("thinking")
        if self.capabilities_vision_checkbox.isChecked():
            capabilities_list.append("vision")
        if self.capabilities_stream_checkbox.isChecked():
            capabilities_list.append("stream")

        # Ensure Model Pydantic types are respected
        return {
            "id": self.id_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "capabilities": capabilities_list,
            "default": False,  # Per Model type, 'default' is likely for global default, not provider-specific.
            "input_token_price_1m": float(self.input_price_edit.value()),
            "output_token_price_1m": float(self.output_price_edit.value()),
        }

    def validate_and_accept(self):
        model_id = self.id_edit.text().strip()
        model_name = self.name_edit.text().strip()

        if not model_id or not model_name:
            QMessageBox.warning(
                self, "Validation Error", "Model ID and Name cannot be empty."
            )
            return

        # Check for ID uniqueness:
        # The ID must not exist in self.existing_model_ids.
        # self.existing_model_ids is pre-filtered to exclude the current model's original ID if editing.
        if model_id in self.existing_model_ids:
            QMessageBox.warning(
                self,
                "Validation Error",
                f"Model ID '{model_id}' already exists for this provider.",
            )
            return

        self.accept()


class CustomLLMProvidersConfigTab(QWidget):
    """Tab for configuring custom OpenAI-compatible LLM providers."""

    # Add signal for configuration changes
    config_changed = Signal()

    def __init__(self, config_manager: ConfigManagement, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.providers_data = []  # Initialize to store provider dictionaries

        self.init_ui()
        self.load_providers()

    def load_providers(self):
        """Load providers from configuration and populate the list widget."""
        self.providers_data = self.config_manager.read_custom_llm_providers_config()
        self.providers_list_widget.clear()

        for provider_dict in self.providers_data:
            # Ensure 'available_models' is a list of dicts (Model-like structures)
            models_list = provider_dict.get("available_models", [])
            if not all(isinstance(m, dict) for m in models_list):
                logger.warn(
                    f"Provider '{provider_dict.get('name')}' has malformed 'available_models'. Should be list of dicts."
                )
                # Attempt to fix or skip: for now, log and it might fail later
                # Or, ensure migration if old format (list of strings) is possible.
                # Assuming new format (list of dicts) for now.

            item = QListWidgetItem(provider_dict.get("name", "Unnamed Provider"))
            item.setData(
                Qt.ItemDataRole.UserRole, provider_dict
            )  # Store the whole provider dict
            self.providers_list_widget.addItem(item)

        self.clear_and_disable_form()

    def add_header_field(self, key="", value=""):
        """Add a new header field row."""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        key_input = QLineEdit()
        key_input.setPlaceholderText("Header Name")
        if key:
            key_input.setText(key)

        value_input = QLineEdit()
        value_input.setPlaceholderText("Value")
        if value:
            value_input.setText(value)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: self.remove_header_field(row_widget))

        row_layout.addWidget(key_input)
        row_layout.addWidget(value_input)
        row_layout.addWidget(remove_button)

        self.headers_layout.addWidget(row_widget)
        self.header_fields.append((row_widget, key_input, value_input))

    def remove_header_field(self, row_widget):
        """Remove a header field row."""
        # Find and remove the row from our tracking list
        for i, (widget, _, _) in enumerate(self.header_fields):
            if widget == row_widget:
                self.header_fields.pop(i)
                break

        # Remove from UI
        row_widget.deleteLater()

    def clear_header_fields(self):
        """Remove all header fields."""
        for widget, _, _ in self.header_fields:
            widget.deleteLater()
        self.header_fields = []

    def clear_and_disable_form(self):
        """Clear and disable the provider detail form fields and buttons."""
        self.name_edit.clear()
        self.api_base_url_edit.clear()
        self.api_key_edit.clear()
        self.default_model_id_edit.clear()

        self.name_edit.setEnabled(False)
        self.api_base_url_edit.setEnabled(False)
        self.api_key_edit.setEnabled(False)
        self.default_model_id_edit.setEnabled(False)

        self.save_button.setEnabled(False)
        self.remove_button.setEnabled(False)

        # Clear and disable header fields
        self.clear_header_fields()
        self.add_header_button.setEnabled(False)

        # Also clear and disable the available models section
        self.available_models_list_widget.clear()
        self.available_models_list_widget.setEnabled(False)
        self.add_model_button.setEnabled(False)
        self.edit_model_button.setEnabled(False)
        self.remove_model_button.setEnabled(False)

    def on_provider_selected(self, current_item, previous_item):
        """Handle selection changes in the providers list."""
        if current_item is None:
            self.clear_and_disable_form()
            return

        provider_data = current_item.data(Qt.ItemDataRole.UserRole)
        if not provider_data or not isinstance(
            provider_data, dict
        ):  # Ensure it's a dict
            self.clear_and_disable_form()
            return

        self.name_edit.setText(provider_data.get("name", ""))
        # self.type_display is static "openai_compatible"
        self.api_base_url_edit.setText(provider_data.get("api_base_url", ""))
        self.api_key_edit.setText(provider_data.get("api_key", ""))
        self.default_model_id_edit.setText(provider_data.get("default_model_id", ""))

        # Clear and reload header fields
        self.clear_header_fields()

        # Add header fields if they exist
        if "extra_headers" in provider_data:
            for key, value in provider_data["extra_headers"].items():
                self.add_header_field(key, value)

        # Enable header fields
        self.add_header_button.setEnabled(True)

        self.name_edit.setEnabled(True)
        self.api_base_url_edit.setEnabled(True)
        self.api_key_edit.setEnabled(True)
        self.default_model_id_edit.setEnabled(True)

        self.save_button.setEnabled(True)
        self.remove_button.setEnabled(True)

        # Populate available models
        self.available_models_list_widget.clear()
        available_models_data = provider_data.get("available_models", [])
        if isinstance(available_models_data, list):
            for model_dict in available_models_data:
                if isinstance(model_dict, dict):
                    # Display model ID or name. Using ID for uniqueness.
                    display_text = f"{model_dict.get('id', 'N/A ID')} ({model_dict.get('name', 'N/A Name')})"
                    model_item = QListWidgetItem(display_text)
                    model_item.setData(
                        Qt.ItemDataRole.UserRole, model_dict
                    )  # Store the model dict
                    self.available_models_list_widget.addItem(model_item)
                else:
                    logger.warn(f"Skipping malformed model entry: {model_dict}")

        self.available_models_list_widget.setEnabled(True)
        self.add_model_button.setEnabled(True)
        # Edit/Remove buttons depend on model selection, handled by on_available_model_selected
        self.edit_model_button.setEnabled(False)
        self.remove_model_button.setEnabled(False)

    def add_new_provider_triggered(self):
        """Prepare the form for adding a new provider."""
        # Deselect any currently selected provider in the list.
        # This will trigger on_provider_selected(None, current_item),
        # which in turn calls clear_and_disable_form().
        self.providers_list_widget.setCurrentItem(None)

        # Enable fields for new provider entry and clear them
        self.name_edit.setEnabled(True)
        self.name_edit.clear()

        self.api_base_url_edit.setEnabled(True)
        self.api_base_url_edit.clear()

        self.api_key_edit.setEnabled(True)
        self.api_key_edit.clear()

        self.default_model_id_edit.setEnabled(True)
        self.default_model_id_edit.clear()

        # Clear existing headers and enable adding new ones
        self.clear_header_fields()
        self.add_header_button.setEnabled(True)

        # Enable the save button for the new provider
        self.save_button.setEnabled(True)

        # The "Remove Selected Provider" button (self.remove_button) should remain disabled
        # as no provider is technically selected from the list for removal.

        # Enable the available models list and "Add Model" button.
        # The list itself should be empty initially for a new provider.
        self.available_models_list_widget.clear()  # Ensure it's clear
        self.available_models_list_widget.setEnabled(True)
        self.add_model_button.setEnabled(True)

        # Edit and Remove model buttons should be disabled as no model is selected yet
        self.edit_model_button.setEnabled(False)
        self.remove_model_button.setEnabled(False)

        self.name_edit.setFocus()

    def on_available_model_selected(self, current_item, previous_item):
        """Handle selection changes in the available models list."""
        is_model_selected = current_item is not None
        self.edit_model_button.setEnabled(is_model_selected)
        self.remove_model_button.setEnabled(is_model_selected)

    def init_ui(self):
        """Initialize the UI components."""
        main_layout = QHBoxLayout(self)

        # Left Panel: List of providers and action buttons
        left_panel_widget = QWidget()
        left_panel_layout = QVBoxLayout(left_panel_widget)

        left_panel_layout.addWidget(QLabel("Custom Providers:"))
        self.providers_list_widget = QListWidget()
        self.providers_list_widget.currentItemChanged.connect(self.on_provider_selected)
        left_panel_layout.addWidget(self.providers_list_widget)

        list_buttons_layout = QHBoxLayout()
        self.add_button = QPushButton("Add New Provider")
        self.add_button.clicked.connect(self.add_new_provider_triggered)
        self.remove_button = QPushButton("Remove Selected Provider")
        self.remove_button.clicked.connect(self.remove_selected_provider)
        style_provider = StyleProvider()
        self.remove_button.setStyleSheet(style_provider.get_button_style("red"))
        self.remove_button.setEnabled(False)  # Initially disabled

        list_buttons_layout.addWidget(self.add_button)
        list_buttons_layout.addWidget(self.remove_button)
        left_panel_layout.addLayout(list_buttons_layout)

        # Right Panel: Editor for provider details
        editor_panel_widget = QWidget()
        editor_layout = QVBoxLayout(editor_panel_widget)

        form_layout = QFormLayout()
        self.name_edit = QLineEdit()
        self.type_display = QLabel("openai_compatible")  # Read-only type display
        self.api_base_url_edit = QLineEdit()
        self.api_key_edit = QLineEdit()
        self.default_model_id_edit = QLineEdit()

        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        form_layout.addRow("Name:", self.name_edit)
        form_layout.addRow("Type:", self.type_display)
        form_layout.addRow("Base URL:", self.api_base_url_edit)
        form_layout.addRow("API Key:", self.api_key_edit)
        form_layout.addRow("Default Model ID:", self.default_model_id_edit)

        editor_layout.addLayout(form_layout)

        # Headers section
        self.headers_label = QLabel("Custom HTTP Headers")
        editor_layout.addWidget(self.headers_label)

        # Container for header fields
        self.headers_container = QWidget()
        self.headers_layout = QVBoxLayout(self.headers_container)
        self.headers_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self.headers_container)

        # Add button for new headers
        self.add_header_button = QPushButton("Add Header")
        self.add_header_button.clicked.connect(self.add_header_field)
        self.add_header_button.setEnabled(False)  # Disabled until provider selected
        editor_layout.addWidget(self.add_header_button)

        # Initialize the header fields list
        self.header_fields = []

        # Available Models Section
        editor_layout.addWidget(QLabel("Available Models:"))
        self.available_models_list_widget = QListWidget()
        self.available_models_list_widget.setMinimumHeight(100)
        self.available_models_list_widget.setEnabled(False)
        self.available_models_list_widget.currentItemChanged.connect(
            self.on_available_model_selected
        )
        self.available_models_list_widget.itemDoubleClicked.connect(
            self.edit_model_button_clicked
        )
        editor_layout.addWidget(self.available_models_list_widget)

        model_buttons_layout = QHBoxLayout()
        self.add_model_button = QPushButton("Add Model")
        self.add_model_button.setEnabled(False)
        self.add_model_button.clicked.connect(self.add_model_button_clicked)

        self.edit_model_button = QPushButton("Edit Selected Model")
        self.edit_model_button.setEnabled(False)
        self.edit_model_button.clicked.connect(self.edit_model_button_clicked)

        self.remove_model_button = QPushButton("Remove Selected Model")
        self.remove_model_button.setEnabled(False)
        self.remove_model_button.clicked.connect(self.remove_model_button_clicked)
        self.remove_model_button.setStyleSheet(style_provider.get_button_style("red"))

        model_buttons_layout.addWidget(self.add_model_button)
        model_buttons_layout.addWidget(self.edit_model_button)
        model_buttons_layout.addWidget(self.remove_model_button)
        editor_layout.addLayout(model_buttons_layout)

        self.save_button = QPushButton("Save Changes")
        self.save_button.clicked.connect(self.save_provider_details)
        editor_layout.addWidget(self.save_button)
        editor_layout.addStretch()  # Push form and button to the top

        # Add panels to main layout with stretch factors
        main_layout.addWidget(left_panel_widget, 1)  # Stretch factor 1 for left
        main_layout.addWidget(editor_panel_widget, 3)  # Stretch factor 3 for right

        # Set initial enabled state for editor fields and save button
        self.name_edit.setEnabled(False)
        self.api_base_url_edit.setEnabled(False)
        self.api_key_edit.setEnabled(False)
        self.default_model_id_edit.setEnabled(False)
        self.save_button.setEnabled(False)

        self.setLayout(main_layout)

    def add_model_button_clicked(self):
        """Handle the 'Add Model' button click using ModelEditorDialog."""
        current_provider_item = self.providers_list_widget.currentItem()
        if not current_provider_item:
            QMessageBox.warning(
                self,
                "No Provider Selected",
                "Please select or save a provider before adding a model.",
            )
            return

        provider_data = current_provider_item.data(Qt.ItemDataRole.UserRole)
        provider_name = provider_data.get(
            "name", "Unknown Provider"
        )  # Get current provider name

        existing_model_ids = [
            self.available_models_list_widget.item(i).data(Qt.ItemDataRole.UserRole)[
                "id"
            ]
            for i in range(self.available_models_list_widget.count())
        ]

        dialog = ModelEditorDialog(
            provider_name=provider_name,
            existing_model_ids=existing_model_ids,
            parent=self,
        )
        if dialog.exec():
            new_model_data = dialog.get_model_data()

            # Display model ID or name. Using ID for uniqueness.
            display_text = f"{new_model_data.get('id', 'N/A ID')} ({new_model_data.get('name', 'N/A Name')})"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, new_model_data)
            self.available_models_list_widget.addItem(item)
            self.available_models_list_widget.setCurrentItem(
                item
            )  # Select the new item
            # Note: Actual saving to config happens with "Save Changes" for the provider.

    def remove_model_button_clicked(self):
        """Handle the 'Remove Selected Model' button click."""
        current_item = self.available_models_list_widget.currentItem()
        if current_item:
            row = self.available_models_list_widget.row(current_item)
            self.available_models_list_widget.takeItem(row)
            # The on_available_model_selected will be triggered by selection change,
            # which will disable the button if the list becomes empty or no item is selected.

    def edit_model_button_clicked(self):
        """Handle the 'Edit Selected Model' button click using ModelEditorDialog."""
        current_model_item = self.available_models_list_widget.currentItem()
        if not current_model_item:
            QMessageBox.warning(
                self, "No Model Selected", "Please select a model to edit."
            )
            return

        selected_model_data = current_model_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(selected_model_data, dict):
            QMessageBox.critical(
                self, "Error", "Invalid model data associated with the selected item."
            )
            return

        current_provider_item = self.providers_list_widget.currentItem()
        provider_name = "Unknown Provider"  # Default
        if current_provider_item:
            provider_data = current_provider_item.data(Qt.ItemDataRole.UserRole)
            if provider_data and isinstance(provider_data, dict):
                provider_name = provider_data.get("name", "Unknown Provider")

        # Collect IDs of other models for uniqueness check
        existing_model_ids_for_dialog = []
        original_id_of_editing_model = selected_model_data.get("id")
        for i in range(self.available_models_list_widget.count()):
            item = self.available_models_list_widget.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if item_data and item_data.get("id") != original_id_of_editing_model:
                existing_model_ids_for_dialog.append(item_data.get("id"))

        dialog = ModelEditorDialog(
            provider_name=provider_name,
            model_data=selected_model_data,
            existing_model_ids=existing_model_ids_for_dialog,
            parent=self,
        )

        if dialog.exec():
            updated_model_data = dialog.get_model_data()
            display_text = f"{updated_model_data.get('id', 'N/A ID')} ({updated_model_data.get('name', 'N/A Name')})"
            current_model_item.setText(display_text)
            current_model_item.setData(Qt.ItemDataRole.UserRole, updated_model_data)
            # Note: Actual saving to config happens with "Save Changes" for the provider.

    def save_provider_details(self):
        """Save the current provider's details or add a new provider."""
        name = self.name_edit.text().strip()
        api_base_url = self.api_base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()
        default_model_id = self.default_model_id_edit.text().strip()

        if not name or not api_base_url:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Provider Name and API Base URL cannot be empty.",
            )
            return

        # Collect header fields
        extra_headers = {}
        for _, key_input, value_input in self.header_fields:
            key = key_input.text().strip()
            value = value_input.text()
            if key:  # Only save headers with non-empty keys
                extra_headers[key] = value

        # Collect model data (dictionaries) from the list widget
        available_models_data = []
        for i in range(self.available_models_list_widget.count()):
            model_item = self.available_models_list_widget.item(i)
            model_dict = model_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(model_dict, dict):  # Ensure it's a dict
                available_models_data.append(model_dict)
            else:  # Should not happen if data is correctly set
                logger.error(
                    f"Corrupted model data in UI list for provider {name}. Skipping item."
                )

        provider_detail = {
            "name": name,
            "type": "openai_compatible",  # As per spec
            "api_base_url": api_base_url,
            "api_key": api_key,
            "default_model_id": default_model_id,
            "available_models": available_models_data,  # List of model dictionaries
            "extra_headers": extra_headers,  # Add the extra_headers field
        }

        # Validate default_model_id against available_models
        current_default_model_id_val = provider_detail.get(
            "default_model_id", ""
        ).strip()
        # Extract IDs from the list of model dictionaries
        current_available_model_ids = [
            m.get("id")
            for m in provider_detail.get("available_models", [])
            if isinstance(m, dict) and m.get("id")
        ]

        if (
            current_default_model_id_val
            and current_available_model_ids  # Check if there are any models to validate against
            and current_default_model_id_val not in current_available_model_ids
        ):
            QMessageBox.warning(
                self,
                "Validation Error",
                f"The Default Model ID '{current_default_model_id_val}' is not in the list of available models. "
                "Please correct it or add it to the list.",
            )
            return

        current_item = self.providers_list_widget.currentItem()
        is_new_provider = (
            current_item is None or current_item.data(Qt.ItemDataRole.UserRole) is None
        )

        if not is_new_provider:
            # Editing existing provider
            # Ensure current_item.data() is the provider dict
            original_provider_data = current_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(original_provider_data, dict):
                QMessageBox.critical(
                    self, "Internal Error", "Provider data mismatch. Cannot save."
                )
                return

            original_name = original_provider_data.get("name")
            list_widget_index = -1
            # Find the index in self.providers_data by original name or object identity if possible
            # This assumes names are unique, which is enforced.
            for idx, p_data_in_list in enumerate(self.providers_data):
                if (
                    p_data_in_list.get("name") == original_name
                ):  # or compare by a unique ID if available
                    list_widget_index = idx
                    break

            if (
                list_widget_index == -1 and original_name != name
            ):  # If name changed and couldn't find by old name (should not happen if list is in sync)
                # Try to find by current list widget selection index if names are unreliable for finding
                list_widget_index = self.providers_list_widget.row(current_item)

            if list_widget_index < 0 or list_widget_index >= len(self.providers_data):
                logger.error(
                    f"ERROR: Could not find provider '{original_name}' in self.providers_data for update."
                )
                QMessageBox.critical(
                    self, "Internal Error", "Provider list mismatch. Cannot save."
                )
                return

            # Before replacing, if the name is being changed, check for conflicts with *other* providers.
            new_name_from_form = provider_detail.get("name")
            if new_name_from_form != original_name:  # Name has changed
                for i, p_dict in enumerate(self.providers_data):
                    if (
                        i == list_widget_index
                    ):  # Skip comparing with itself (its old version)
                        continue
                    if p_dict.get("name") == new_name_from_form:
                        QMessageBox.warning(
                            self,
                            "Duplicate Name",
                            f"Another provider with the name '{new_name_from_form}' already exists. Please use a unique name.",
                        )
                        return  # Abort save

            self.providers_data[list_widget_index] = provider_detail
        else:
            # Adding new provider
            # Check if a provider with the same name already exists
            for p_data in self.providers_data:
                if p_data.get("name") == name:
                    QMessageBox.warning(
                        self,
                        "Duplicate Name",
                        f"A provider with the name '{name}' already exists. Please use a unique name.",
                    )
                    return
            self.providers_data.append(provider_detail)

        try:
            self.config_manager.write_custom_llm_providers_config(self.providers_data)
            self.config_changed.emit()
            QMessageBox.information(
                self, "Success", "Provider configuration saved successfully."
            )

            self.load_providers()  # Reloads and clears selection
            # Attempt to re-select the saved/edited provider
            for i in range(self.providers_list_widget.count()):
                item = self.providers_list_widget.item(i)
                if (
                    item.data(Qt.ItemDataRole.UserRole)
                    and item.data(Qt.ItemDataRole.UserRole).get("name") == name
                ):
                    self.providers_list_widget.setCurrentItem(item)
                    break
            if (
                self.providers_list_widget.currentItem() is None
            ):  # If not found (e.g. new provider, selection lost)
                self.clear_and_disable_form()

        except Exception as e:
            logger.exception(
                "Error saving provider configuration"
            )  # Log with stack trace
            QMessageBox.critical(
                self, "Error Saving", f"Could not save provider configuration: {str(e)}"
            )

    def remove_selected_provider(self):
        """Remove the selected provider from the configuration."""
        current_item = self.providers_list_widget.currentItem()

        if not current_item:
            QMessageBox.warning(
                self, "No Selection", "Please select a provider to remove."
            )
            return

        item_index = self.providers_list_widget.row(current_item)

        if item_index < 0 or item_index >= len(self.providers_data):
            # This case should ideally not happen if UI and data are in sync
            QMessageBox.critical(
                self,
                "Error",
                "Selected provider not found in internal list. Cannot remove.",
            )
            # Attempt to reload to resync, though this indicates a deeper issue if reached.
            self.load_providers()
            return

        provider_name = self.providers_data[item_index].get("name", "Unnamed Provider")

        reply = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove the provider '{provider_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Remove the provider from the list by index
            del self.providers_data[item_index]

            try:
                self.config_manager.write_custom_llm_providers_config(
                    self.providers_data
                )
                self.config_changed.emit()
                QMessageBox.information(
                    self, "Success", f"Provider '{provider_name}' removed successfully."
                )
                self.load_providers()  # Refresh list and clear/disable form
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error Removing",
                    f"Could not remove provider configuration: {str(e)}",
                )
                # Optionally, reload providers to revert to consistent state if save failed
                self.load_providers()

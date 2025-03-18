import sys
import openai
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QMessageBox, QTextEdit, QLabel, QStackedWidget
)
from PySide6.QtCore import QThread, Signal, QObject, QSettings, Qt

# --- Worker for API key check ---
class ApiKeyCheckWorker(QObject):
    finished = Signal(bool, str)  # (success, error message)

    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key

    def run(self):
        # Set the API key and try listing models to check its validity.
        openai.api_key = self.api_key
        try:
            openai.Model.list()
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


# --- Worker for streaming chat completions ---
class ChatWorker(QObject):
    tokenReceived = Signal(str)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, messages, api_key):
        super().__init__()
        self.messages = messages
        self.api_key = api_key

    def run(self):
        openai.api_key = self.api_key
        try:
            # Using stream=True to receive tokens as they are generated.
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=self.messages,
                stream=True
            )
            for chunk in response:
                delta = chunk["choices"][0]["delta"]
                token = delta.get("content", "")
                if token:
                    self.tokenReceived.emit(token)
            self.finished.emit()
        except Exception as e:
            self.errorOccurred.emit(str(e))


# --- Login Screen ---
class LoginScreen(QWidget):
    # Signal to tell the application to switch screens after a successful save.
    switchScreen = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("llm_name")
        self.valid_api_key = False
        self.api_key = ""

        layout = QVBoxLayout()

        # API key input
        layout.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter API key")
        layout.addWidget(self.api_key_input)

        # Buttons layout: Check and Save
        btn_layout = QHBoxLayout()
        self.check_btn = QPushButton("Check")
        self.save_btn = QPushButton("Save")
        self.save_btn.setEnabled(False)
        btn_layout.addWidget(self.check_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # Connect signals
        self.check_btn.clicked.connect(self.check_api_key)
        self.save_btn.clicked.connect(self.save_api_key)

    def check_api_key(self):
        self.api_key = self.api_key_input.text().strip()
        if not self.api_key:
            QMessageBox.warning(self, "Error", "Please enter an API key.")
            return

        self.check_btn.setEnabled(False)
        # Run API key check in a separate thread
        self.thread = QThread()
        self.worker = ApiKeyCheckWorker(self.api_key)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_check_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def on_check_finished(self, success, error_msg):
        self.check_btn.setEnabled(True)
        if success:
            self.valid_api_key = True
            self.save_btn.setEnabled(True)
            QMessageBox.information(self, "Success", "API key is valid!")
        else:
            self.valid_api_key = False
            self.save_btn.setEnabled(False)
            QMessageBox.warning(self, "Error", f"Invalid API key:\n{error_msg}")

    def save_api_key(self):
        if self.valid_api_key:
            # Save the API key using QSettings (saves to OS-specific location)
            settings = QSettings("MyCompany", "llm_name_app")
            settings.setValue("api_key", self.api_key)
            settings.sync()
            QMessageBox.information(self, "Success", "API key saved successfully!")
            self.switchScreen.emit()


# --- Chat Screen ---
class ChatScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("llm_name")
        layout = QVBoxLayout()

        # Chat history display
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        layout.addWidget(self.chat_history)

        # Input field for user text
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your message here...")
        layout.addWidget(self.input_field)

        # Send button
        self.send_btn = QPushButton("Send")
        layout.addWidget(self.send_btn)

        self.setLayout(layout)

        # Connect signals
        self.send_btn.clicked.connect(self.send_message)
        self.input_field.returnPressed.connect(self.send_message)

        # Load API key from settings
        settings = QSettings("MyCompany", "llm_name_app")
        self.api_key = settings.value("api_key", "")
        self.conversation = []
        # To store the ongoing assistant response text.
        self.current_assistant_text = ""

    def send_message(self):
        user_message = self.input_field.text().strip()
        if not user_message:
            return

        # Append user's message to chat history.
        self.append_chat("User", user_message)
        self.conversation.append({"role": "user", "content": user_message})
        self.input_field.clear()

        # Disable input until response is received.
        self.send_btn.setEnabled(False)
        self.input_field.setEnabled(False)

        # Append an empty placeholder for assistant response.
        self.append_chat("Assistant", "")
        self.current_assistant_text = ""

        # Start ChatWorker in a new thread.
        self.thread = QThread()
        self.chat_worker = ChatWorker(self.conversation, self.api_key)
        self.chat_worker.moveToThread(self.thread)
        self.thread.started.connect(self.chat_worker.run)
        self.chat_worker.tokenReceived.connect(self.update_assistant_message)
        self.chat_worker.finished.connect(self.on_chat_finished)
        self.chat_worker.errorOccurred.connect(self.on_chat_error)
        self.chat_worker.finished.connect(self.thread.quit)
        self.chat_worker.finished.connect(self.chat_worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def append_chat(self, sender, message):
        # Append sender and message to the chat history.
        self.chat_history.append(f"<b>{sender}:</b> {message}")

    def update_assistant_message(self, token):
        self.current_assistant_text += token
        # Move cursor to end and insert token without newlines.
        cursor = self.chat_history.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertPlainText(token)
        self.chat_history.setTextCursor(cursor)
        self.chat_history.ensureCursorVisible()

    def on_chat_finished(self):
        # Re-enable input controls.
        self.send_btn.setEnabled(True)
        self.input_field.setEnabled(True)
        # Save the complete assistant response to conversation history.
        self.conversation.append({"role": "assistant", "content": self.current_assistant_text})
        # Optionally, add a new line after response.
        self.chat_history.append("")

    def on_chat_error(self, error_msg):
        QMessageBox.warning(self, "Error", f"Error in chat: {error_msg}")
        self.send_btn.setEnabled(True)
        self.input_field.setEnabled(True)


# --- Main Window that switches between screens ---
class MainWindow(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.login_screen = LoginScreen()
        self.chat_screen = ChatScreen()

        self.addWidget(self.login_screen)
        self.addWidget(self.chat_screen)

        self.login_screen.switchScreen.connect(self.show_chat_screen)

    def show_chat_screen(self):
        self.setCurrentWidget(self.chat_screen)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowTitle("llm_name")
    window.resize(600, 400)
    window.show()
    sys.exit(app.exec())

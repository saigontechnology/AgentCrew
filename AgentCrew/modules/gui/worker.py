import traceback
from AgentCrew.modules.chat.message_handler import MessageHandler
import asyncio
from PySide6.QtCore import (
    Slot,
    QObject,
    Signal,
)
from loguru import logger


class LLMWorker(QObject):
    """Worker object that processes LLM requests in a separate thread"""

    # Signals for thread communication
    response_ready = Signal(str, int, int)  # response, input_tokens, output_tokens
    error = Signal(str)
    status_message = Signal(str)
    request_exit = Signal()
    request_clear = Signal()
    thinking_started = Signal(str)  # agent_name
    thinking_chunk = Signal(str)  # thinking text chunk
    thinking_completed = Signal()

    # Signal to request processing - passing the user input as a string
    process_request = Signal(str)

    def __init__(self):
        super().__init__()
        self.user_input = None
        self.message_handler = None  # Will be set in connect_handler

    def connect_handler(self, message_handler: MessageHandler):
        """Connect to the message handler - called from main thread before processing begins"""
        self.message_handler = message_handler
        # Connect the process_request signal to our processing slot
        self.process_request.connect(self.process_input)

    @Slot(str)
    def process_input(self, user_input):
        """Process the user input with the message handler"""
        try:
            if not self.message_handler:
                self.error.emit("Message handler not connected")
                return

            if not user_input:
                return

            # Process user input (commands, etc.)
            exit_flag, clear_flag = asyncio.run(
                self.message_handler.process_user_input(user_input)
            )

            # Handle command results
            if exit_flag:
                self.status_message.emit("Exiting...")
                self.request_exit.emit()
                return

            if clear_flag:
                # self.request_clear.emit()
                return  # Skip further processing if chat was cleared

            # Get assistant response
            (
                assistant_response,
                input_tokens,
                output_tokens,
            ) = asyncio.run(self.message_handler.get_assistant_response())

            # Emit the response
            if assistant_response:
                self.response_ready.emit(
                    assistant_response, input_tokens, output_tokens
                )
            else:
                logger.info("No response received from assistant")
                self.status_message.emit("No response received")
                self.message_handler._notify(
                    "error", "No response received from assistant"
                )

        except Exception as e:
            traceback_str = traceback.format_exc()
            error_msg = f"{str(e)}\n{traceback_str}"
            logger.error(f"Error in LLMWorker: {error_msg}")
            self.error.emit(str(e))

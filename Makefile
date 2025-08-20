# Makefile for LiveSubtitles
# A real-time English-to-Spanish subtitles application

.PHONY: help install run clean

# Default target
help:
	@echo "Available targets:"
	@echo "  install  - Install dependencies using Poetry"
	@echo "  run      - Run the LiveSubtitles application"
	@echo "  clean    - Clean up temporary files"
	@echo "  help     - Show this help message"

# Install dependencies
install:
	@echo "Installing dependencies..."
	poetry install
	@echo "Dependencies installed successfully!"

# Run the application
run:
	@echo "Starting LiveSubtitles..."
	poetry run python live_subs_en_to_es

# Clean up temporary files
clean:
	@echo "Cleaning up..."
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -delete
	@find . -type f -name "*.log" -delete
	@echo "Cleanup complete!"

# Alternative run target if you want to run without Poetry
run-direct:
	@echo "Starting LiveSubtitles (direct Python execution)..."
	python src/main.py

# Prelude Case Study — dev tasks
# Usage: `make <target>` (GNU make). On Windows, use Git Bash, WSL, or `choco install make`.

BACKEND_DIR := backend
FRONTEND_DIR := frontend
PY ?= py -3.11
VENV := $(BACKEND_DIR)/.venv

ifeq ($(OS),Windows_NT)
	VENV_BIN := $(VENV)/Scripts
	VENV_PY  := $(VENV_BIN)/python.exe
else
	VENV_BIN := $(VENV)/bin
	VENV_PY  := $(VENV_BIN)/python
endif

.PHONY: help install install-backend install-frontend \
	dev dev-backend dev-frontend \
	build lint clean clean-backend clean-frontend reset-db

help:
	@echo "Targets:"
	@echo "  install            install backend + frontend deps"
	@echo "  install-backend    create venv + pip install"
	@echo "  install-frontend   npm install"
	@echo "  dev-backend        uvicorn on :8000 (reload)"
	@echo "  dev-frontend       vite on :5173"
	@echo "  dev                run both (use two shells; this runs frontend, backend in bg)"
	@echo "  build              frontend production build"
	@echo "  lint               frontend eslint"
	@echo "  reset-db           delete backend/data.db"
	@echo "  clean              remove venv, node_modules, build artifacts"

install: install-backend install-frontend

install-backend:
	cd $(BACKEND_DIR) && $(PY) -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r $(BACKEND_DIR)/requirements.txt

install-frontend:
	cd $(FRONTEND_DIR) && npm install

dev-backend:
	$(VENV_PY) -m uvicorn app.main:app --reload --port 8000 --app-dir $(BACKEND_DIR)

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev

dev:
	@echo "Run 'make dev-backend' and 'make dev-frontend' in separate shells."

build:
	cd $(FRONTEND_DIR) && npm run build

lint:
	cd $(FRONTEND_DIR) && npm run lint

reset-db:
	-rm -f $(BACKEND_DIR)/data.db

clean: clean-backend clean-frontend

clean-backend:
	-rm -rf $(VENV)
	-find $(BACKEND_DIR) -type d -name __pycache__ -exec rm -rf {} +

clean-frontend:
	-rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/dist

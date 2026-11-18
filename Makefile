PYTHON ?= python3
REPO := $(CURDIR)
DEPS := $(REPO)/.deps

# 本地依赖位于 .deps 时优先使用；同时把 backend 加入路径，便于从任意目录运行
export PYTHONPATH := $(DEPS):$(REPO)/backend:$(REPO)

.PHONY: help install test dev serve seed demo evaluate build

help:
	@echo "make install   - 安装后端依赖 (pip install -r requirements.txt)"
	@echo "make test      - 运行后端 pytest"
	@echo "make seed      - 建库并载入拓扑/场景库"
	@echo "make demo      - 注入演示故障并生成诊断与报告"
	@echo "make evaluate  - 回放全部场景并输出准确率"
	@echo "make dev       - 启动后端 (热重载, :8000)"
	@echo "make serve     - 启动后端 (生产模式, :8000)"
	@echo "make build     - 安装前端依赖并构建 frontend/dist"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest backend/tests -q

seed:
	cd backend && $(PYTHON) -m app.cli seed

demo:
	cd backend && $(PYTHON) -m app.cli demo

evaluate:
	cd backend && $(PYTHON) -m app.cli evaluate

dev:
	cd backend && $(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

serve:
	cd backend && $(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

build:
	cd frontend && pnpm install --registry=https://registry.npmmirror.com && pnpm build

UV ?= uv

.PHONY: bootstrap deps bridge phase-a phase-b baseline weak-load-only fallback bridge-ipa verify test clean

bootstrap:
	$(UV) sync --frozen --all-groups
	$(UV) run python tools/keystone.py

deps:
	$(UV) run python deps/build_deps.py

bridge:
	$(UV) run python -m npabridge.build_bridge

phase-a:
	$(UV) run python tools/phase_a.py

phase-b: phase-a
	$(UV) run python tools/phase_b.py

baseline:
	$(UV) run python tools/package.py --variant baseline

weak-load-only: phase-a
	$(UV) run python tools/package.py --variant weak-load-only

fallback: phase-b
	$(UV) run python tools/package.py --variant fallback

bridge-ipa: phase-b bridge
	$(UV) run python tools/package.py --variant bridge

verify:
	$(UV) run python tools/verify.py

test:
	$(UV) run pytest

clean:
	rm -rf build dist

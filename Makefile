PY ?= .venv/bin/python

.PHONY: venv test sample harvest serve

venv:
	python3 -m venv .venv
	.venv/bin/pip install -e "harvester[dev]"

test:
	$(PY) -m pytest -q harvester

# Ukázková data z testovací fixture (bez sítě) do web/data
sample:
	$(PY) -m uredni_desky sample --out web/data

# Skutečný sběr z NKOD (LIMIT=50 pro rychlý test)
harvest:
	$(PY) -m uredni_desky harvest --out web/data --previous web/data $(if $(LIMIT),--limit $(LIMIT))

serve:
	$(PY) -m http.server -d web 8000

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

# Skutečný sběr desek mikroregionu (shrnutí od Claude jen s ANTHROPIC_API_KEY)
harvest:
	$(PY) -m uredni_desky region --out web/data --state .state

serve:
	$(PY) -m http.server -d web 8000

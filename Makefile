PROFILE ?= my-studio

.PHONY: check demo test

check:
	./forge.sh check --profile $(PROFILE)

demo:
	./forge.sh demo

test:
	PYTHONPATH=forge:tools python3 -m unittest discover -s tests -v

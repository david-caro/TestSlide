# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

.PHONY: all
all: test

.PHONY: black_check
black_check:
	if python -c 'import sys ; sys.exit(1 if sys.version.startswith("2.") else 0)' ; then black --check testslide/ tests/ ; fi

.PHONY: unittest_tests
unittest_tests:
	# We need to convert file names to module names for Python 2...
	python -m unittest --verbose $(for f in tests/*_unittest.py ; do echo ${f%.py} | tr / .  ; done)

.PHONY: testslide_tests
testslide_tests:
	python -m testslide.cli tests/*_testslide.py

.PHONY: test
test: black_check unittest_tests testslide_tests
SHELL := /usr/bin/env -S bash -O globstar # makes work globs like **/*.py
.DEFAULT_GOAL := dummy

normalize:
	mp3gain -r -k **/*.mp3

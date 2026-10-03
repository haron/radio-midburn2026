SHELL := /usr/bin/env -S bash -O globstar # makes work globs like **/*.py
.DEFAULT_GOAL := deploy
.DELETE_ON_ERROR: # a failed recipe (e.g. `scale.py > radio.svg`) leaves no half-written target

# always runs; the prerequisites get rebuilt first if they are generated (radio.svg, SONGS.md)
linter: $(wildcard *.py *.md) radio.svg
	uvx ruff check .
	markdownlint-cli2 '*.md'
	xmllint --noout radio.svg
	jq empty **/*.json .*.json

normalize:
	mp3gain -r -k **/*.mp3

local: static.flac
	uv run radio.py

# Seamless loop: cut the clip's silent edges (0-0.85s, 17.35s-end), equal-power crossfade its tail into its head,
# +9dB to match the old clip's loudness. FLAC, since MP3 re-adds encoder padding at the loop point.
static.flac: static.mp3
	ffmpeg -v error -y -i $< -filter_complex "\
		[0]atrim=0.85:17.35,asetpts=PTS-STARTPTS,volume=9dB,asplit=3[a][b][c];\
		[a]atrim=0.3:16.2,asetpts=PTS-STARTPTS[body];\
		[b]atrim=16.2:16.5,asetpts=PTS-STARTPTS,afade=t=out:d=0.3:curve=qsin[tail];\
		[c]atrim=0:0.3,asetpts=PTS-STARTPTS,afade=t=in:d=0.3:curve=qsin[head];\
		[tail][head]amix=inputs=2:normalize=0[mix];\
		[body][mix]concat=n=2:v=0:a=1" $@

SONGS.md: songs.json
	jq -r '"# Songs\n", (to_entries[] | "## \(.key)\n", (.value | to_entries[]\
		| (.key | sub("^[0-9]+_"; "")) as $$e | "* \($$e[:1] | ascii_upcase)\($$e[1:])",\
		(.value[] | "  - \(.artist) — \(.title) (\(.year))")), "")' $< > $@
	format-md -i $@

# dial scale from the music folders; the PNG is a preview
radio.svg: scale.py music
	uv run scale.py > $@

# text as curves for cutting/printing; the PNG preview is rendered from it, so it shows exactly what gets cut
radio-curves.svg: radio.svg
	rsvg-convert -f svg $< -o $@

radio.png: radio-curves.svg
	rsvg-convert $< -o $@

# for the print shop: vector, 60x20 cm trim plus 3 mm bleed (the SVG's black background already reaches past the edge)
radio.pdf: radio-curves.svg
	rsvg-convert -f pdf -w 60cm -h 20cm --page-width 606mm --page-height 206mm --left 3mm --top 3mm $< -o $@

clean:
	ssh pinky.tailab2d8.ts.net rm -rf /opt/radio

provision:
	./provision.sh pinky.tailab2d8.ts.net

upload: static.flac SONGS.md radio.png linter
	dsstore-delete
	rsync -F .rsync-filter --delete -av . pinky.tailab2d8.ts.net:/opt/radio

deploy: upload
	ssh pinky.tailab2d8.ts.net 'uv --directory /opt/radio sync && systemctl daemon-reload && systemctl restart radio'

# runs in the foreground with the keyboard; `make deploy` brings the service back
remote: upload
	ssh -t pinky.tailab2d8.ts.net 'systemctl stop radio; uv --directory /opt/radio run radio.py'

logs:
	ssh pinky.tailab2d8.ts.net journalctl -n100 -f -u radio

restart:
	ssh pinky.tailab2d8.ts.net systemctl restart radio

stop:
	ssh pinky.tailab2d8.ts.net systemctl stop radio

download: songs.json linter
	uv run download.py

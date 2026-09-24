SHELL := /usr/bin/env -S bash -O globstar # makes work globs like **/*.py
.DEFAULT_GOAL := dummy

normalize:
	mp3gain -r -k **/*.mp3

local: static.flac
	uv run radio.py

# Seamless loop: cut the clip's silent edges (0-0.1s, 4.48s-end), equal-power crossfade its tail into its head.
# FLAC, since MP3 re-adds encoder padding at the loop point.
static.flac: static.mp3
	ffmpeg -v error -y -i $< -filter_complex "\
		[0]atrim=0.10:4.48,asetpts=PTS-STARTPTS,asplit=3[a][b][c];\
		[a]atrim=0.3:4.08,asetpts=PTS-STARTPTS[body];\
		[b]atrim=4.08:4.38,asetpts=PTS-STARTPTS,afade=t=out:d=0.3:curve=qsin[tail];\
		[c]atrim=0:0.3,asetpts=PTS-STARTPTS,afade=t=in:d=0.3:curve=qsin[head];\
		[tail][head]amix=inputs=2:normalize=0[mix];\
		[body][mix]concat=n=2:v=0:a=1" $@

clean:
	ssh pinky.tailab2d8.ts.net rm -rf /opt/radio

provision: upload
	./provision.sh pinky.tailab2d8.ts.net

upload: static.flac
	rsync -F .rsync-filter  -av . pinky.tailab2d8.ts.net:/opt/radio

deploy: upload
	ssh pinky.tailab2d8.ts.net 'uv --directory /opt/radio sync && systemctl daemon-reload && systemctl restart radio'

# runs in the foreground with the keyboard; `make deploy` brings the service back
remote: upload
	ssh -t pinky.tailab2d8.ts.net 'systemctl stop radio; uv --directory /opt/radio run radio.py'

log:
	ssh pinky.tailab2d8.ts.net journalctl -n100 -f -u radio

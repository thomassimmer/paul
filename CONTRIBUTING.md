# Contributing

Issues and pull requests are welcome. Please keep the code small and readable, add a
test for new behavior, leave the gates green (see the README's
[Without Docker](README.md#without-docker-development) section), and never include
real personal data in examples or bug reports (use the fictional profile in
`app/examples/`).

How it was built, and where the name comes from, are in [`docs/about.md`](docs/about.md).

## A demo workspace

A throwaway workspace for trying things out, taking screenshots or recording a
demo. Both scripts refuse to touch a directory they did not create:

```bash
python scripts/seed_demo.py --data-dir ./demo-data --model deepseek/deepseek-flash
python scripts/anonymize_template.py --data-dir ./demo-data --report
```

`seed_demo.py` fills the directory with the fictional profile, five made-up offers,
their verdicts and one prepared application folder. It goes through the app's own
stores, so the scores, the rendered DOCX and the keyword coverage come from the real
code; only the two model steps — analyzing an offer, writing the documents — are
fixtures. Pass the model you will actually use: a stored score carries a fingerprint
of its inputs, the model included, and typing a different one later would flag every
offer as out of date.

The screenshots in the README were taken from this workspace.

`anonymize_template.py` rewrites an imported `.docx` template with the fictional
text while keeping its layout, so a demo can show a real template without showing a
real CV. `--report` prints what would change and writes nothing.

Then run the app on it, on another port so your own workspace can stay open. The
image is the one built in the README's [Quick start](README.md#quick-start):

```bash
docker run --rm -p 127.0.0.1:8001:8000 -v "$PWD/demo-data:/app/data" -e PAUL_DATA_DIR=/app/data paul

# or from the source tree, with LibreOffice installed for the PDF preview
PAUL_DATA_DIR=./demo-data uvicorn app.main:app --port 8001
```

It serves on <http://localhost:8001>. The profile there is Camille Moreau: if you
see your own name, or your own offers on the board, you are looking at your real
workspace rather than at the demo.

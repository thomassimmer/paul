# Third-party code shipped by Paul

Paul has no front-end build step, so the one library the interface needs is
vendored as a file rather than installed. Vendoring it means redistributing it,
and its licence has to travel with it.

## htmx 2.0.4

- File: `htmx.min.js`, in this directory.
- Upstream: <https://htmx.org>, <https://github.com/bigskysoftware/htmx>.
- Version: 2.0.4, the one the vendored file reports in `htmx.version`.
- Licence: Zero-Clause BSD (0BSD), reproduced below.

To update it, replace `htmx.min.js` with the file from the release, check
`htmx.version` in the new file, and update the version above. Nothing else in the
repository depends on it beyond the `hx-*` attributes in the templates.

```
Zero-Clause BSD
=============

Permission to use, copy, modify, and/or distribute this software for
any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED “AS IS” AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE
FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY
DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

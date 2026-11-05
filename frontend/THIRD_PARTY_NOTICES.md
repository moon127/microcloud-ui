# Third-party notices

microcloud-ui is MIT-licensed (see `../LICENSE`). The frontend bundles these
libraries, each under its own licence:

| Library | Version | Licence | Notes |
|---|---|---|---|
| React, React DOM | 19.x | MIT | |
| React Router | 7.18.4 | MIT | |
| uPlot | 1.6.32 | MIT | charts |
| yaml | 2.9.1 | ISC | YAML editor |
| xterm.js (`@xterm/xterm`, `@xterm/addon-fit`) | 6.0.0 / 0.11.0 | MIT | terminal and text console |
| spice-html5 (`@spice-project/spice-html5`) | 0.2.1 | **LGPL-3.0-or-later** | VM graphical console. Used **unmodified** and loaded as a separate chunk (`VgaView-*.js`) only when a graphical console is opened (D24). Source: https://gitlab.freedesktop.org/spice/spice-html5 ; licence text: `node_modules/@spice-project/spice-html5/COPYING.LESSER`. You may replace this module with a modified version by rebuilding the frontend. |

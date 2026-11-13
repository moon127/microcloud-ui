# Security policy

microcloud-ui talks to LXD with an admin certificate, so a flaw in it can matter more than
its size suggests. Please report anything you think is a security problem privately.

## Reporting

Use **Report a vulnerability** on this repository's
[Security tab](https://github.com/moon127/microcloud-ui/security/advisories/new). It reaches the
maintainer only. Please don't open a public issue for it.

Include what you found, how to reproduce it, and the version (`mcui --version`, or the
image tag). This is a personal project, so expect a reply within a week or two rather than
hours.

## Supported versions

Only the latest release gets fixes. Fixes ship as a new patch release, built with current
dependencies (docs/RELEASING.md, "Security updates").

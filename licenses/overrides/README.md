# Researched licences

A package with a poor manifest — no licence file, or no licence declared —
cannot simply be skipped: the wheel and the executable still redistribute its
code, and BSD, MIT and Apache-2.0 all ask for the licence text to travel with
the distribution.

So the build fails on such a package, and the gap is closed here, by going to
the project's own source and reading its licence.

One folder per distribution, named as the distribution is:

```
licenses/overrides/<name>/LICENSE.txt   the text, verbatim, as published
licenses/overrides/<name>/source.json   {"license": "MIT",
                                         "source_url": "https://…/LICENSE",
                                         "retrieved": "2026-08-23"}
```

`source_url` is required. An override is a claim about someone else's licence,
and unsourced it is indistinguishable from a guess — the URL and the date are
what make it checkable, and they save the next person the same reading.

This directory is empty when every dependency carries its own licence file,
which is the state to prefer. It exists because that state is not guaranteed.

"""`crawl_main` -- resolves what to capture from a URL (the
command-line equivalent of a URL dropped into the console) or from
`--component`, runs the crawl against an injected client/archive, prints the
report, and signals nonzero on any failure/warning.

The URLs here are the same Atom shapes `parse_wiki_url` reads in the console,
pointed at the in-process fake server, so these exercise the real resolution
path rather than a stand-in for it.
"""

from connections_export.archive.store import Archive
from connections_export.cli import crawl_main
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client


def test_crawl_main_runs_a_clean_crawl_and_exits_zero(tmp_path, capsys):
    wikiset = synthesize(SynthSeed(seed=40, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["https://fake/wikis/basic/api/wiki/wiki0/feed"],
        env={},
        client=make_client(app),
        archive=archive,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "run" in out.lower()
    assert len(archive.seen_urls()) > 0


def test_crawl_main_exits_nonzero_on_failure(tmp_path, capsys):
    wikiset = synthesize(SynthSeed(seed=41, wiki_count=1, depth=1, pages_per_level=1))
    # A URL-scoped crawl skips the wikis LIST feed entirely -- that is what
    # naming a wiki means -- so fault a request it actually makes.
    faulted_path = "/wikis/basic/api/wiki/wiki0/nav/feed"
    app = make_app(wikiset, faults=Faults(status_for={faulted_path: 500}))
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["https://fake/wikis/basic/api/wiki/wiki0/feed"],
        env={},
        client=make_client(app, max_attempts=1),
        archive=archive,
    )

    assert exit_code != 0


def test_crawl_main_builds_config_from_argv_and_env(tmp_path, capsys):
    wikiset = synthesize(SynthSeed(seed=42, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["--component", "wiki:wiki0"],
        env={"CONNECTIONS_EXPORT_BASE_URL": "https://fake"},
        client=make_client(app),
        archive=archive,
    )

    assert exit_code == 0


def test_crawl_main_refuses_when_nothing_names_a_target(capsys):
    """No URL and no --component names no target. Reading that as "crawl
    every wiki in the deployment" is not a thing anyone wants: it is
    unbounded, and silent about being that broad."""
    exit_code = crawl_main([], env={"CONNECTIONS_EXPORT_BASE_URL": "https://fake"})

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "nothing to capture" in err
    assert "serve" in err  # points at the console, which is where you choose


def test_crawl_main_reports_an_unidentifiable_url(capsys):
    exit_code = crawl_main(["https://fake/not/a/known/shape"], env={})

    assert exit_code == 2
    assert "could not identify" in capsys.readouterr().err


def test_crawl_main_rejects_a_malformed_component(capsys):
    exit_code = crawl_main(["--component", "wiki"], env={})

    assert exit_code == 2
    assert "unrecognised --component" in capsys.readouterr().err


def test_a_url_supplies_the_base_url_so_no_config_is_needed(tmp_path):
    """The URL carries the deployment root, so `--base-url` is redundant when
    one is given -- the point of making the URL the unit of specification."""
    wikiset = synthesize(SynthSeed(seed=43, wiki_count=1, depth=1, pages_per_level=1))
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["https://fake/wikis/basic/api/wiki/wiki0/feed"],
        env={},  # no CONNECTIONS_EXPORT_BASE_URL anywhere
        client=make_client(make_app(wikiset)),
        archive=archive,
    )

    assert exit_code == 0
    assert any("/wiki/wiki0/" in url for url in archive.seen_urls())


def test_a_community_url_alone_expands_to_its_components(tmp_path, monkeypatch, capsys):
    """The point of lifting discovery out of the console: a community URL on
    the command line finds its own parts, instead of the caller having to know
    them and pass --component for each."""
    from connections_export import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_discover_community",
        lambda config, client, community_uuid: {
            "components": [
                {"kind": "wiki", "id": "wiki0", "title": "Handbook"},
                {"kind": "forum", "id": "f-1", "title": "General"},
            ],
            "community": "Engineering",
        },
    )
    wikiset = synthesize(SynthSeed(seed=44, wiki_count=1, depth=1, pages_per_level=1))
    archive = Archive.open(tmp_path / "archive")

    exit_code = crawl_main(
        ["https://fake/communities/service/html/communityview?communityUuid=c-1"],
        env={},
        client=make_client(make_app(wikiset)),
        archive=archive,
    )

    out = capsys.readouterr().out
    assert "community holds" in out
    assert "1 wiki" in out and "1 forum" in out
    assert exit_code in (0, 1)  # the fake server has no forums; the wiki is real
    assert any("/wiki/wiki0/" in url for url in archive.seen_urls())


def test_a_community_with_nothing_in_it_says_so(tmp_path, monkeypatch, capsys):
    from connections_export import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_discover_community",
        lambda config, client, community_uuid: {"components": [], "community": None},
    )

    exit_code = crawl_main(
        ["https://fake/communities/service/html/communityview?communityUuid=c-2"],
        env={},
        client=make_client(make_app(synthesize(SynthSeed(seed=45, wiki_count=1)))),
        archive=Archive.open(tmp_path / "archive"),
    )

    assert exit_code == 1
    assert "reports no wiki, blog or forum" in capsys.readouterr().err


def test_auth_failure_reads_as_instructions_not_a_stack_trace(monkeypatch, capsys):
    """Authentication is an ordinary thing to get wrong -- an optional package
    not installed, no domain to authenticate against, credentials unset -- and
    the console hands people commands to run, so this is reached by users
    rather than only by developers. A raw traceback is not an answer."""
    from connections_export import cli as cli_module
    from connections_export.http import AuthError

    def refuse(config, env):
        raise AuthError("SspiAuth requires the 'requests-negotiate-sspi' package.")

    monkeypatch.setattr(cli_module, "_build_default_client", refuse)

    exit_code = crawl_main(
        ["https://fake/wikis/basic/api/wiki/wiki0/feed"],
        env={},
    )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "requests-negotiate-sspi" in captured.err
    assert "Traceback" not in captured.err


def test_auth_error_is_still_a_runtime_error():
    """It subclasses RuntimeError deliberately: anything that already handled
    these keeps working, and narrowing the type was not meant to change what
    callers outside this repo can catch."""
    from connections_export.http import AuthError

    assert issubclass(AuthError, RuntimeError)

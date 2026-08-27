def test_every_container_in_the_model_is_reachable_generically():
    """`scope.py` and `traverse.py` each enumerated five fields by name. The
    registry knows the field names; the base makes what is inside them uniform
    enough to use."""
    from connections_export.derive.model import (
        DerivedBlog,
        DerivedForum,
        DerivedRichContent,
        DerivedWiki,
        Interchange,
    )
    from connections_export.derive.traverse import iter_containers

    interchange = Interchange(
        wikis=[DerivedWiki(id="w", label="w", title="Wiki")],
        blogs=[DerivedBlog(id="b")],
        forums=[DerivedForum(id="f")],
        rich_content=[DerivedRichContent(id="r")],
    )
    kinds = {spec.kind for spec, _ in iter_containers(interchange)}

    assert "wiki" in kinds and "rich_content" in kinds
    assert {container.id for _spec, container in iter_containers(interchange)} == {
        "w",
        "b",
        "f",
        "r",
    }


def test_a_blog_is_yielded_once_though_two_apps_share_the_field():
    """`blog` and `ideation_blog` are two apps over one `blogs` list."""
    from connections_export.derive.model import DerivedBlog, Interchange
    from connections_export.derive.traverse import iter_containers

    interchange = Interchange(blogs=[DerivedBlog(id="b", kind="ideation_blog")])

    assert len(list(iter_containers(interchange))) == 1

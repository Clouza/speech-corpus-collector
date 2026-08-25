from core.licenses import download_allowed, resolve_license


def test_known_license_properties() -> None:
    info = resolve_license("CC BY-SA 4.0")
    assert info.identifier == "CC-BY-SA-4.0"
    assert info.commercial_use_allowed is True
    assert info.attribution_required is True
    assert info.share_alike_required is True


def test_unknown_license_is_blocked_by_default() -> None:
    info = resolve_license(None)
    assert not download_allowed(info, False)
    assert download_allowed(info, True)


def test_non_reusable_license_is_always_blocked() -> None:
    info = resolve_license("CC-BY-NC-4.0")
    assert info.status == "prohibited"
    assert not download_allowed(info, True)

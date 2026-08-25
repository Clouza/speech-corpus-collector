from collectors.inesco import parse_inesco_filename


def test_inesco_filename_normalization() -> None:
    assert parse_inesco_filename("fcim_h001.wav") == ("fcim", 1, "happiness")
    assert parse_inesco_filename("mbaz_a212.wav") == ("mbaz", 212, "anger")
    assert parse_inesco_filename("mdpa_s600.wav") == ("mdpa", 600, "sadness")

from worker.adapters.vision import gcp_likelihood_score


def test_gcp_likelihood_score_int_and_name():
    assert gcp_likelihood_score(0) == 0.0
    assert gcp_likelihood_score(5) == 0.95
    assert gcp_likelihood_score("VERY_LIKELY") == 0.95
    assert gcp_likelihood_score("Likelihood.POSSIBLE") == 0.45
    assert gcp_likelihood_score(None) == 0.0


class _Enumish:
    def __init__(self, value: int, name: str):
        self.value = value
        self.name = name


def test_gcp_likelihood_score_enum_like():
    assert gcp_likelihood_score(_Enumish(4, "LIKELY")) == 0.75

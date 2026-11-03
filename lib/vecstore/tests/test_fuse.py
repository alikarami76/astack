from vecstore.fuse import rrf


def test_rrf_rewards_agreement_between_rankers():
    assert rrf([["a", "b", "c"], ["c", "a", "d"]]) == ["a", "c", "b", "d"]


def test_rrf_keeps_first_ranking_order_on_ties():
    # a and x both appear once at rank 0; the keyword ranking comes first, so its id wins the tie
    assert rrf([["a"], ["x"]]) == ["a", "x"]


def test_rrf_handles_empty_rankings():
    assert rrf([[], []]) == []
    assert rrf([["a"], []]) == ["a"]

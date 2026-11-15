from decimal import Decimal

from app.domain.contributors import (
    ItemContribution,
    top_contributors,
)
from app.domain.item_calc import ItemNutrient
from app.domain.quantity import Unit
from app.domain.swaps import (
    SwapCandidate,
    find_swaps,
    normalize_name,
)
from app.domain.values import NutrientValue, Qualifier, Range, Source

D = Decimal


def _known(lower, upper):
    return ItemNutrient(True, Range(D(lower), D(upper) if upper is not None else None), Qualifier.NONE)


def _unknown():
    return ItemNutrient(False, None, Qualifier.NONE)


def test_unknown_contributor_listed_not_summed():
    items = [
        ItemContribution(1, "Apple", _known("10", "10")),
        ItemContribution(2, "Mystery", _unknown()),
    ]
    contributors, unknown_for = top_contributors(items)
    assert unknown_for == ["Mystery"]
    assert [c.product_id for c in contributors] == [1]


def test_share_is_range():
    items = [
        ItemContribution(1, "Apple", _known("10", "20")),
        ItemContribution(2, "Pear", _known("5", "5")),
    ]
    contributors, _ = top_contributors(items)
    total_lower = D("15")
    total_upper = D("25")
    by_id = {c.product_id: c for c in contributors}
    apple = by_id[1]
    assert apple.lower == D("10")
    assert apple.upper == D("20")
    expected_lower_share = (D("10") / total_upper).quantize(D("0.01"))
    expected_upper_share = (D("20") / total_lower).quantize(D("0.01"))
    assert apple.share == (expected_lower_share, expected_upper_share)


def test_trace_total_gives_open_share():
    # total_upper is None (because some item's upper is None) -> share lower bound is 0
    items = [
        ItemContribution(1, "Apple", _known("10", None)),
        ItemContribution(2, "Pear", _known("5", "5")),
    ]
    contributors, _ = top_contributors(items)
    by_id = {c.product_id: c for c in contributors}
    apple = by_id[1]
    assert apple.share[0] == D("0")

    # total_lower is 0 -> share upper bound is None
    items2 = [
        ItemContribution(3, "Zero", _known("0", "0")),
    ]
    contributors2, _ = top_contributors(items2)
    zero = contributors2[0]
    assert zero.share[1] is None


def test_swap_requires_same_unit():
    target = SwapCandidate(1, "Coca-Cola", "Coca-Cola", Unit.ML, NutrientValue.point(D("11"), Source.LABEL))
    candidate = SwapCandidate(
        2, "Coca-Cola Zero", "Coca-Cola", Unit.G, NutrientValue.point(D("0"), Source.LABEL)
    )
    assert find_swaps(target, [candidate]) == []


def test_swap_requires_known_finite_values():
    target = SwapCandidate(1, "Coca-Cola", "Coca-Cola", Unit.ML, NutrientValue.point(D("11"), Source.LABEL))
    unknown_candidate = SwapCandidate(
        2, "Coca-Cola Zero", "Coca-Cola", Unit.ML, NutrientValue.unknown(Source.LABEL)
    )
    assert find_swaps(target, [unknown_candidate]) == []

    unbounded_candidate = SwapCandidate(
        3, "Coca-Cola Zero", "Coca-Cola", Unit.ML, NutrientValue(True, Range(D("0"), None), Qualifier.TRACE, Source.LABEL)
    )
    assert find_swaps(target, [unbounded_candidate]) == []


def test_swap_requires_30pct_no_overlap():
    target = SwapCandidate(1, "Coca-Cola", "Coca-Cola", Unit.ML, NutrientValue.point(D("11"), Source.LABEL))
    qualifies = SwapCandidate(
        2, "Coca-Cola Zero", "Coca-Cola", Unit.ML, NutrientValue.point(D("7.7"), Source.LABEL)
    )
    does_not = SwapCandidate(
        3, "Coca-Cola Zero", "Coca-Cola", Unit.ML, NutrientValue.point(D("7.8"), Source.LABEL)
    )
    result = find_swaps(target, [qualifies, does_not])
    assert [c.product_id for c in result] == [2]


def test_variant_word_matching():
    target1 = SwapCandidate(
        1, "Red Bull Peach Edition", "Red Bull", Unit.ML, NutrientValue.point(D("11"), Source.LABEL)
    )
    candidate1 = SwapCandidate(
        2, "Red Bull Peach Edition Sugarfree", "Red Bull", Unit.ML, NutrientValue.point(D("0"), Source.LABEL)
    )
    assert [c.product_id for c in find_swaps(target1, [candidate1])] == [2]

    target2 = SwapCandidate(
        3, "Coca-Cola", "Coca-Cola", Unit.ML, NutrientValue.point(D("11"), Source.LABEL)
    )
    candidate2 = SwapCandidate(
        4, "Coca-Cola Zéro", "Coca-Cola", Unit.ML, NutrientValue.point(D("0"), Source.LABEL)
    )
    assert [c.product_id for c in find_swaps(target2, [candidate2])] == [4]


def test_different_brand_not_swap():
    target = SwapCandidate(1, "Coca-Cola", "Coca-Cola", Unit.ML, NutrientValue.point(D("11"), Source.LABEL))
    candidate = SwapCandidate(
        2, "Coca-Cola Zero", "Pepsi", Unit.ML, NutrientValue.point(D("0"), Source.LABEL)
    )
    assert find_swaps(target, [candidate]) == []


def test_normalize_name_strips_variant_words_and_accents():
    assert normalize_name("Coca-Cola Zéro") == normalize_name("Coca-Cola")


def test_variant_word_matching_is_token_aware():
    # "zero" and "light" glued to other letters are not variant words.
    assert normalize_name("Zerotti") == "zerotti"
    assert normalize_name("Lightning bar") == "lightning bar"
    assert normalize_name("Coca-Cola Zéro") == "coca-cola"
    assert normalize_name("Yaourt 0% sucres") == normalize_name("Yaourt")
    assert normalize_name("Yaourt sans sucres") == normalize_name("Yaourt")


def test_top_contributors_tie_break_is_deterministic():
    items = [
        ItemContribution(2, "Banana", _known("10", "10")),
        ItemContribution(1, "Apple", _known("10", "10")),
        ItemContribution(3, "Apple", _known("10", "10")),
    ]
    contributors, _ = top_contributors(items)
    assert [c.product_id for c in contributors] == [1, 3, 2]

from ember.facts import FACTS, pick_fact


class TestFactContent:
    def test_there_are_enough_to_feel_varied(self):
        assert len(FACTS) >= 20

    def test_all_are_non_empty_trimmed_strings(self):
        for fact in FACTS:
            assert isinstance(fact, str)
            assert fact == fact.strip()
            assert fact

    def test_none_are_duplicated(self):
        assert len(set(FACTS)) == len(FACTS)

    def test_each_fits_the_strip(self):
        # The card wraps at ~42 chars over a couple of lines; much beyond
        # this and it starts pushing the layout around.
        assert max(len(f) for f in FACTS) <= 120


class TestPickFact:
    def test_returns_a_matching_index_and_text(self):
        index, text = pick_fact()
        assert FACTS[index] == text

    def test_never_repeats_the_previous_fact(self):
        last = 0
        for _ in range(300):
            index, _text = pick_fact(last)
            assert index != last
            last = index

    def test_no_previous_fact_is_allowed(self):
        index, text = pick_fact(-1)
        assert 0 <= index < len(FACTS)
        assert FACTS[index] == text

    def test_out_of_range_previous_index_is_harmless(self):
        index, text = pick_fact(9999)
        assert FACTS[index] == text

    def test_spreads_across_the_catalogue(self):
        seen = {pick_fact(-1)[0] for _ in range(400)}
        assert len(seen) > len(FACTS) / 2

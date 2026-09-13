from omapuffco.product_info import get_product_info, is_proxy


class TestLookup:
    def test_by_product_code(self):
        info = get_product_info(product_code=71)
        assert info is not None
        assert info.marketing_name == "Onyx"
        assert info.type == "peach"

    def test_by_model_code_when_product_code_is_unknown(self):
        assert get_product_info(product_code=9999, model_code=17).marketing_name == "Flourish"

    def test_product_code_takes_precedence(self):
        assert get_product_info(product_code=72, model_code=17).marketing_name == "Pearl"

    def test_unknown_codes_return_none(self):
        assert get_product_info(product_code=9999) is None
        assert get_product_info() is None

    def test_to_dict_carries_a_display_label(self):
        data = get_product_info(product_code=71).to_dict()
        assert data["label"] == "Peak Pro Onyx"
        assert isinstance(data["model_codes"], list)


class TestIsProxy:
    def test_detects_proxy_and_pivot_by_name(self):
        assert is_proxy({"device_name": "Puffco Proxy"})
        assert is_proxy({"name": "PIVOT"})
        assert is_proxy({"label": "Peak Pro Pivot"})

    def test_peak_pro_is_not_rejected(self):
        assert not is_proxy({"label": "Peak Pro Onyx", "type": "peach"})

    def test_missing_info_is_not_a_proxy(self):
        assert not is_proxy(None)
        assert not is_proxy({})

    def test_dataclass_input_is_not_treated_as_proxy(self):
        assert not is_proxy(get_product_info(product_code=71))

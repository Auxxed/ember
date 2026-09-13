from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ProductInfo:
    type: str
    product_code: int
    model_codes: tuple[int, ...]
    marketing_name: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["model_codes"] = list(self.model_codes)
        data["label"] = f"Peak Pro {self.marketing_name}"
        return data


_PRODUCT_INFOS: tuple[ProductInfo, ...] = (
    ProductInfo("pikachu", 21, (0, 21, 0xFFFFFFFF), "OG"),
    ProductInfo("pikachu", 22, (1, 22), "Opal"),
    ProductInfo("pikachu", 25, (2,), "Indiglow"),
    ProductInfo("pikachu", 26, (4,), "Guardian"),
    ProductInfo("raichu", 51, (0, 0xFFFFFFFF), "OG"),
    ProductInfo("peach", 71, (13,), "Onyx"),
    ProductInfo("peach", 72, (12,), "Pearl"),
    ProductInfo("peach", 74, (13, 15), "Desert"),
    ProductInfo("peach", 75, (17,), "Flourish"),
    ProductInfo("peach", 78, (19,), "Storm"),
    ProductInfo("peach", 79, (13,), "Onyx"),
    ProductInfo("peach", 80, (12,), "Pearl"),
    ProductInfo("peach", 81, (23,), "Daybreak"),
    ProductInfo("peach", 83, (25,), "Plasma"),
    ProductInfo("peach", 84, (26,), "Glacier"),
)

_BY_PRODUCT_CODE = {p.product_code: p for p in _PRODUCT_INFOS}
_BY_MODEL_CODE: dict[int, ProductInfo] = {}
for _p in _PRODUCT_INFOS:
    for _mc in _p.model_codes:
        _BY_MODEL_CODE.setdefault(_mc, _p)


# Puffco's internal families: pikachu/raichu/peach are Peak Pro colorways.
# Proxy and Pivot are rejected by advertised name, not by this table.
PEAK_PRO_TYPES = {"pikachu", "raichu", "peach"}
PROXY_TYPES = set()


def get_product_info(
    *,
    product_code: int | None = None,
    model_code: int | None = None,
) -> ProductInfo | None:
    if product_code is not None:
        info = _BY_PRODUCT_CODE.get(product_code)
        if info:
            return info
    if model_code is not None:
        return _BY_MODEL_CODE.get(model_code)
    return None


def is_proxy(info: ProductInfo | dict | None) -> bool:
    if info is None:
        return False
    if isinstance(info, dict):
        blob = " ".join(
            str(info.get(k) or "")
            for k in ("type", "label", "marketing_name", "device_name", "name")
        ).lower()
        return "proxy" in blob or "pivot" in blob
    return False

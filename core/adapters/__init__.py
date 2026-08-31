"""어댑터 레지스트리. 기관 추가 시 여기에 한 줄만 등록하면 된다."""
from __future__ import annotations

from typing import Dict, Type

from core.adapters.base import BaseAdapter
from core.adapters.hana import HanaAdapter
from core.adapters.kb import KbAdapter
from core.adapters.woorifg import WooriFgAdapter

REGISTRY: Dict[str, Type[BaseAdapter]] = {
    KbAdapter.adapter_id: KbAdapter,
    WooriFgAdapter.adapter_id: WooriFgAdapter,
    HanaAdapter.adapter_id: HanaAdapter,
}


def get_adapter(spec, fetcher) -> BaseAdapter:
    cls = REGISTRY.get(spec.adapter)
    if cls is None:
        raise KeyError(
            f"'{spec.adapter}' 어댑터를 찾을 수 없습니다. "
            f"사용 가능: {', '.join(sorted(REGISTRY))}"
        )
    return cls(spec, fetcher)


__all__ = ["BaseAdapter", "REGISTRY", "get_adapter"]

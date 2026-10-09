"""Keep numeric SQL results from being mistaken for personal identifiers."""

from functools import wraps
from numbers import Number

from springbootai.orm.pymybatis.security.sensitive_data_masker import SensitiveDataMasker


def install_numeric_masking_guard() -> None:
    """Preserve numbers during automatic detection, retaining explicit masks.

    PyMyBatis 2.3.7 converts numbers to strings before guessing sensitive data
    types. A total such as 16374444681 then becomes a masked phone number.
    Explicit phone/bank-card field masks still run independently of detection.
    """
    original = SensitiveDataMasker.detect_type
    if getattr(original, "_preserves_numeric_results", False):
        return

    @wraps(original)
    def detect_type(self, value):
        if isinstance(value, Number):
            return None
        return original(self, value)

    detect_type._preserves_numeric_results = True
    SensitiveDataMasker.detect_type = detect_type

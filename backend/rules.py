"""桥梁微应变判定与温度补偿。

判定带：80～220 με 为合格，否则越界。
温度补偿：补偿后读数 = 原始读数 + 补偿系数 × (现场气温 − 基准气温)。
补偿系数允许范围 0～1（含端点）。判定一律使用补偿后读数。
"""

import math

LOWER_BAND = 80.0
UPPER_BAND = 220.0

COEFF_MIN = 0.0
COEFF_MAX = 1.0

TEMP_MIN = -50.0
TEMP_MAX = 100.0

# 与接口层共用的错误说法，保证网页投递与绕过网页的请求说法对齐。
MSG_TEMP_REQUIRED = "现场气温不能为空"
MSG_TEMP_INVALID = "现场气温必须是数字"
MSG_TEMP_OUT_OF_RANGE = f"现场气温必须在 {TEMP_MIN:g}～{TEMP_MAX:g}℃ 之间"
MSG_COEFF_REQUIRED = "补偿系数不能为空"
MSG_COEFF_INVALID = "补偿系数必须是数字"
MSG_COEFF_OUT_OF_RANGE = (
    f"补偿系数必须在 {COEFF_MIN:g}～{COEFF_MAX:g} 之间（含端点）"
)
MSG_BASE_TEMP_REQUIRED = "基准气温不能为空"
MSG_BASE_TEMP_INVALID = "基准气温必须是数字"
MSG_BASE_TEMP_OUT_OF_RANGE = (
    f"基准气温必须在 {TEMP_MIN:g}～{TEMP_MAX:g}℃ 之间"
)
MSG_SPAN_NOT_CONFIGURED = "该跨段尚未设置温度补偿，请先在温度补偿专页设置系数与基准气温"


def finite_number(value) -> float | None:
    """把入参转成有限浮点数；None/空串/NaN/Inf/非数字一律返回 None。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def judge_microstrain(microstrain: float) -> tuple[str, str]:
    if LOWER_BAND <= microstrain <= UPPER_BAND:
        return "合格", (
            f"补偿后读数 {microstrain:g} με 处于 "
            f"{LOWER_BAND:g}～{UPPER_BAND:g} με 设计允许范围内"
        )
    if microstrain < LOWER_BAND:
        return "越界", (
            f"补偿后读数 {microstrain:g} με 低于 {LOWER_BAND:g} με 设计下限"
        )
    return "越界", (
        f"补偿后读数 {microstrain:g} με 高于 {UPPER_BAND:g} με 设计上限"
    )


def validate_coefficient(value) -> tuple[float | None, str | None]:
    coefficient = finite_number(value)
    if coefficient is None:
        # 缺与不是数字两种说法分开，便于定位。
        if value is None or value == "":
            return None, MSG_COEFF_REQUIRED
        return None, MSG_COEFF_INVALID
    if not (COEFF_MIN <= coefficient <= COEFF_MAX):
        return None, MSG_COEFF_OUT_OF_RANGE
    return coefficient, None


def validate_temperature(
    value,
    *,
    required_msg=MSG_TEMP_REQUIRED,
    invalid_msg=MSG_TEMP_INVALID,
    range_msg=MSG_TEMP_OUT_OF_RANGE,
) -> tuple[float | None, str | None]:
    temperature = finite_number(value)
    if temperature is None:
        if value is None or value == "":
            return None, required_msg
        return None, invalid_msg
    if not (TEMP_MIN <= temperature <= TEMP_MAX):
        return None, range_msg
    return temperature, None


def compensate_reading(
    raw: float, coefficient: float, field_temperature: float, base_temperature: float
) -> float:
    """补偿后读数 = 原文 + 系数 ×（现场气温 − 基准气温），保留 6 位小数。"""
    return round(raw + coefficient * (field_temperature - base_temperature), 6)

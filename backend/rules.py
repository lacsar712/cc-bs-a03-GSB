"""桥梁微应变判定与温度补偿。

判定一律使用补偿后读数：
    补偿后读数 = 原始读数 + 补偿系数 × (现场气温 − 基准气温)

补偿后读数处于 80～220 με 为合格，否则越界。
"""

# 补偿系数允许范围（含端点）。超出此区间的系数设置与报送一律退回。
COEFF_MIN = 0.0
COEFF_MAX = 0.5

# 气温合理区间（℃），仅用于挡掉明显荒唐值；缺气温直接退回。
TEMP_MIN = -50.0
TEMP_MAX = 80.0


def compensate(microstrain: float, coeff: float, site_temp: float,
               base_temp: float) -> float:
    """温度补偿：补偿后读数 = 原文 + 系数 × (现场气温 − 基准气温)。

    系数 0.1、气温抬高 10℃ 时，补偿后读数比原文多 1。
    """
    return microstrain + coeff * (site_temp - base_temp)


def judge_microstrain(microstrain: float) -> tuple[str, str]:
    if 80 <= microstrain <= 220:
        return "合格", "微应变处于 80～220 με 设计允许范围内"
    if microstrain < 80:
        return "越界", "微应变低于 80 με 设计下限"
    return "越界", "微应变高于 220 με 设计上限"


def judge_compensated(compensated: float) -> tuple[str, str]:
    verdict, base_reason = judge_microstrain(compensated)
    if verdict == "合格":
        return verdict, f"温度补偿后读数 {_fmt(compensated)} με，{base_reason}"
    return verdict, f"温度补偿后读数 {_fmt(compensated)} με，{base_reason}"


def validate_coeff(coeff) -> float | None:
    """校验补偿系数；越界或非数字返回 None。"""
    try:
        value = float(coeff)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    if not (COEFF_MIN <= value <= COEFF_MAX):
        return None
    return value


def validate_temp(temp) -> float | None:
    """校验气温；缺失或非数字返回 None。"""
    if temp is None or temp == "":
        return None
    try:
        value = float(temp)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    if not (TEMP_MIN <= value <= TEMP_MAX):
        return None
    return value


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")

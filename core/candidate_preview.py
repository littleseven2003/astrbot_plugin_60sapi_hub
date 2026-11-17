"""按任务候选顺序寻找同一站点满足全部栏目与条数的预览。

投递与聊天查询共用这一判定：只有单个站点同时满足组合的全部栏目和条数，
才算可用；不满足就换下一个候选，绝不拼接多个站点的内容。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from .site_requirements import Requirement, evaluate_sections

# 单个候选站点的尝试最多占用剩余投递时间的这一份，其余留给后续候选与发送。
PREVIEW_BUDGET_SHARE = 0.5
MIN_PREVIEW_ATTEMPT_SECONDS = 1.0


def attempt_timeout_for(
    deadline: float | None,
    now: float | None = None,
    *,
    share: float = PREVIEW_BUDGET_SHARE,
) -> float | None:
    """按投递截止时间算出这一次预览尝试的时长上限。

    每次只看“还剩多少时间”，所以第一名候选超时后，后面的候选自然
    拿到更大的份额；上限始终不超过剩余时间，余量留给后续候选与发送。
    剩余时间不足两个最小尝试时长时不再保留下限，避免一次尝试就吃光预算。
    """
    if deadline is None:
        return None
    if now is None:
        now = asyncio.get_running_loop().time()
    remaining = deadline - now
    if remaining <= 0:
        return 0.0
    floor = (
        MIN_PREVIEW_ATTEMPT_SECONDS
        if remaining >= 2 * MIN_PREVIEW_ATTEMPT_SECONDS
        else 0.0
    )
    return max(0.01, min(remaining, max(floor, remaining * share)))


async def satisfied_previews(
    previews: object,
    *,
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    attempt_timeout: float | None = None,
    deadline: float | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """按顺序产出满足全部要求的 (site_id, 预览数据)。

    catch_errors 为真时跳过单个站点抛出的异常（聊天查询不能让一次
    预览失败中断整条链路），为假时继续向上传播（投递保留原有语义）。
    attempt_timeout 限制单个候选站点的尝试时长：慢站点只占用自己的份额，
    超时视作该站不满足并继续下一个候选，不必等它把所有时间用完。
    给出 deadline 时按剩余时间逐次计算上限（投递用），否则用固定值。
    """
    for site_id in site_ids:
        payload: dict[str, Any] = {
            "profile_id": profile_id,
            "force_refresh": False,
            "site_id": site_id,
        }
        # 站点同时放进载荷与关键字参数：预览接口两处都接受，调用方无需区分。
        kwargs: dict[str, Any] = {"site_id": site_id}
        if owner is not None:
            kwargs["owner"] = owner
        timeout = (
            attempt_timeout if deadline is None else attempt_timeout_for(deadline)
        )
        try:
            if timeout is None:
                preview = await previews.preview_profile(payload, **kwargs)
            else:
                preview = await asyncio.wait_for(
                    previews.preview_profile(payload, **kwargs),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            # 超时与“该站不满足”同等对待：换下一个候选，而不是直接失败。
            continue
        except Exception:
            if not catch_errors:
                raise
            continue
        if getattr(preview, "status_code", None) != 200:
            continue
        body = getattr(preview, "body", None)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            continue
        sections = data.get("sections")
        if isinstance(sections, list):
            satisfied, _ = evaluate_sections(sections, requirements)
            if not satisfied:
                continue
        yield site_id, data


async def first_satisfying_preview(
    previews: object,
    *,
    profile_id: str,
    requirements: tuple[Requirement, ...],
    site_ids: Iterable[str],
    owner: str | None = None,
    catch_errors: bool = False,
    attempt_timeout: float | None = None,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """返回第一个满足全部要求的站点预览；全部不满足时返回 None。"""
    async for result in satisfied_previews(
        previews,
        profile_id=profile_id,
        requirements=requirements,
        site_ids=site_ids,
        owner=owner,
        catch_errors=catch_errors,
        attempt_timeout=attempt_timeout,
        deadline=deadline,
    ):
        return result
    return None


__all__ = [
    "PREVIEW_BUDGET_SHARE",
    "attempt_timeout_for",
    "first_satisfying_preview",
    "satisfied_previews",
]
